"""
PropertyReport - FastAPI Backend
Supports both Stripe TEST and LIVE modes via environment variables.

Run (development):
  uvicorn api:app --reload --port 8000

Run (production):
  uvicorn api:app --host 0.0.0.0 --port $PORT --workers 2
"""

import os
import uuid
import sqlite3
import asyncio
import json
import time
import tempfile
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager, contextmanager

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import re
import stripe
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("propertyreport")

# Suppress noisy internal loggers from WeasyPrint's font pipeline
for _logger in ("fontTools", "fontTools.ttLib", "fontTools.subset",
                "fontTools.otlLib", "fontTools.ttLib.tables",
                "weasyprint", "weasyprint.progress"):
    logging.getLogger(_logger).setLevel(logging.ERROR)


class _RedactApiKeysFilter(logging.Filter):
    """httpx logs the full request URL (e.g. 'HTTP Request: GET https://...?key=SECRET ...')
    at INFO level. Several calls we make pass API keys as a `key=` query param
    (Google Maps, Scrapfly-style services), so strip those before they hit any handler."""
    _pattern = re.compile(r"([?&]key=)[^&\s\"]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                self._pattern.sub(r"\1***REDACTED***", a) if isinstance(a, str) else a
                for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = self._pattern.sub(r"\1***REDACTED***", record.msg)
        return True


logging.getLogger("httpx").addFilter(_RedactApiKeysFilter())

from orchestrator import research_property, PropertyReport
from pdf_generator import generate_pdf
from email_sender import send_report_email
from suburb_db import init_suburb_db
from compare_suburbs import get_suburb_comparison, check_compare_rate_limit, CompareSuburbsResponse

# Phase 2B renderer — try import at module load so we know up-front if
# WeasyPrint is available. Falls back to ReportLab generate_pdf on any failure.
try:
    from weasy_generator import render_dashboard_pdf as _render_weasy_pdf  # type: ignore
    _WEASY_RENDERER = True
    log.info("WeasyPrint renderer available")
except Exception as _weasy_import_err:  # noqa: BLE001
    _WEASY_RENDERER = False
    _render_weasy_pdf = None
    log.warning("WeasyPrint renderer unavailable (will fall back to ReportLab): %s",
                _weasy_import_err)


def _render_pdf(report: PropertyReport, pdf_path: str) -> str:
    """Render PDF via WeasyPrint when available, fall back to ReportLab.
    Returns the renderer name actually used ('weasyprint' or 'reportlab')."""
    if _WEASY_RENDERER and _render_weasy_pdf is not None:
        try:
            _render_weasy_pdf(report, pdf_path)
            return "weasyprint"
        except Exception as e:  # noqa: BLE001
            log.warning("WeasyPrint render failed (%s), falling back to ReportLab", e)
    generate_pdf(report, pdf_path)
    return "reportlab"


# ─── Config ───────────────────────────────────────────────────────────────────

stripe.api_key         = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
REPORT_PRICE_AUD_CENTS = int(os.getenv("REPORT_PRICE_CENTS", "499"))
FRONTEND_URL           = os.getenv("FRONTEND_URL", "http://localhost:8000")
IS_TEST_MODE           = stripe.api_key.startswith("sk_test_")
ENV                    = os.getenv("ENV", "development")
DB_PATH                = os.getenv("JOB_DB_PATH", "jobs.db")
_DEV_TOKEN             = os.getenv("DEV_ENDPOINT_TOKEN", "")

# Address validation: printable ASCII, typical address characters only
_ADDRESS_RE = re.compile(r"^[\w\s,.\-'/]+$")

# ─── Rate Limiter (in-memory, per-IP) ────────────────────────────────────────

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT    = int(os.getenv("CHECKOUT_RATE_LIMIT", "3"))   # max requests
_RATE_WINDOW   = int(os.getenv("CHECKOUT_RATE_WINDOW", "600")) # per N seconds


def _check_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    _rate_buckets[ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[ip]) >= _RATE_LIMIT:
        return False
    _rate_buckets[ip].append(now)
    return True


# ─── SQLite Job Store ─────────────────────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id       TEXT PRIMARY KEY,
                status       TEXT NOT NULL,
                address      TEXT,
                message      TEXT,
                created_at   TEXT,
                completed_at TEXT
            )
        """)


def job_create(job_id: str, address: str, message: str = "Awaiting payment..."):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO jobs (job_id, status, address, message, created_at) VALUES (?,?,?,?,?)",
            (job_id, "pending", address, message, datetime.utcnow().isoformat())
        )


def job_update(job_id: str, status: str, message: str, completed_at: str = None):
    with get_db() as db:
        db.execute(
            "UPDATE jobs SET status=?, message=?, completed_at=? WHERE job_id=?",
            (status, message, completed_at, job_id)
        )


def job_get(job_id: str) -> Optional[dict]:
    with get_db() as db:
        row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None


# ─── Startup ──────────────────────────────────────────────────────────────────

def _ensure_sample_pdf() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    sample_path = os.path.join(here, "frontend", "sample_report.pdf")
    try:
        import runpy
        runpy.run_path(os.path.join(here, "generate_sample_pdf.py"))
        log.info("Sample PDF generated at %s", sample_path)
    except Exception as exc:
        log.warning("Could not generate sample PDF: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_sample_pdf()
    init_db()
    init_suburb_db()
    mode  = "TEST MODE" if IS_TEST_MODE else "LIVE MODE"
    price = f"${REPORT_PRICE_AUD_CENTS / 100:.2f} AUD"
    log.info("PropertyReport API | %s | Price: %s | ENV: %s | DB: %s", mode, price, ENV, DB_PATH)

    _required_prod = {
        "ANTHROPIC_API_KEY":    os.getenv("ANTHROPIC_API_KEY"),
        "STRIPE_SECRET_KEY":    stripe.api_key,
        "STRIPE_WEBHOOK_SECRET": STRIPE_WEBHOOK_SECRET,
        "SENDER_EMAIL":         os.getenv("SENDER_EMAIL"),
    }
    missing = [k for k, v in _required_prod.items() if not v]

    # Email delivery needs either SendGrid or SMTP credentials, not both.
    if ENV == "production" and not os.getenv("SENDGRID_API_KEY") and not os.getenv("SMTP_PASS"):
        missing.append("SENDGRID_API_KEY or SMTP_PASS")

    if ENV == "production" and missing:
        raise RuntimeError(f"FATAL: Missing required env vars for production: {', '.join(missing)}")

    for key in missing:
        log.warning("WARNING: %s not set", key)

    yield


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="PropertyReport API",
    description="AI-powered Australian property research reports",
    version="1.0.0",
    lifespan=lifespan,
)

# Never combine wildcard with credentials — pick specific origins for dev
if ENV == "production":
    CORS_ORIGINS = [FRONTEND_URL] if FRONTEND_URL else []
    CORS_CREDS   = True
else:
    CORS_ORIGINS = ["http://localhost:8000", "http://localhost:3000", "http://127.0.0.1:8000"]
    CORS_CREDS   = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_CREDS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        response.headers["Content-Security-Policy"]  = (
            "default-src 'self'; "
            "script-src 'self' https://maps.googleapis.com 'unsafe-inline'; "
            "img-src 'self' data: https://maps.googleapis.com https://maps.gstatic.com; "
            "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src https://fonts.gstatic.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        return response

app.add_middleware(_SecurityHeaders)
# Honour X-Forwarded-For set by Railway's trusted proxy so request.client.host is real
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


# ─── Models ───────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    address:    str = Field(min_length=10, max_length=200)
    buyer_name: str = Field(min_length=2,  max_length=100)
    buyer_email: EmailStr

    @field_validator("address")
    @classmethod
    def address_chars(cls, v: str) -> str:
        v = v.strip()
        if not _ADDRESS_RE.match(v):
            raise ValueError("Address contains invalid characters")
        return v

    @field_validator("buyer_name")
    @classmethod
    def name_chars(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[\w\s\-'.]+$", v):
            raise ValueError("Name contains invalid characters")
        return v

class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str
    mode: str

class JobStatus(BaseModel):
    job_id: str
    status: str
    address: Optional[str] = None
    message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


# ─── Background Report Generation ────────────────────────────────────────────

async def generate_and_deliver_report(
    job_id: str,
    address: str,
    buyer_name: str,
    buyer_email: str,
):
    """Full pipeline: research → PDF → email."""

    def update(status, message):
        job_update(job_id, status, message)
        log.info("[%s] [%s] %s", job_id, status, message)

    pdf_path = os.path.join(tempfile.gettempdir(), f"propertyreport_{job_id}.pdf")

    try:
        update("researching", "AI is researching suburb, schools, transport & infrastructure...")
        loop = asyncio.get_event_loop()
        report: PropertyReport = await loop.run_in_executor(None, research_property, address)

        update("generating_pdf", "Generating branded PDF report...")
        renderer = await loop.run_in_executor(None, _render_pdf, report, pdf_path)
        log.info("[%s] PDF rendered via %s", job_id, renderer)

        update("emailing", f"Sending report to {buyer_email}...")
        await loop.run_in_executor(
            None,
            lambda: send_report_email(
                report=report,
                recipient_email=buyer_email,
                recipient_name=buyer_name,
                pdf_attachment_path=pdf_path,
            )
        )

        job_update(job_id, "complete", f"Report sent to {buyer_email}",
                   completed_at=datetime.utcnow().isoformat())
        log.info("[%s] Complete", job_id)

    except Exception as e:
        job_update(job_id, "failed", "Report generation failed. Please try again or contact support.")
        log.exception("[%s] Failed: %s", job_id, e)

    finally:
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
                log.info("[%s] Temp PDF cleaned up", job_id)
            except OSError:
                pass


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/session", response_model=CheckoutResponse)
async def create_checkout(req: CheckoutRequest, request: Request):
    # ProxyHeadersMiddleware already resolved the real IP into request.client.host
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        log.warning("Rate limit hit for IP %s", client_ip)
        raise HTTPException(429, f"Too many requests. Max {_RATE_LIMIT} per {_RATE_WINDOW // 60} minutes.")

    if not stripe.api_key:
        raise HTTPException(500, "Stripe not configured — set STRIPE_SECRET_KEY")

    job_id     = str(uuid.uuid4())
    mode_label = "TEST — " if IS_TEST_MODE else ""

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "aud",
                    "product_data": {
                        "name": f"{mode_label}PropertyReport Report",
                        "description": f"AI property research report: {req.address}",
                    },
                    "unit_amount": REPORT_PRICE_AUD_CENTS,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{FRONTEND_URL}?job_id={job_id}&success=true",
            cancel_url=f"{FRONTEND_URL}?cancelled=true",
            customer_email=req.buyer_email,
            metadata={
                "job_id":      job_id,
                "address":     req.address,
                "buyer_name":  req.buyer_name,
                "buyer_email": req.buyer_email,
            }
        )

        job_create(job_id, req.address)

        return CheckoutResponse(
            checkout_url=session.url,
            session_id=session.id,
            mode="test" if IS_TEST_MODE else "live",
        )

    except stripe.StripeError as e:
        raise HTTPException(400, f"Stripe error: {str(e)}")


@app.post("/webhook")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # CRIT: verify signature BEFORE touching the payload.
    # construct_event() returns a StripeObject — call it for verification only,
    # then re-parse the raw bytes as a plain dict so the rest of the handler
    # can use standard dict access without StripeObject quirks.
    if STRIPE_WEBHOOK_SECRET:
        try:
            stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
            event = json.loads(payload)
        except stripe.SignatureVerificationError:
            log.warning("Webhook signature verification failed")
            raise HTTPException(400, "Invalid webhook signature")
    elif ENV == "production":
        raise HTTPException(400, "Webhook secret not configured")
    else:
        log.warning("Dev mode: skipping webhook signature verification")
        event = json.loads(payload)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta    = session.get("metadata", {})

        job_id      = meta.get("job_id")
        address     = meta.get("address")
        buyer_name  = meta.get("buyer_name", "Valued Buyer")
        buyer_email = meta.get("buyer_email")

        if not all([job_id, address, buyer_email]):
            log.warning("Webhook missing metadata — ignoring")
            return JSONResponse({"status": "ignored"})

        log.info("Payment confirmed [%s] — %s → %s", job_id, address, buyer_email)

        # MED-7: idempotency — skip if job already in a terminal or active state
        existing = job_get(job_id)
        if existing and existing.get("status") in ("complete", "researching", "generating_pdf", "emailing"):
            log.info("Duplicate webhook for job %s (status: %s) — ignoring", job_id, existing["status"])
            return JSONResponse({"status": "duplicate"})

        if not existing:
            job_create(job_id, address)

        background_tasks.add_task(
            generate_and_deliver_report, job_id, address, buyer_name, buyer_email
        )

    return JSONResponse({"status": "ok"})


@app.get("/api/v1/status/{job_id}", response_model=JobStatus)
async def get_report_status(job_id: str):
    job = job_get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return JobStatus(**job)


@app.post("/dev/generate")
async def dev_generate(
    request: Request,
    address: str,
    buyer_email: str,
    buyer_name: str = "Test Buyer",
    background_tasks: BackgroundTasks = None,
):
    if ENV == "production":
        raise HTTPException(403, "Dev endpoint disabled in production")
    if _DEV_TOKEN and request.headers.get("X-Dev-Token") != _DEV_TOKEN:
        raise HTTPException(403, "Invalid or missing X-Dev-Token header")

    job_id = str(uuid.uuid4())
    job_create(job_id, address, "Starting (dev mode — no payment required)...")

    background_tasks.add_task(
        generate_and_deliver_report, job_id, address, buyer_name, buyer_email
    )

    return {
        "job_id":  job_id,
        "message": "Report generation started (dev mode)",
        "poll":    f"GET /api/v1/status/{job_id}",
    }


@app.get("/api/v1/cfg")
def get_config():
    """Public config for the frontend — safe to expose (key is domain-restricted in Google Cloud)."""
    return {"google_maps_api_key": os.getenv("GOOGLE_MAPS_API_KEY", "")}


@app.get("/api/v1/compare-suburbs", response_model=CompareSuburbsResponse)
async def compare_suburbs(
    request: Request,
    suburb_a: str, state_a: str, postcode_a: str = "",
    lat_a: Optional[float] = None, lng_a: Optional[float] = None,
    suburb_b: str = "", state_b: str = "", postcode_b: str = "",
    lat_b: Optional[float] = None, lng_b: Optional[float] = None,
):
    """Free suburb comparator — median price, crime, commute, ABS demographics for 2 suburbs."""
    client_ip = request.client.host if request.client else "unknown"
    if not check_compare_rate_limit(client_ip):
        raise HTTPException(429, "Too many comparisons. Please try again shortly.")
    if not suburb_b or not state_b:
        raise HTTPException(400, "suburb_b and state_b are required")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        get_suburb_comparison,
        suburb_a, state_a, postcode_a, lat_a, lng_a,
        suburb_b, state_b, postcode_b, lat_b, lng_b,
    )


# ─── Serve Frontend (must be last — API routes take priority) ─────────────────

_frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    log.info("Frontend served from %s", _frontend_dir)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
