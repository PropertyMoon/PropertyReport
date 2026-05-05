# 🏠 PropertyReport

AI-powered Australian property research reports. Buyer pays → AI researches → PDF generated → report emailed.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/yourusername/propertyreport.git
cd propertyreport

# 2. Install
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your API keys

# 4. Verify setup (Stripe test mode check)
python stripe_test_setup.py

# 5. Run
uvicorn api:app --reload --port 8000
```

---

## Project Structure

```
propertyreport/
│
├── 📄 api.py                   FastAPI backend (Stripe + webhook + job polling)
├── 🤖 orchestrator.py          Claude AI research agent (6 research tasks)
├── 📊 pdf_generator.py         Branded PDF report builder (reportlab)
├── 📧 email_sender.py          Email delivery (SendGrid / SMTP)
├── 🖥️  main.py                 CLI runner — no payment required
│
├── frontend/
│   └── 🌐 index.html           Landing page + Stripe payment + status tracker
│
├── ⚙️  stripe_test_setup.py    Test mode setup checker + instructions
├── 📋 requirements.txt
├── 🔒 .env.example             Template — copy to .env (never commit .env)
├── 🚫 .gitignore
└── 📖 README.md
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Your Anthropic API key |
| `STRIPE_SECRET_KEY` | ✅ | `sk_test_...` for test, `sk_live_...` for production |
| `STRIPE_PUBLISHABLE_KEY` | ✅ | `pk_test_...` or `pk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | ✅ | From Stripe CLI or Dashboard |
| `SENDGRID_API_KEY` | ✅ | From SendGrid dashboard |
| `SENDER_EMAIL` | ✅ | Verified sender address |
| `REPORT_PRICE_CENTS` | optional | Default: 4900 ($49 AUD) |
| `FRONTEND_URL` | optional | Default: http://localhost:3000 |
| `ENV` | optional | `development` or `production` |

---

## Testing with Stripe Test Mode

### Step 1 — Get test keys
Go to [dashboard.stripe.com](https://dashboard.stripe.com) → toggle **Test Mode** (top right) → Developers → API Keys.

Copy the `sk_test_...` and `pk_test_...` keys into your `.env`.

### Step 2 — Install Stripe CLI
```bash
# macOS
brew install stripe/stripe-cli/stripe

# Windows / Linux
# https://stripe.com/docs/stripe-cli#install

stripe login
```

### Step 3 — Run everything locally

**Terminal 1 — API + Frontend:**
```bash
uvicorn api:app --reload --port 8000
```

**Terminal 2 — Webhook forwarding:**
```bash
stripe listen --forward-to localhost:8000/webhook
# Copy the whsec_... secret it prints → add to .env as STRIPE_WEBHOOK_SECRET
```

**Browser:** http://localhost:8000

> The frontend is now served directly by FastAPI — no separate server needed.

### Step 4 — Test card numbers

| Card Number | Result |
|---|---|
| `4242 4242 4242 4242` | ✅ Success |
| `4000 0000 0000 0002` | ❌ Declined |
| `4000 0025 0000 3155` | 🔐 3D Secure |
| `4000 0000 0000 0036` | 🇦🇺 Australian card |

Use any future expiry date and any 3-digit CVC.

### Shortcut — Dev endpoint (skip payment entirely)
```bash
curl -X POST "http://localhost:8000/dev/generate" \
  -d "address=45 Chapel St Windsor VIC 3181" \
  -d "buyer_email=you@gmail.com" \
  -d "buyer_name=Test Buyer"

# Then poll status:
curl http://localhost:8000/report/{job_id}
```

---

## GitHub Setup (First Time)

```bash
# 1. Create a new repo on github.com (do NOT initialise with README)

# 2. In your project folder:
git init
git add .
git commit -m "Initial commit — PropertyReport full stack"

# 3. Connect to GitHub
git remote add origin https://github.com/yourusername/propertyreport.git
git branch -M main
git push -u origin main
```

### Ongoing workflow
```bash
git add .
git commit -m "Your message here"
git push
```

### ⚠️ Security reminder
Make sure `.env` is in `.gitignore` (it is by default). Before pushing, verify:
```bash
git status   # .env should NOT appear in the list
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Status + mode (test/live) |
| POST | `/create-checkout` | Create Stripe checkout session |
| POST | `/webhook` | Stripe payment confirmed → start report |
| GET | `/report/{job_id}` | Poll report status |
| POST | `/dev/generate` | Dev: generate without payment |

---

## Deployment (Production)

### Railway (recommended — simplest)
```bash
# Install Railway CLI
npm install -g @railway/cli

railway login
railway init
railway up

# Set env vars in Railway dashboard
# Add your live Stripe keys, Anthropic key, SendGrid key
```

### Render
1. Connect your GitHub repo at render.com
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn api:app --host 0.0.0.0 --port $PORT`
4. Add environment variables in the dashboard

### Going Live Checklist
- [ ] Switch `STRIPE_SECRET_KEY` from `sk_test_...` to `sk_live_...`
- [ ] Switch `STRIPE_PUBLISHABLE_KEY` from `pk_test_...` to `pk_live_...`
- [ ] Create a new webhook in Stripe Dashboard pointing to your live API URL
- [ ] Set `ENV=production` (disables `/dev/generate` endpoint)
- [ ] Set `FRONTEND_URL` to your live domain
- [ ] Verify sender email in SendGrid
- [ ] Set `REPORT_PRICE_CENTS` to your desired price
