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

import stripe
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("propertyreport")

from orchestrator import research_property, PropertyReport
from pdf_generator import generate_pdf
from email_sender import send_report_email


# ─── Config ───────────────────────────────────────────────────────────────────

stripe.api_key         = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
REPORT_PRICE_AUD_CENTS = int(os.getenv("REPORT_PRICE_CENTS", "2000"))
FRONTEND_URL           = os.getenv("FRONTEND_URL", "http://localhost:8000")
IS_TEST_MODE           = stripe.api_key.startswith("sk_test_")
ENV                    = os.getenv("ENV", "development")
DB_PATH                = os.getenv("JOB_DB_PATH", "jobs.db")

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    mode  = "TEST MODE" if IS_TEST_MODE else "LIVE MODE"
    price = f"${REPORT_PRICE_AUD_CENTS / 100:.2f} AUD"
    log.info("PropertyReport API | %s | Price: %s | ENV: %s | DB: %s", mode, price, ENV, DB_PATH)

    _required_prod = {
        "ANTHROPIC_API_KEY":    os.getenv("ANTHROPIC_API_KEY"),
        "STRIPE_SECRET_KEY":    stripe.api_key,
        "STRIPE_WEBHOOK_SECRET": STRIPE_WEBHOOK_SECRET,
        "SENDGRID_API_KEY":     os.getenv("SENDGRID_API_KEY"),
        "SENDER_EMAIL":         os.getenv("SENDER_EMAIL"),
    }
    missing = [k for k, v in _required_prod.items() if not v]

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

CORS_ORIGINS = ["*"] if ENV != "production" else [FRONTEND_URL]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ───────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    address: str
    buyer_name: str
    buyer_email: EmailStr

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
        await loop.run_in_executor(None, generate_pdf, report, pdf_path)

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
        job_update(job_id, "failed", f"Error: {str(e)}")
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
    return {
        "status":    "ok",
        "service":   "PropertyReport API",
        "version":   "1.0.0",
        "stripe":    "test" if IS_TEST_MODE else "live",
        "env":       ENV,
        "price_aud": REPORT_PRICE_AUD_CENTS / 100,
    }


@app.post("/create-checkout", response_model=CheckoutResponse)
async def create_checkout(req: CheckoutRequest, request: Request):
    client_ip = (request.headers.get("x-forwarded-for") or
                 (request.client.host if request.client else "unknown")).split(",")[0].strip()
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

    event = json.loads(payload)

    if STRIPE_WEBHOOK_SECRET:
        try:
            stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except stripe.SignatureVerificationError:
            log.warning("Webhook signature verification failed")
            raise HTTPException(400, "Invalid webhook signature")
    elif ENV == "production":
        raise HTTPException(400, "Webhook secret not configured")
    else:
        log.warning("Dev mode: skipping webhook signature verification")

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

        if not job_get(job_id):
            job_create(job_id, address)

        background_tasks.add_task(
            generate_and_deliver_report, job_id, address, buyer_name, buyer_email
        )

    return JSONResponse({"status": "ok"})


@app.get("/report/{job_id}", response_model=JobStatus)
async def get_report_status(job_id: str):
    job = job_get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return JobStatus(**job)


@app.post("/dev/generate")
async def dev_generate(
    address: str,
    buyer_email: str,
    buyer_name: str = "Test Buyer",
    background_tasks: BackgroundTasks = None,
):
    if ENV == "production":
        raise HTTPException(403, "Dev endpoint disabled in production")

    job_id = str(uuid.uuid4())
    job_create(job_id, address, "Starting (dev mode — no payment required)...")

    background_tasks.add_task(
        generate_and_deliver_report, job_id, address, buyer_name, buyer_email
    )

    return {
        "job_id":  job_id,
        "message": "Report generation started (dev mode)",
        "poll":    f"GET /report/{job_id}",
    }


# ─── Serve Frontend (must be last — API routes take priority) ─────────────────

_frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    log.info("Frontend served from %s", _frontend_dir)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
