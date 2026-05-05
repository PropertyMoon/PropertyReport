"""
Email Delivery Module (v2)
Sends the property report via SendGrid with PDF attachment
"""

import os
import re
import json
import base64
from orchestrator import PropertyReport


def send_report_email(
    report: PropertyReport,
    recipient_email: str,
    recipient_name: str = "Valued Buyer",
    sender_email: str = None,
    sender_name: str = "PropertyReport Reports",
    pdf_attachment_path: str = None,
):
    sender_email = sender_email or os.getenv("SENDER_EMAIL", "reports@propertyreport.com.au")
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    subject      = f"Your PropertyReport Report — {report.address}"
    html_body    = build_email_html(report, recipient_name)

    if sendgrid_key:
        return _send_via_sendgrid(
            sendgrid_key, sender_email, sender_name,
            recipient_email, recipient_name, subject, html_body, pdf_attachment_path
        )
    else:
        return _send_via_smtp(
            sender_email, recipient_email, subject, html_body, pdf_attachment_path
        )


def build_email_html(report: PropertyReport, recipient_name: str) -> str:
    report_html = ""
    for line in report.summary.split("\n"):
        line = line.strip()
        if not line:
            report_html += "<br/>"
        elif line.startswith("## "):
            report_html += f'<h2 style="color:#1a3c5e;border-bottom:2px solid #c9a84c;padding-bottom:6px;margin:24px 0 10px">{line[3:]}</h2>'
        elif line.startswith("# "):
            report_html += f'<h1 style="color:#1a3c5e;">{line[2:]}</h1>'
        elif line.startswith("### "):
            report_html += f'<h3 style="color:#2c5f8a;margin:16px 0 6px">{line[4:]}</h3>'
        elif line.startswith("- ") or line.startswith("• "):
            report_html += f'<li style="margin-bottom:5px">{line[2:]}</li>'
        else:
            clean = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", line)
            report_html += f'<p style="margin:6px 0;line-height:1.6">{clean}</p>'

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0">
  <tr><td style="background:#1a3c5e;padding:24px 40px;">
    <span style="font-family:Georgia,serif;font-size:22px;color:#fff;font-weight:700;">Property<span style="color:#c9a84c;">IQ</span></span>
  </td></tr>
  <tr><td style="height:4px;background:#c9a84c;"></td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:40px 20px;">
<table width="640" align="center" cellpadding="0" cellspacing="0"
  style="background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.10);max-width:100%;">
<tr><td style="padding:36px 40px 0;">
  <p style="font-size:16px;color:#4a5568;margin:0 0 6px">Hi {recipient_name},</p>
  <p style="font-size:15px;color:#4a5568;line-height:1.6;margin:0 0 24px">Your PropertyReport report is ready. We've researched the suburb, schools, transport, government infrastructure and risk overlays.</p>
  <div style="background:#e8f0f8;border-left:4px solid #1a3c5e;padding:14px 18px;border-radius:0 8px 8px 0;margin-bottom:28px;">
    <span style="font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#8a9bb0;">Property Address</span><br/>
    <span style="font-size:17px;font-weight:700;color:#1a3c5e;">{report.address}</span>
  </div>
</td></tr>
<tr><td style="padding:0 40px 32px;">{report_html}</td></tr>
<tr><td style="background:#f5f7fa;padding:20px 40px;border-top:1px solid #e8ecf0;">
  <p style="font-size:13px;color:#8a9bb0;margin:0;">📎 <strong>Your full PDF report is attached</strong> to this email.</p>
</td></tr>
<tr><td style="padding:18px 40px;border-top:1px solid #f0f2f5;">
  <p style="font-size:11px;color:#aab4c4;margin:0;line-height:1.5;">For informational purposes only. Not financial advice. Always conduct independent due diligence.</p>
</td></tr>
</table></td></tr></table>
<table width="100%" cellpadding="0" cellspacing="0"><tr>
  <td style="background:#0f2540;padding:20px;text-align:center;">
    <p style="font-size:12px;color:rgba(255,255,255,0.4);margin:0;">© PropertyReport · Australia's AI Property Research Platform</p>
  </td></tr></table>
</body></html>"""


def _read_pdf_base64(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _send_via_sendgrid(api_key, sender_email, sender_name,
                        recipient_email, recipient_name, subject, html_body, pdf_path=None):
    try:
        import sendgrid
        from sendgrid.helpers.mail import (Mail, Email, To, Content, Attachment,
            FileContent, FileName, FileType, Disposition)
        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        message = Mail(from_email=Email(sender_email, sender_name),
                       to_emails=To(recipient_email, recipient_name),
                       subject=subject,
                       html_content=Content("text/html", html_body))
        pdf_data = _read_pdf_base64(pdf_path)
        if pdf_data:
            message.attachment = Attachment(FileContent(pdf_data),
                FileName("PropertyReport_Report.pdf"), FileType("application/pdf"), Disposition("attachment"))
        response = sg.client.mail.send.post(request_body=message.get())
        if response.status_code in (200, 202):
            print(f"✅ Email sent to {recipient_email} via SendGrid")
            return True
        print(f"❌ SendGrid error: {response.status_code}")
        return False
    except ImportError:
        print("⚠️  sendgrid not installed. Run: pip install sendgrid")
        return False
    except Exception as e:
        print(f"❌ SendGrid exception: {e}")
        return False


def _send_via_smtp(sender_email, recipient_email, subject, html_body, pdf_path=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", sender_email)
    smtp_pass = os.getenv("SMTP_PASS", "")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = sender_email
    msg["To"]      = recipient_email
    msg.attach(MIMEText(html_body, "html"))

    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename="PropertyReport_Report.pdf")
            msg.attach(part)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        print(f"✅ Email sent to {recipient_email} via SMTP")
        return True
    except Exception as e:
        print(f"❌ SMTP error: {e}")
        return False
