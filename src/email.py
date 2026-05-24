"""Optional email delivery via Resend. Failure never raises."""
from __future__ import annotations

import logging

import markdown as md_lib
import requests

log = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT = 15


def send_digest(
    markdown_text: str,
    cfg: dict,
    week_label: str,
    api_key: str | None,
) -> bool:
    """Send the digest via Resend. Returns True on success, False otherwise."""
    if not cfg.get("enabled"):
        return False
    if not api_key:
        log.warning("Email enabled but no RESEND_API_KEY available; skipping send")
        return False

    subject_template = cfg.get("subject_template", "AI Digest — {week_label}")
    subject = subject_template.format(week_label=week_label)

    html = md_lib.markdown(markdown_text, extensions=["extra"])

    body = {
        "from": cfg.get("from", ""),
        "to": cfg.get("to", []),
        "subject": subject,
        "html": html,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(_RESEND_ENDPOINT, json=body, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Resend send failed: %s", e)
        return False
