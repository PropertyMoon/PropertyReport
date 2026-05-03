"""
Stripe Test Mode Setup & Local Webhook Listener
================================================

This script helps you:
1. Verify your Stripe test keys are configured correctly
2. Shows you exactly how to set up the Stripe CLI for local webhook testing
3. Provides test card numbers to simulate payments

Run this first to confirm everything is wired up before going live.
"""

import os
import sys
import subprocess


def check_env():
    """Check all required environment variables are set."""
    print("\n🔍 Checking environment variables...\n")

    required = {
        "ANTHROPIC_API_KEY":      ("sk-ant-",  "Anthropic"),
        "STRIPE_SECRET_KEY":      ("sk_test_", "Stripe (test mode)"),
        "STRIPE_PUBLISHABLE_KEY": ("pk_test_", "Stripe (test mode)"),
        "STRIPE_WEBHOOK_SECRET":  ("whsec_",   "Stripe webhook"),
        "SENDGRID_API_KEY":       ("SG.",       "SendGrid"),
        "SENDER_EMAIL":           ("@",         "Sender email"),
    }

    all_ok = True
    for var, (prefix, label) in required.items():
        val = os.getenv(var, "")
        if not val:
            print(f"  ❌  {var:30s} — NOT SET")
            all_ok = False
        elif not val.startswith(prefix):
            print(f"  ⚠️   {var:30s} — Set but unexpected format (expected to start with '{prefix}')")
            all_ok = False
        else:
            masked = val[:12] + "..." + val[-4:]
            print(f"  ✅  {var:30s} — {masked}  ({label})")

    # Warn if using live keys in dev
    secret = os.getenv("STRIPE_SECRET_KEY", "")
    if secret.startswith("sk_live_") and os.getenv("ENV") != "production":
        print("\n  🚨  WARNING: You are using LIVE Stripe keys in a non-production environment!")
        print("      Switch to sk_test_ keys for local development.\n")
        all_ok = False

    return all_ok


def print_stripe_cli_setup():
    """Print instructions to set up Stripe CLI for local webhook forwarding."""
    print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STRIPE CLI — LOCAL WEBHOOK SETUP (Test Mode)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Install Stripe CLI (macOS):
   brew install stripe/stripe-cli/stripe

   Windows / Linux:
   https://stripe.com/docs/stripe-cli#install

2. Login to Stripe:
   stripe login

3. Forward webhooks to your local API (run in a separate terminal):
   stripe listen --forward-to localhost:8000/webhook

4. The CLI will print your webhook secret — copy it:
   > Ready! Your webhook signing secret is whsec_xxxxxx
   
   Add it to your .env:
   STRIPE_WEBHOOK_SECRET=whsec_xxxxxx

5. In another terminal, start your API:
   uvicorn api:app --reload --port 8000

6. In another terminal, trigger a test payment:
   stripe trigger checkout.session.completed
""")


def print_test_cards():
    """Print Stripe test card numbers."""
    print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STRIPE TEST CARD NUMBERS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use these in the Stripe Checkout page during testing.
Any future expiry date and any 3-digit CVC work.

  ✅  Success payment:
      4242 4242 4242 4242

  ❌  Card declined:
      4000 0000 0000 0002

  🔐  Requires 3D Secure authentication:
      4000 0025 0000 3155

  💳  Insufficient funds:
      4000 0000 0000 9995

  🇦🇺  Australian card (success):
      4000 0000 0000 0036

Full list: https://stripe.com/docs/testing#cards
""")


def print_test_flow():
    """Print the complete end-to-end test flow."""
    print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  END-TO-END TEST FLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Terminal 1 — Start API:
  uvicorn api:app --reload --port 8000

Terminal 2 — Forward webhooks:
  stripe listen --forward-to localhost:8000/webhook

Terminal 3 — Serve frontend:
  cd frontend && python -m http.server 3000

Browser:
  http://localhost:3000
  → Enter any Melbourne address
  → Enter your email
  → Click Pay → use test card 4242 4242 4242 4242
  → Watch the status page update in real time
  → Check your inbox for the report!

Or skip the browser entirely with dev endpoint:
  curl -X POST "http://localhost:8000/dev/generate" \\
    -d "address=45 Chapel St Windsor VIC 3181" \\
    -d "buyer_email=you@gmail.com" \\
    -d "buyer_name=Test Buyer"

  Then poll:
  curl http://localhost:8000/report/{job_id}

Set $1 price for testing (update .env):
  REPORT_PRICE_CENTS=100
""")


def verify_stripe_connection():
    """Actually hit the Stripe API to verify the key works."""
    try:
        import stripe
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
        if not stripe.api_key:
            print("  ⏭️  Skipping Stripe API verification — key not set")
            return

        print("\n🔌 Verifying Stripe API connection...")
        account = stripe.Account.retrieve()
        mode = "TEST" if stripe.api_key.startswith("sk_test_") else "LIVE"
        print(f"  ✅  Connected to Stripe [{mode} MODE]")
        print(f"      Account: {account.get('email', 'N/A')}")
        print(f"      Country: {account.get('country', 'N/A')}")

    except ImportError:
        print("  ⚠️  stripe not installed — run: pip install stripe")
    except Exception as e:
        print(f"  ❌  Stripe connection failed: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("  PropertyIQ — Stripe Test Mode Setup")
    print("=" * 50)

    env_ok = check_env()
    verify_stripe_connection()
    print_stripe_cli_setup()
    print_test_cards()
    print_test_flow()

    if not env_ok:
        print("⚠️  Fix the environment issues above before testing.\n")
        sys.exit(1)
    else:
        print("✅  All checks passed — you're ready to test!\n")
