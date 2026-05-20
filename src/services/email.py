"""
Email delivery via Resend (https://resend.com).

Tolerant of missing configuration: if RESEND_API_KEY is not set, calls log a
warning and return False rather than raising. This keeps the rest of the app
functional during local development without email credentials.
"""
import logging
import os
from typing import List, Optional, Sequence, Union

import httpx

logger = logging.getLogger("milo-orchestrator.email")

RESEND_API_URL = "https://api.resend.com/emails"


def _api_key() -> Optional[str]:
    return os.getenv("RESEND_API_KEY", "").strip() or None


def _from_address() -> str:
    return os.getenv("EMAIL_FROM", "Milo <onboarding@resend.dev>").strip()


def frontend_base_url() -> str:
    return os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


async def send_email(
    *,
    to: Union[str, Sequence[str]],
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> bool:
    """Send a single transactional email via Resend.

    Returns True if delivered (HTTP 2xx), False otherwise. Never raises.
    """
    api_key = _api_key()
    if not api_key:
        logger.warning("RESEND_API_KEY not set; skipping email send to %s.", to)
        return False

    recipients: List[str] = [to] if isinstance(to, str) else list(to)
    if not recipients:
        return False

    payload = {
        "from": _from_address(),
        "to": recipients,
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 200 and response.status_code < 300:
            logger.info("Email sent to %s (subject=%r).", recipients, subject)
            return True
        logger.error(
            "Resend returned %s for email to %s: %s",
            response.status_code,
            recipients,
            response.text[:300],
        )
        return False
    except Exception:
        logger.exception("Failed to send email to %s.", recipients)
        return False


def render_button_email(
    *,
    headline: str,
    body_html: str,
    cta_label: str,
    cta_url: str,
    footer: str = "Milo — Metacognitive Coach",
) -> str:
    """Minimal inline-styled HTML email with a primary CTA button."""
    return f"""\
<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background:#f4f8f6;font-family:'Segoe UI',Tahoma,Arial,sans-serif;color:#11312b;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f8f6;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width:520px;background:#ffffff;border:1px solid #d8e8e3;border-radius:14px;padding:32px;">
          <tr>
            <td>
              <h1 style="margin:0 0 16px;font-size:1.4rem;color:#136d56;">{headline}</h1>
              <div style="font-size:0.95rem;line-height:1.5;color:#11312b;">{body_html}</div>
              <div style="margin:28px 0;">
                <a href="{cta_url}"
                   style="display:inline-block;background:#1f8f73;color:#ffffff;text-decoration:none;
                          padding:12px 22px;border-radius:10px;font-weight:600;">
                  {cta_label}
                </a>
              </div>
              <p style="margin:24px 0 0;font-size:0.8rem;color:#4a6c65;">{footer}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
