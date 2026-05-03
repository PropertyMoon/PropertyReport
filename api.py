"""
PropertyIQ - FastAPI Backend
Supports both Stripe TEST and LIVE modes via environment variables.

Install:
  pip install -r requirements.txt

Run (development):
  uvicorn api:app --reload --port 8000

Run (production):
  uvicorn api:app --host 0.0.0.0 --port 8000 --workers 2
"""

import os
import uuid
import asyncio
import json
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

# Load .env file automatically in development
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional — env vars can be set manually

import stripe
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from orchestrator import research_property, PropertyReport
from pdf_generator import generate_pdf
from email_sender import send_report_email


# ─── Config ───────────────────────────────────────────────────────────────────

stripe.api_key          = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET   = os.getenv("STRIPE_WEBHOOK_SECRET", "")
REPORT_PRICE_AUD_CENTS  = int(os.getenv("REPORT_PRICE_CENTS", "4900"))
FRONTEND_URL            = os.getenv("FRONTEND_URL", "http://localhost:3000")
IS_TEST_MODE            = stripe.api_key.startswith("sk_test_")
ENV                     = os.getenv("ENV", "development")


# ─── In-memory job store ──────────────────────────────────────────────────────
# In production replace with Redis or a database

jobs: dict[str, dict] = {}


# ─── Startup ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    mode = "🧪 TEST MODE" if IS_TEST_MODE else "🚀 LIVE MODE"
    price = f"${REPORT_PRICE_AUD_CENTS / 100:.2f} AUD"
    print(f"\n{'='*50}")
    print(f"  PropertyIQ API  |  {mode}")
    print(f"  Price: {price}  |  ENV: {ENV}")
    print(f"{'='*50}\n")

    if not stripe.api_key:
        print("⚠️  WARNING: STRIPE_SECRET_KEY not set — payments will fail")
    if not STRIPE_WEBHOOK_SECRET:
        print("⚠️  WARNING: STRIPE_WEBHOOK_SECRET not set — webhooks will be rejected")

    yield


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="PropertyIQ API",
    description="AI-powered Australian property research reports",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your domain in production
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
    mode: str  # "test" or "live"

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
        jobs[job_id]["status"]  = status
        jobs[job_id]["message"] = message
        print(f"[{job_id}] [{status}] {message}")

    try:
        # Step 1: Research
        update("researching", "AI is researching suburb, schools, transport & infrastructure...")
        loop = asyncio.get_event_loop()
        report: PropertyReport = await loop.run_in_executor(
            None, research_property, address
        )

        # Step 2: PDF
        update("generating_pdf", "Generating branded PDF report...")
        pdf_path = f"/tmp/propertyiq_{job_id}.pdf"
        await loop.run_in_executor(None, generate_pdf, report, pdf_path)

        # Step 3: Email
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

        # Done
        jobs[job_id]["status"]       = "complete"
        jobs[job_id]["message"]      = f"Report sent to {buyer_email}"
        jobs[job_id]["completed_at"] = datetime.utcnow().isoformat()
        print(f"[{job_id}] ✅ Complete")

    except Exception as e:
        jobs[job_id]["status"]  = "failed"
        jobs[job_id]["message"] = f"Error: {str(e)}"
        print(f"[{job_id}] ❌ Failed: {e}")
        import traceback; traceback.print_exc()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":    "ok",
        "service":   "PropertyIQ API",
        "version":   "1.0.0",
        "stripe":    "test" if IS_TEST_MODE else "live",
        "env":       ENV,
        "price_aud": REPORT_PRICE_AUD_CENTS / 100,
    }


@app.post("/create-checkout", response_model=CheckoutResponse)
async def create_checkout(req: CheckoutRequest):
    """Create a Stripe Checkout session and return the URL."""

    if not stripe.api_key:
        raise HTTPException(500, "Stripe not configured — set STRIPE_SECRET_KEY")

    job_id = str(uuid.uuid4())
    mode_label = "TEST — " if IS_TEST_MODE else ""

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "aud",
                    "product_data": {
                        "name": f"{mode_label}PropertyIQ Report",
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

        jobs[job_id] = {
            "job_id":     job_id,
            "status":     "pending",
            "address":    req.address,
            "message":    "Awaiting payment...",
            "created_at": datetime.utcnow().isoformat(),
        }

        return CheckoutResponse(
            checkout_url=session.url,
            session_id=session.id,
            mode="test" if IS_TEST_MODE else "live",
        )

    except stripe.StripeError as e:
        raise HTTPException(400, f"Stripe error: {str(e)}")


@app.post("/webhook")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Stripe sends this when payment is confirmed.
    Configure in Stripe Dashboard → Webhooks:
      URL: https://yourdomain.com/webhook
      Event: checkout.session.completed
    
    For local testing use Stripe CLI:
      stripe listen --forward-to localhost:8000/webhook
    """
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Skip signature check if no secret set (only allow in dev)
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except stripe.SignatureVerificationError:
            print("❌ Webhook signature verification failed")
            raise HTTPException(400, "Invalid webhook signature")
    else:
        if ENV == "production":
            raise HTTPException(400, "Webhook secret not configured")
        print("⚠️  Dev mode: skipping webhook signature verification")
        event = json.loads(payload)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta    = session.get("metadata", {})

        job_id      = meta.get("job_id")
        address     = meta.get("address")
        buyer_name  = meta.get("buyer_name", "Valued Buyer")
        buyer_email = meta.get("buyer_email")

        if not all([job_id, address, buyer_email]):
            print("⚠️  Webhook missing metadata — ignoring")
            return JSONResponse({"status": "ignored"})

        print(f"💳 Payment confirmed [{job_id}] — {address} → {buyer_email}")

        if job_id not in jobs:
            jobs[job_id] = {
                "job_id":     job_id,
                "status":     "pending",
                "address":    address,
                "created_at": datetime.utcnow().isoformat(),
            }

        background_tasks.add_task(
            generate_and_deliver_report,
            job_id, address, buyer_name, buyer_email
        )

    return JSONResponse({"status": "ok"})


@app.get("/report/{job_id}", response_model=JobStatus)
async def get_report_status(job_id: str):
    """Poll this every 5 seconds after payment to track progress."""
    job = jobs.get(job_id)
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
    """
    DEV ONLY — bypass Stripe and generate a report immediately.
    Disabled automatically when ENV=production.
    """
    if ENV == "production":
        raise HTTPException(403, "Dev endpoint disabled in production")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id":     job_id,
        "status":     "pending",
        "address":    address,
        "message":    "Starting (dev mode — no payment required)...",
        "created_at": datetime.utcnow().isoformat(),
    }

    background_tasks.add_task(
        generate_and_deliver_report, job_id, address, buyer_name, buyer_email
    )

    return {
        "job_id":  job_id,
        "message": "Report generation started (dev mode)",
        "poll":    f"GET /report/{job_id}",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
