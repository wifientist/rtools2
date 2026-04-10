"""
Shared Slack webhook notification utility.

Provides a simple interface for sending messages to Slack via incoming webhooks.
Can be used by any feature that needs Slack notifications.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def send_slack_message(
    webhook_url: str,
    text: str,
    blocks: Optional[list[dict]] = None,
) -> bool:
    """
    Send a message to a Slack channel via an incoming webhook.

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
        logger.error("Slack webhook timed out: %s", webhook_url)
        return False
    except Exception as e:
        logger.error("Failed to send Slack message: %s", e)
        return False


def build_dfs_blacklist_blocks(
    channel: int,
    zone_name: str,
    threshold_type: str,
    event_count: int,
    backoff_hours: int,
    controller_name: str = "",
) -> list[dict]:
    """Build Slack Block Kit blocks for a DFS blacklist notification."""
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"DFS Blacklist Alert — Channel {channel}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Controller:*\n{controller_name or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Zone:*\n{zone_name}"},
                {"type": "mrkdwn", "text": f"*Threshold:*\n{threshold_type} ({event_count} events)"},
                {"type": "mrkdwn", "text": f"*Backoff:*\n{backoff_hours}h"},
            ],
        },
        {"type": "divider"},
    ]


def build_dfs_reentry_blocks(
    channel: int,
    zone_name: str,
    controller_name: str = "",
) -> list[dict]:
    """Build Slack Block Kit blocks for a channel re-entry notification."""
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"DFS Channel Re-enabled — Channel {channel}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Controller:*\n{controller_name or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Zone:*\n{zone_name}"},
            ],
        },
        {"type": "divider"},
    ]
