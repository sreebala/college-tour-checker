"""
Dual-channel notification layer.

  send_slot_alert()  — instant SMS + email when a new morning slot appears
  send_daily_report() — HTML email digest (connection status + slot snapshot)

Credentials are read from environment variables; see .env.example.
"""

import os
import base64
import logging
from datetime import datetime, timezone
from typing import Optional

from twilio.rest import Client as TwilioClient
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName,
    FileType, Disposition, ContentId,
)

logger = logging.getLogger(__name__)

# ── Credentials (injected via env / GitHub Actions secrets) ──────────────────
_TWILIO_SID    = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_FROM   = os.environ.get("TWILIO_FROM_NUMBER", "")
_ALERT_PHONE   = os.environ.get("ALERT_PHONE_NUMBER", "")
_SG_KEY        = os.environ.get("SENDGRID_API_KEY", "")
_FROM_EMAIL    = os.environ.get("FROM_EMAIL", "alerts@example.com")
_TO_EMAIL      = os.environ.get("TO_EMAIL", "you@gmail.com")


# ── Low-level senders ─────────────────────────────────────────────────────────

def send_sms(body: str) -> bool:
    if not all([_TWILIO_SID, _TWILIO_TOKEN, _TWILIO_FROM, _ALERT_PHONE]):
        logger.error("Twilio credentials incomplete — skipping SMS")
        return False
    try:
        client = TwilioClient(_TWILIO_SID, _TWILIO_TOKEN)
        msg = client.messages.create(body=body, from_=_TWILIO_FROM, to=_ALERT_PHONE)
        logger.info("SMS sent: %s", msg.sid)
        return True
    except Exception as exc:
        logger.error("SMS failed: %s", exc)
        return False


def send_email(subject: str, html: str, text: str) -> bool:
    if not _SG_KEY:
        logger.error("SENDGRID_API_KEY not set — skipping email")
        return False
    try:
        message = Mail(
            from_email=_FROM_EMAIL,
            to_emails=_TO_EMAIL,
            subject=subject,
            html_content=html,
            plain_text_content=text,
        )
        sg = SendGridAPIClient(_SG_KEY)
        resp = sg.send(message)
        logger.info("Email sent (status %s): %s", resp.status_code, subject)
        return resp.status_code in (200, 201, 202)
    except Exception as exc:
        logger.error("Email failed: %s", exc)
        return False


# ── High-level alert builders ─────────────────────────────────────────────────

def send_slot_alert(university: str, target_date: str, morning_slots: list) -> None:
    """Instant alert: fire as soon as a new morning slot is detected."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    slot_lines = "\n".join(f"  • {s.time_str}" for s in morning_slots)
    slot_items = "".join(f"<li><strong>{s.time_str}</strong></li>" for s in morning_slots)

    booking_url = (
        "https://apply.admissions.uci.edu/portal/uci_uga_tours_prospect?tab=prospect_guidedtours"
        if university == "UCI"
        else "https://connect.admission.ucla.edu/portal/tours"
    )

    # ── SMS ───────────────────────────────────────────────────────────────────
    sms = (
        f"TOUR SLOT OPEN — {university}\n"
        f"{target_date} morning:\n"
        f"{slot_lines}\n"
        f"Book NOW: {booking_url}"
    )
    send_sms(sms)

    # ── Email ─────────────────────────────────────────────────────────────────
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:24px">
  <h1 style="color:#c0392b;margin-bottom:4px">Tour Slot Available!</h1>
  <p style="font-size:18px;margin-top:0">
    <strong>{university}</strong> &mdash; {target_date}
  </p>
  <h2 style="font-size:16px">Morning Slots Found:</h2>
  <ul style="font-size:16px;line-height:1.8">{slot_items}</ul>
  <p>
    <a href="{booking_url}"
       style="display:inline-block;padding:12px 24px;background:#c0392b;
              color:#fff;text-decoration:none;border-radius:4px;font-size:16px">
      Book Your Spot Now
    </a>
  </p>
  <hr style="margin:24px 0;border:none;border-top:1px solid #ddd">
  <p style="color:#999;font-size:12px">Alert triggered at {now}</p>
</body>
</html>
"""
    text = (
        f"TOUR SLOT AVAILABLE — {university}\n"
        f"{target_date}\n\n"
        f"Morning slots:\n{slot_lines}\n\n"
        f"Book now: {booking_url}\n\n"
        f"(Alert at {now})"
    )
    send_email(
        subject=f"[UCIUCLA] URGENT: {university} Morning Tour Slot Open — {target_date}",
        html=html,
        text=text,
    )


