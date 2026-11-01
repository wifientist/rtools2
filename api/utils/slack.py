"""
Shared Slack webhook notification utility.

Provides a simple interface for sending messages to Slack via incoming webhooks.
Can be used by any feature that needs Slack notifications.
"""
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Users supply these URLs and the server POSTs to them, so anything but a real
# Slack webhook would let a user aim the server at an internal address.
_WEBHOOK_URL = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+")


def is_slack_webhook_url(url: str) -> bool:
    """True only for a Slack incoming webhook URL."""
    return bool(_WEBHOOK_URL.fullmatch(url))


async def send_slack_message(
    webhook_url: str,
    text: str,
    blocks: Optional[list[dict]] = None,
) -> bool:
    """
    Send a message to a Slack channel via an incoming webhook.

    The URL is a credential, so nothing here may log it.

    Args:
        webhook_url: Slack incoming webhook URL.
        text: Fallback plain-text message (shown in notifications).
        blocks: Optional Block Kit blocks for rich formatting.

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    payload: dict = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 200 and resp.text == "ok":
                logger.info("Slack message sent successfully")
                return True
            logger.warning(
                "Slack webhook returned unexpected response: %s %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
    except httpx.TimeoutException:
        logger.error("Slack webhook timed out")
        return False
    except Exception as e:
        logger.error("Failed to send Slack message: %s", type(e).__name__)
        return False
