"""Slack alerting backend."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

from homelab_storage_monitor.config import SlackConfig
from homelab_storage_monitor.models import RunResult, Status

logger = logging.getLogger(__name__)


class SlackAlerter:
    """Send alerts to Slack via incoming webhook."""

    def __init__(self, config: SlackConfig):
        self.config = config
        self.webhook_url = config.webhook_url

    def send(
        self,
        result: RunResult,
        is_test: bool = False,
        dashboard_url: str | None = None,
    ) -> bool:
        """
        Send alert to Slack.

        Returns True if successful.
        """
        if not self.webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        payload = self._build_payload(result, is_test, dashboard_url)

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            logger.info("Slack alert sent successfully")
            return True

        except requests.RequestException as e:
            logger.error(f"Failed to send Slack alert: {e}")
            return False

    def _build_payload(
        self,
        result: RunResult,
        is_test: bool,
        dashboard_url: str | None,
    ) -> dict[str, Any]:
        """Build Slack message payload."""
        status = result.overall_status
        emoji = self._get_emoji(status)
        color = self._get_color(status)

        # Header
        if is_test:
            header = f"{emoji} *TEST ALERT* - Homelab Storage Monitor"
        else:
            header = f"{emoji} *[{status}]* Homelab Storage Monitor - {result.hostname}"

        # Build check summary list
        check_lines = []
        for check in result.check_results:
            check_emoji = self._get_emoji(check.status)
            check_lines.append(f"{check_emoji} *{check.name}*: {check.summary}")

        # Build blocks
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{'🧪 TEST: ' if is_test else ''}{status} - {result.hostname}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Timestamp:* {result.ts_end.strftime('%Y-%m-%d %H:%M:%S')}",
                },
            },
            {"type": "divider"},
        ]

        # Add check results
        for check in result.check_results:
            check_emoji = self._get_emoji(check.status)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{check_emoji} *{check.name}*\n{check.summary}",
                },
            })

        # Add dashboard link if provided
        if dashboard_url:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{dashboard_url}|View Dashboard>",
                },
            })

        return {
            "text": header,  # Fallback for notifications
            "attachments": [
                {
                    "color": color,
                    "blocks": blocks,
                }
            ],
        }

    def _get_emoji(self, status: Status) -> str:
        """Get emoji for status."""
        return {
            Status.OK: "✅",
            Status.WARN: "⚠️",
            Status.CRIT: "🚨",
            Status.UNKNOWN: "❓",
        }.get(status, "❓")

    def _get_color(self, status: Status) -> str:
        """Get color code for status."""
        return {
            Status.OK: "#36a64f",  # Green
            Status.WARN: "#daa038",  # Orange/Yellow
            Status.CRIT: "#cc0000",  # Red
            Status.UNKNOWN: "#808080",  # Gray
        }.get(status, "#808080")


def send_recovery_alert(
    config: SlackConfig,
    hostname: str,
    recovered_checks: list[str],
    dashboard_url: str | None = None,
) -> bool:
    """Send a recovery notification."""
    if not config.webhook_url:
        return False

    payload = {
        "text": f"✅ *RECOVERY* - {hostname}",
        "attachments": [
            {
                "color": "#36a64f",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"✅ RECOVERY - {hostname}",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Timestamp:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Recovered issues:\n" + "\n".join(
                                f"• {check}" for check in recovered_checks
                            ),
                        },
                    },
                ],
            }
        ],
    }

    if dashboard_url:
        payload["attachments"][0]["blocks"].append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<{dashboard_url}|View Dashboard>",
            },
        })

    try:
        response = requests.post(config.webhook_url, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send Slack recovery alert: {e}")
        return False
