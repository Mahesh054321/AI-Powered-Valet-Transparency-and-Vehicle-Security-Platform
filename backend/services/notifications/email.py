"""Email notification channel with SMTP delivery, templating, and audit tracking.

Surfaces security alerts to customers and management over email when configured.
If SMTP configuration is unavailable, retains the alert in the database and records
an explanatory failure ledger entry without interrupting the alert workflow.
"""

from __future__ import annotations

import email.utils
import logging
import os
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.extensions import db
from models.audit_log import AuditAction, AuditLog, AuditOutcome
from models.notification import Notification, NotificationChannel, NotificationStatus
from models.user import User
from services.notifications.base import BaseChannel, DeliveryResult, NotificationPayload

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class EmailChannel(BaseChannel):
    """SMTP email channel supporting customer and manager notifications."""

    name = NotificationChannel.EMAIL

    def __init__(self) -> None:
        self.reload_config()

    def reload_config(self) -> None:
        self.smtp_host = os.getenv("SMTP_HOST", "").strip()
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "").strip()
        self.smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
        self.smtp_from = os.getenv("SMTP_FROM", "alerts@valet.local").strip()
        self.smtp_use_tls = _env_bool("SMTP_USE_TLS", True)
        self.mock_mode = (
            self.smtp_host.lower() in {"mock", "test", "demo"}
            or _env_bool("EMAIL_MOCK_DISPATCH", False)
        )
        self.enabled = bool(self.smtp_host or self.mock_mode)

    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_host or self.mock_mode)

    def describe(self) -> dict[str, Any]:
        return {
            "channel": self.name.value,
            "enabled": self.enabled,
            "configured": self.is_configured,
            "host": self.smtp_host if self.smtp_host else None,
            "mock_mode": self.mock_mode,
            "from_address": self.smtp_from,
        }

    def send(self, payload: NotificationPayload) -> DeliveryResult:
        """Deliver email notification for a security alert."""
        self.reload_config()

        # Check configuration first
        if not self.is_configured or not self.enabled:
            reason = (
                "Email delivery is not configured: SMTP_HOST environment variable is not set. "
                "Alert is retained in PostgreSQL."
            )
            return DeliveryResult.failed(self.name.value, reason)

        # Look up recipient user
        recipient = db.session.get(User, payload.user_id) if payload.user_id else None
        recipient_email = payload.context.get("recipient_email") or (recipient.email if recipient else None)
        recipient_name = payload.context.get("recipient_name") or (recipient.full_name if recipient else "Valet Platform User")

        # Check user notification preferences
        if recipient and recipient.notification_preferences:
            prefs = recipient.notification_preferences
            if prefs.get("email_enabled") is False:
                logger.info("Email notification skipped: user %s disabled email alerts", payload.user_id)
                return DeliveryResult.failed(
                    self.name.value,
                    f"Email notifications disabled in user preferences for {recipient.email}",
                )

        if not recipient_email:
            reason = "No recipient email address could be resolved"
            self._record_notification(
                payload,
                status=NotificationStatus.FAILED,
                failure_reason=reason,
            )
            return DeliveryResult.failed(self.name.value, reason)

        # Build email content
        subject, text_body, html_body = self._build_email_message(payload, recipient_name)
        provider_id = f"valet-mail-{uuid.uuid4()}"

        if self.mock_mode:
            logger.info(
                "[MOCK EMAIL] To: %s <%s> | Subject: %s | Body preview: %s",
                recipient_name,
                recipient_email,
                subject,
                payload.body[:120],
            )
            self._record_notification(
                payload,
                status=NotificationStatus.DELIVERED,
                provider_id=provider_id,
            )
            self._record_audit(payload, recipient_email, outcome=AuditOutcome.SUCCESS)
            return DeliveryResult.ok(self.name.value, provider_message_id=provider_id)

        # Live SMTP delivery
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.smtp_from
            msg["To"] = email.utils.formataddr((recipient_name, recipient_email))
            msg["Date"] = email.utils.formatdate(localtime=True)
            msg["Message-ID"] = email.utils.make_msgid(domain="valet.local")

            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=8.0) as server:
                if self.smtp_use_tls:
                    server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            self._record_notification(
                payload,
                status=NotificationStatus.DELIVERED,
                provider_id=provider_id,
            )
            self._record_audit(payload, recipient_email, outcome=AuditOutcome.SUCCESS)
            return DeliveryResult.ok(self.name.value, provider_message_id=provider_id)

        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send alert email to %s: %s", recipient_email, exc)
            failure_reason = f"SMTP error ({type(exc).__name__}): {exc}"
            self._record_notification(
                payload,
                status=NotificationStatus.FAILED,
                failure_reason=failure_reason[:500],
            )
            self._record_audit(payload, recipient_email, outcome=AuditOutcome.FAILURE, notes=str(exc))
            return DeliveryResult.failed(self.name.value, failure_reason)

    def send_test_email(self, to_email: str, actor_id: str | None = None) -> DeliveryResult:
        """Send a test email to verify SMTP configuration and server connectivity."""
        self.reload_config()
        if not self.is_configured:
            return DeliveryResult.failed(
                self.name.value,
                "SMTP is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD in environment."
            )
        frontend_base = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
        payload = NotificationPayload(
            user_id=actor_id or str(uuid.uuid4()),
            title="Valet Platform SMTP Test",
            body="This is an automated test email confirming your email configuration is operating correctly.",
            severity="LOW",
            context={
                "vehicle_info": "Test Configuration Check",
                "zone_name": "Valet Central",
                "recipient_email": to_email,
                "recipient_name": "Valet Administrator",
                "tracking_url": f"{frontend_base}/status",
            },
        )
        return self.send(payload)

    def _record_notification(
        self,
        payload: NotificationPayload,
        status: NotificationStatus,
        provider_id: str | None = None,
        failure_reason: str | None = None,
    ) -> Notification | None:
        try:
            notification = Notification(
                alert_id=uuid.UUID(payload.alert_id) if payload.alert_id else None,
                user_id=uuid.UUID(payload.user_id),
                channel=self.name.value,
                title=payload.title[:200],
                body=payload.body[:1000],
                status=status.value,
                delivered_at=datetime.now(timezone.utc) if status == NotificationStatus.DELIVERED else None,
                provider_message_id=provider_id,
                failure_reason=failure_reason[:500] if failure_reason else None,
                metadata_=payload.context or None,
            )
            db.session.add(notification)
            db.session.flush()
            return notification
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist notification ledger row: %s", exc)
            return None

    def _record_audit(
        self,
        payload: NotificationPayload,
        recipient_email: str,
        outcome: AuditOutcome,
        notes: str | None = None,
    ) -> None:
        try:
            action = (
                AuditAction.EMAIL_DISPATCHED
                if outcome == AuditOutcome.SUCCESS
                else AuditAction.EMAIL_DISPATCH_FAILED
            )
            log = AuditLog(
                actor_id=uuid.UUID(payload.user_id) if payload.user_id else None,
                action=action.value,
                entity_type="Notification",
                entity_id=uuid.UUID(payload.alert_id) if payload.alert_id else None,
                outcome=outcome.value,
                notes=(
                    f"Email alert '{payload.title}' to {recipient_email}. {notes or ''}"
                )[:500],
                metadata_={
                    "channel": "EMAIL",
                    "recipient": recipient_email,
                    "title": payload.title,
                    "severity": payload.severity,
                },
            )
            db.session.add(log)
            db.session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not record email audit log: %s", exc)

    def _build_email_message(
        self, payload: NotificationPayload, recipient_name: str
    ) -> tuple[str, str, str]:
        severity = payload.severity or "ALERT"
        title = payload.title
        body = payload.body
        context = payload.context or {}

        vehicle_info = context.get("vehicle_info") or "Assigned Vehicle"
        zone_name = context.get("zone_name") or "Authorized Safe Zone"
        session_id = context.get("custody_session_id") or "N/A"
        event_time = context.get("event_time") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        frontend_base = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
        raw_tracking = context.get("tracking_url") or "/tracking"
        tracking_url = f"{frontend_base}{raw_tracking}" if raw_tracking.startswith("/") else raw_tracking
        location_str = context.get("location_str") or "Not provided"

        subject = f"[Valet Security Alert - {severity}] {title} ({vehicle_info})"

        text_body = f"""Valet Transparency & Vehicle Security Alert
==================================================

Dear {recipient_name},

A vehicle security alert has been triggered for your vehicle.

ALERT DETAILS:
- Severity: {severity}
- Event: {title}
- Vehicle: {vehicle_info}
- Zone: {zone_name}
- Custody Session: {session_id}
- Time: {event_time}
- Location: {location_str}

REASON:
{body}

ACTION REQUIRED:
Please review live vehicle tracking immediately at:
{tracking_url}

AI-Powered Valet Transparency and Vehicle Security Platform
"""

        severity_color = {
            "LOW": "#3b82f6",
            "MEDIUM": "#eab308",
            "HIGH": "#f97316",
            "CRITICAL": "#ef4444",
        }.get(severity, "#ef4444")

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #1f2937; margin: 0; padding: 24px; background: #f3f4f6; }}
    .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e5e7eb; overflow: hidden; }}
    .header {{ background: #111827; color: #ffffff; padding: 20px; }}
    .header h1 {{ margin: 0; font-size: 18px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-weight: bold; font-size: 13px; color: #ffffff; background: {severity_color}; margin-top: 8px; }}
    .content {{ padding: 24px; }}
    .alert-box {{ background: #fef2f2; border-left: 4px solid {severity_color}; padding: 14px; margin: 16px 0; border-radius: 0 4px 4px 0; }}
    .table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
    .table td {{ padding: 8px 0; border-bottom: 1px solid #f3f4f6; }}
    .table td:first-child {{ color: #6b7280; font-weight: 500; width: 35%; }}
    .button {{ display: inline-block; background: #2563eb; color: #ffffff !important; padding: 12px 24px; border-radius: 6px; text-decoration: none; font-weight: 600; margin: 16px 0; }}
    .footer {{ padding: 16px 24px; background: #f9fafb; border-top: 1px solid #e5e7eb; font-size: 12px; color: #6b7280; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>AI-Powered Valet Vehicle Security</h1>
      <span class="badge">{severity} SEVERITY</span>
    </div>
    <div class="content">
      <p>Hello <strong>{recipient_name}</strong>,</p>
      <div class="alert-box">
        <strong>{title}</strong>
        <p style="margin: 6px 0 0 0;">{body}</p>
      </div>
      <table class="table">
        <tr><td>Vehicle</td><td><strong>{vehicle_info}</strong></td></tr>
        <tr><td>Assigned Safe Zone</td><td>{zone_name}</td></tr>
        <tr><td>Event Timestamp</td><td>{event_time}</td></tr>
        <tr><td>Position / Coordinates</td><td>{location_str}</td></tr>
        <tr><td>Custody Session</td><td><code>{session_id}</code></td></tr>
      </table>
      <div style="text-align: center;">
        <a href="{tracking_url}" class="button">View Live Vehicle Tracking</a>
      </div>
      <p style="font-size: 13px; color: #6b7280;">If you did not authorize vehicle movement or need immediate assistance, please contact the valet desk manager immediately.</p>
    </div>
    <div class="footer">
      AI-Powered Valet Transparency and Vehicle Security Platform &bull; Automated Security Service
    </div>
  </div>
</body>
</html>"""

        return subject, text_body, html_body


__all__ = ["EmailChannel"]
