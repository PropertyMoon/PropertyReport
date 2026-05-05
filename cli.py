"""
PropertyReport - Main Runner
Run this to generate and email a property report in one command.

Usage:
  python cli.py "123 Smith St, Richmond VIC 3121" buyer@email.com "Jane Smith"

Environment variables required:
  ANTHROPIC_API_KEY     - Your Anthropic API key
  SENDGRID_API_KEY      - Your SendGrid API key (or use SMTP vars below)
  SENDER_EMAIL          - Your verified sender email

Optional SMTP fallback (if not using SendGrid):
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
"""

import sys
import os
from orchestrator import research_property
from email_sender import send_report_email


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("\nExample:")
        print('  python cli.py "45 Chapel St, Windsor VIC 3181" buyer@gmail.com "John Smith"')
        sys.exit(1)
    
    address = sys.argv[1]
    recipient_email = sys.argv[2]
    recipient_name = sys.argv[3] if len(sys.argv) > 3 else "Valued Buyer"
    
    # Validate API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ Error: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)
    
    print(f"\n🚀 PropertyReport Report Generator")
    print(f"   Address: {address}")
    print(f"   Sending to: {recipient_email} ({recipient_name})")
    print()
    
    # Step 1: Research the property
    report = research_property(address)
    
    # Step 2: Email the report
    print(f"\n📧 Sending report to {recipient_email}...")
    success = send_report_email(
        report=report,
        recipient_email=recipient_email,
        recipient_name=recipient_name
    )
    
    if success:
        print("\n🎉 Done! Report researched and emailed successfully.")
    else:
        print("\n⚠️  Report generated but email failed. Check report_output.json for the data.")


if __name__ == "__main__":
    main()
