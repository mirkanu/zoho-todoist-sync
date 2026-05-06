"""Shared notification helpers.

Fire-and-forget email notifications via Resend (EDGE-6).
Centralised here so both todoist.writer and zoho.writer use the same
sender address and recipient without duplication.
"""
from __future__ import annotations

import resend

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


async def send_deletion_notification(subject: str, html: str) -> None:
    """Fire-and-forget Resend email. EDGE-6: failure logged, NOT re-raised."""
    try:
        settings = get_settings()
        params: resend.Emails.SendParams = {
            "from": settings.resend_sender_email,
            "to": ["manuelkuhs@gmail.com"],
            "subject": subject,
            "html": html,
        }
        await resend.Emails.send_async(params)
    except Exception as exc:
        log.error("resend_email_failed", error=str(exc))
        # Do NOT re-raise — EDGE-6.
