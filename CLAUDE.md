# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run development server (hot-reload)
uvicorn api:app --reload --port 8000

# Run Stripe webhook forwarding (separate terminal)
stripe listen --forward-to localhost:8000/webhook

# Generate a report via CLI (no payment)
python cli.py "45 Chapel St Windsor VIC 3181" buyer@email.com "Jane Smith"

# Generate via API dev endpoint (no payment)
curl -X POST "http://localhost:8000/dev/generate" \
  -d "address=45 Chapel St Windsor VIC 3181" \
  -d "buyer_email=you@gmail.com" \
  -d "buyer_name=Test Buyer"

# Poll report status
curl http://localhost:8000/report/{job_id}

# Verify Stripe test-mode setup
python stripe_test_setup.py
```

No test suite exists. Manual testing via the dev endpoint or CLI is the primary verification path.

## Architecture

**End-to-end flow:**
1. Buyer submits address + email on `frontend/index.html` → POST `/create-checkout` → Stripe Checkout
2. Stripe fires `checkout.session.completed` → POST `/webhook` → FastAPI starts a `BackgroundTask`
3. Background task: `research_property()` → `generate_pdf()` → `send_report_email()` → job marked `complete`
4. Frontend polls `GET /report/{job_id}` to show live status

**Job state machine** (persisted in SQLite `jobs.db`):
`pending` → `researching` → `generating_pdf` → `emailing` → `complete` / `failed`

**AI layer — two-model design** (`orchestrator.py`):
- **Research phase**: 6 sequential tasks (`suburb`, `schools`, `government_projects`, `transport`, `property_market`, `risk_overlays`) each call `claude-haiku-4-5-20251001` with the `web_search_20250305` tool (max 3 searches per task). Each returns a JSON dict.
- **Synthesis phase**: All 6 JSON dicts are passed to `claude-sonnet-4-5` which writes the full Markdown narrative. A fixed `skeleton` template in `synthesise_report()` enforces the section structure.
- `extract_metrics()` post-processes the research dicts into 7 scorecard values (median price, rental yield, school quality, flood risk, CBD train time, market outlook, last sale). Falls back to regex over the narrative summary if structured fields are missing.
- Rate-limit retry: up to 4 attempts with 60s × attempt backoff. A hard `time.sleep(60)` separates the research and synthesis phases to reset the Anthropic rate-limit window.

**PDF** (`pdf_generator.py`): ReportLab. Renders the Markdown summary into a branded A4 document. Includes inline charts (ICSEA bar chart, crime percentile chart), a comparable sales table, and an amenities panel — do not duplicate these in prose when editing prompts.

**Email** (`email_sender.py`): SendGrid primary; falls back to SMTP if `SENDGRID_API_KEY` is absent.

**Frontend** (`frontend/`): Static HTML/JS served by FastAPI's `StaticFiles` mount, which must be registered last in `api.py` so API routes take priority.

**State detection duplication**: `_STATE_SOURCES` (state abbreviation → planning/crime/flood URLs) is defined independently in both `orchestrator.py` and `pdf_generator.py`. Keep them in sync when adding states or changing URLs.

## Key env vars

| Variable | Notes |
|---|---|
| `ANTHROPIC_API_KEY` | Required |
| `STRIPE_SECRET_KEY` | `sk_test_...` enables test mode; controls `IS_TEST_MODE` flag |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` from `stripe listen` output |
| `SENDGRID_API_KEY` + `SENDER_EMAIL` | Required for email delivery |
| `GOOGLE_MAPS_API_KEY` | Google Maps Platform key — needs **Street View Static API**, **Maps Static API** (backend PDF/cover images), and **Places API** (frontend autocomplete). Key is served to the frontend via `GET /config`; restrict it to your domain in Google Cloud Console. |
| `ENV` | Set to `production` to disable `/dev/generate` and enforce all required vars |
| `REPORT_PRICE_CENTS` | Default `2000` (AUD $20.00) |
| `JOB_DB_PATH` | SQLite file path, default `jobs.db` |
| `CHECKOUT_RATE_LIMIT` / `CHECKOUT_RATE_WINDOW` | In-memory per-IP rate limiter, default 3 requests / 600s |

## Deployment

Deployed on Railway via `railway.toml`. Health check at `GET /health`. The `/dev/generate` endpoint is disabled when `ENV=production`.