def _make_inline_attachment(path: Optional[str], cid: str) -> Optional[Attachment]:
    """Base64-encode a PNG and return a SendGrid inline attachment, or None."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        att = Attachment()
        att.file_content = FileContent(data)
        att.file_name    = FileName(os.path.basename(path))
        att.file_type    = FileType("image/png")
        att.disposition  = Disposition("inline")
        att.content_id   = ContentId(cid)
        return att
    except Exception as exc:
        logger.warning("Could not attach screenshot %s: %s", path, exc)
        return None


def send_daily_report(uci_result, ucla_result, last_check: str) -> None:
    """Daily digest: connection health + current slot snapshot for both schools."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _badge(ok: bool) -> str:
        return "✅ Connected" if ok else "❌ FAILED"

    def _slot_cell(result) -> str:
        if not result.connected:
            return f"<span style='color:red'>Connection error: {result.error or 'unknown'}</span>"
        if result.morning_slots:
            times = ", ".join(s.time_str for s in result.morning_slots)
            return f"<span style='color:green'><strong>MORNING SLOTS OPEN: {times}</strong></span>"
        if result.slots:
            times = ", ".join(s.time_str for s in result.slots)
            return f"Afternoon only: {times}"
        return "No available slots (still fully booked)"

    def _slot_text(result) -> str:
        if not result.connected:
            return f"ERROR: {result.error or 'unknown'}"
        if result.morning_slots:
            return "MORNING SLOTS OPEN: " + ", ".join(s.time_str for s in result.morning_slots)
        if result.slots:
            return "Afternoon only: " + ", ".join(s.time_str for s in result.slots)
        return "Fully booked"

    system_ok = uci_result.connected and ucla_result.connected
    system_badge = "✅ Running normally" if system_ok else "⚠️ One or more portals unreachable"

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px;
             border:1px solid #ddd;border-radius:8px">
  <h1 style="color:#2c3e50">Daily Tour Checker Report</h1>
  <p style="color:#666">Generated: <strong>{now}</strong> &nbsp;|&nbsp;
     Last scrape: <strong>{last_check}</strong></p>

  <table style="width:100%;border-collapse:collapse;margin:20px 0;font-size:14px">
    <thead>
      <tr style="background:#f4f6f8;text-align:left">
        <th style="padding:10px;border:1px solid #ddd">School</th>
        <th style="padding:10px;border:1px solid #ddd">Target Date</th>
        <th style="padding:10px;border:1px solid #ddd">Connection</th>
        <th style="padding:10px;border:1px solid #ddd">Slot Status</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="padding:10px;border:1px solid #ddd"><strong>UCI</strong></td>
        <td style="padding:10px;border:1px solid #ddd">July 8, 2026 — Morning</td>
        <td style="padding:10px;border:1px solid #ddd">{_badge(uci_result.connected)}</td>
        <td style="padding:10px;border:1px solid #ddd">{_slot_cell(uci_result)}</td>
      </tr>
      <tr style="background:#f9f9f9">
        <td style="padding:10px;border:1px solid #ddd"><strong>UCLA</strong></td>
        <td style="padding:10px;border:1px solid #ddd">July 9, 2026 — Morning</td>
        <td style="padding:10px;border:1px solid #ddd">{_badge(ucla_result.connected)}</td>
        <td style="padding:10px;border:1px solid #ddd">{_slot_cell(ucla_result)}</td>
      </tr>
    </tbody>
  </table>

  <h3>July Calendar Snapshots</h3>
  <table style="width:100%;border-collapse:collapse">
    <tr>
      <td style="padding:8px;text-align:center;width:50%">
        <p style="margin:0 0 6px;font-weight:bold">UCI — July 8</p>
        <img src="cid:uci_calendar" alt="UCI July calendar"
             style="max-width:100%;border:1px solid #ddd;border-radius:4px">
      </td>
      <td style="padding:8px;text-align:center;width:50%">
        <p style="margin:0 0 6px;font-weight:bold">UCLA — July 9</p>
        <img src="cid:ucla_calendar" alt="UCLA July calendar"
             style="max-width:100%;border:1px solid #ddd;border-radius:4px">
      </td>
    </tr>
  </table>

  <h3>System Health</h3>
  <p>{system_badge}</p>
  <ul>
    <li>Check interval: every 30 minutes (GitHub Actions)</li>
    <li>Next daily report: ~24 hours from now</li>
    <li>Monitoring window: until August 2026</li>
  </ul>
  <p style="color:#c0392b;font-size:13px">
    If you see persistent connection errors, the portal layout may have changed.
    Open a debug screenshot from the latest CI run to diagnose.
  </p>

  <div style="background:#f8f9fa;padding:16px;border-radius:4px;margin-top:20px">
    <strong>Quick booking links:</strong><br>
    <a href="https://apply.admissions.uci.edu/portal/uci_uga_tours_prospect?tab=prospect_guidedtours">
      UCI Tour Registration</a><br>
    <a href="https://connect.admission.ucla.edu/portal/tours">UCLA Tour Registration</a>
  </div>

  <p style="color:#bbb;font-size:11px;margin-top:20px">
    Automated report — do not reply to this email.
  </p>
</body>
</html>
"""
    text = f"""Daily Tour Checker Report
Generated : {now}
Last check: {last_check}

UCI  (July 8, 2026)  | {_badge(uci_result.connected)}  | {_slot_text(uci_result)}
UCLA (July 9, 2026)  | {_badge(ucla_result.connected)}  | {_slot_text(ucla_result)}

System: {system_badge}

UCI  booking: https://apply.admissions.uci.edu/portal/uci_uga_tours_prospect?tab=prospect_guidedtours
UCLA booking: https://connect.admission.ucla.edu/portal/tours
"""
    if not _SG_KEY:
        logger.error("SENDGRID_API_KEY not set — skipping email")
        return
    try:
        message = Mail(
            from_email=_FROM_EMAIL,
            to_emails=_TO_EMAIL,
            subject=f"[UCIUCLA] Daily Report — {now[:10]}",
            html_content=html,
            plain_text_content=text,
        )
        for att in [
            _make_inline_attachment(uci_result.screenshot_july,  "uci_calendar"),
            _make_inline_attachment(ucla_result.screenshot_july, "ucla_calendar"),
        ]:
            if att:
                message.add_attachment(att)

        sg = SendGridAPIClient(_SG_KEY)
        resp = sg.send(message)
        logger.info("Daily report sent (status %s)", resp.status_code)
    except Exception as exc:
        logger.error("Daily report email failed: %s", exc)
