"""Push notifications via ntfy (ntfy.sh or self-hosted) when an issue becomes
available in the library.

Title/message are sent as query parameters rather than headers: ntfy supports
both, but HTTP header values must be Latin-1/ASCII, which real publication
titles this app already handles (e.g. "Le Monde Diplomatique", "Août") would
violate. `requests` percent-encodes unicode query params automatically.
"""

import logging
from pathlib import Path

import requests

from kioskarr.covers import get_or_generate_cover
from kioskarr.models import AppSettings, Issue, Publication

logger = logging.getLogger(__name__)

_APP_TITLE = "Kioskarr"
_TIMEOUT = 15.0


def send_notification(
    app_settings: AppSettings, title: str, message: str, attachment_path: Path | None = None
) -> None:
    """Raises on failure — used by the test-notification endpoint, which wants
    to surface real errors to the person configuring it."""
    url = f"{app_settings.ntfy_url.rstrip('/')}/{app_settings.ntfy_topic}"
    headers = {}
    if app_settings.ntfy_token:
        headers["Authorization"] = f"Bearer {app_settings.ntfy_token}"

    if attachment_path is not None:
        params = {"title": title, "message": message, "filename": attachment_path.name}
        with open(attachment_path, "rb") as f:
            requests.put(url, params=params, data=f, headers=headers, timeout=_TIMEOUT).raise_for_status()
    else:
        params = {"title": title, "message": message}
        requests.post(url, params=params, headers=headers, timeout=_TIMEOUT).raise_for_status()


def notify_issue_available(app_settings: AppSettings, issue: Issue, publication: Publication) -> None:
    """Never raises — a failed/misconfigured notification must never break an
    import, same philosophy as covers.get_or_generate_cover."""
    if not app_settings.ntfy_configured:
        return

    message = f"{publication.title} - {issue.identifier} available!"
    cover_path = get_or_generate_cover(issue)
    try:
        send_notification(app_settings, _APP_TITLE, message, cover_path)
    except Exception:
        logger.exception("Failed to send ntfy notification for issue %s", issue.id)
