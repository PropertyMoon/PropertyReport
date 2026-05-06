"""
Email Delivery Module
Sends a concise summary email with key metrics and PDF attachment.
"""

import os
import re
import base64
from orchestrator import PropertyReport


def send_report_email(
    report: PropertyReport,
    recipient_email: str,
    recipient_name: str = "Valued Buyer",
    sender_email: str = None,
    sender_name: str = "PropertyReport",
    pdf_attachment_path: str = None,
):
    sender_email = sender_email or os.getenv("SENDER_EMAIL", "reports@propertyreport.com.au")
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    subject      = f"Your Property Report — {report.address}"
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


def _extract_executive_summary(summary: str) -> str:
    """Pull just the Executive Summary section from the full narrative."""
    lines = summary.split("\n")
    in_exec = False
    result = []

    for line in lines:
        stripped = line.strip()
        # Start capturing at Executive Summary heading
        if re.search(r'executive\s+summary', stripped, re.IGNORECASE) and stripped.startswith("#"):
            in_exec = True
            continue
        # Stop at the next section heading
        if in_exec and stripped.startswith("#") and not re.search(r'executive\s+summary', stripped, re.IGNORECASE):
            break
        if in_exec and stripped:
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", stripped)  # strip bold markers
            clean = clean.lstrip("#- •").strip()
            if clean:
                result.append(clean)

    # Fallback: first 3 non-empty non-heading lines
    if not result:
        count = 0
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("---"):
                clean = re.sub(r"\*\*(.*?)\*\*", r"\1", s).lstrip("- •").strip()
                if clean:
                    result.append(clean)
                    count += 1
                    if count >= 3:
                        break

    return " ".join(result[:4])  # max ~4 sentences


def _metrics_table_html(metrics: dict) -> str:
    cells = [
        ("Median Price",  metrics.get("median_price",   "N/A")),
        ("Rental Yield",  metrics.get("rental_yield",   "N/A")),
        ("Schools",       metrics.get("school_quality", "N/A")),
        ("Flood Risk",    metrics.get("flood_risk",     "N/A")),
        ("Train to CBD",  metrics.get("cbd_train_mins", "N/A")),
        ("Market",        metrics.get("market_outlook", "N/A")),
    ]
    cell_html = ""
    for label, value in cells:
        cell_html += f"""
        <td style="width:16.6%;padding:14px 8px;text-align:center;border-right:1px solid #e2e8f0;vertical-align:top;">
          <div style="font-size:10px;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;color:#94a3b8;margin-bottom:6px;">{label}</div>
          <div style="font-size:15px;font-weight:700;color:#1e293b;line-height:1.3;">{value}</div>
        </td>"""

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0"
      style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin:20px 0;">
      <tr style="background:#f8fafc;">{cell_html}</tr>
    </table>"""


def build_email_html(report: PropertyReport, recipient_name: str) -> str:
    exec_summary = _extract_executive_summary(report.summary or "")
    metrics      = report.metrics if isinstance(getattr(report, "metrics", None), dict) else {}
    metrics_html = _metrics_table_html(metrics)
    today        = __import__("datetime").datetime.now().strftime("%d %B %Y")

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Arial,sans-serif;">

<!-- Header -->
<table width="100%" cellpadding="0" cellspacing="0">
  <tr><td style="background:#0f172a;padding:20px 40px;">
    <span style="font-family:Georgia,serif;font-size:20px;color:#fff;font-weight:700;">
      Property<span style="color:#10b981;">Report</span>
    </span>
  </td></tr>
  <tr><td style="height:3px;background:#10b981;"></td></tr>
</table>

<!-- Body -->
<table width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:32px 20px;">
<table width="620" align="center" cellpadding="0" cellspacing="0"
  style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);max-width:100%;">

  <!-- Greeting -->
  <tr><td style="padding:32px 36px 0;">
    <p style="font-size:16px;color:#334155;margin:0 0 4px;">Hi {recipient_name},</p>
    <p style="font-size:14px;color:#64748b;margin:0 0 20px;line-height:1.5;">
      Your property research report is ready. Here's a quick summary — the full report is attached as a PDF.
    </p>

    <!-- Address block -->
    <div style="background:#f1f5f9;border-left:4px solid #1e293b;padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:20px;">
      <div style="font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;margin-bottom:4px;">Property Address</div>
      <div style="font-size:16px;font-weight:700;color:#1e293b;">{report.address}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:2px;">Report generated {today}</div>
    </div>
  </td></tr>

  <!-- Key Metrics -->
  <tr><td style="padding:0 36px;">
    <div style="font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;margin-bottom:4px;">Key Metrics at a Glance</div>
    {metrics_html}
  </td></tr>

  <!-- Executive Summary -->
  <tr><td style="padding:4px 36px 28px;">
    <div style="font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;margin-bottom:8px;">Executive Summary</div>
    <p style="font-size:14px;color:#475569;line-height:1.7;margin:0;">{exec_summary}</p>
  </td></tr>

  <!-- PDF nudge -->
  <tr><td style="background:#f8fafc;padding:16px 36px;border-top:1px solid #e8ecf0;">
    <p style="font-size:13px;color:#64748b;margin:0;">
      📎 <strong>Your full PDF report is attached</strong> — including suburb analysis, schools,
      transport, government projects, risk overlays and market outlook.
    </p>
  </td></tr>

  <!-- Disclaimer -->
  <tr><td style="padding:14px 36px;">
    <p style="font-size:11px;color:#aab4c4;margin:0;line-height:1.5;">
      For informational purposes only. Not financial advice. Always conduct independent due diligence.
    </p>
  </td></tr>

</table></td></tr></table>

<!-- Footer -->
<table width="100%" cellpadding="0" cellspacing="0"><tr>
  <td style="background:#0f172a;padding:16px;text-align:center;">
    <p style="font-size:11px;color:rgba(255,255,255,0.35);margin:0;">© PropertyReport · Australia's AI Property Research Platform</p>
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
                FileName("PropertyReport.pdf"), FileType("application/pdf"), Disposition("attachment"))
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
            part.add_header("Content-Disposition", "attachment", filename="PropertyReport.pdf")
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
