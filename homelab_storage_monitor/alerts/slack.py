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

        # Only show checks with problems (not OK)
        problem_checks = [c for c in result.check_results if c.status != Status.OK]

        if problem_checks:
            for check in problem_checks:
                check_emoji = self._get_emoji(check.status)

                # Build detailed message with impact
                details = check.details or {}
                issues = details.get("issues", [])
                warnings = details.get("warnings", [])

                # Main summary
                text_parts = [f"{check_emoji} *{check.name}*: {check.summary}"]

                # Add impact description based on check type
                impact = self._get_impact_description(check.name, check.status, details)
                if impact:
                    text_parts.append(f"_{impact}_")

                # Add specific issues/warnings
                if issues:
                    text_parts.append("*Issues:*")
                    for issue in issues[:5]:  # Limit to 5
                        text_parts.append(f"  • {issue}")

                if warnings:
                    text_parts.append("*Warnings:*")
                    for warning in warnings[:5]:  # Limit to 5
                        text_parts.append(f"  • {warning}")

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(text_parts),
                    },
                })
        else:
            # Test alert or all OK (shouldn't normally happen for alerts)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "✅ All checks passed" if not is_test else "This is a test alert - no actual issues.",
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

    def _get_impact_description(
        self,
        check_name: str,
        status: Status,
        details: dict[str, Any],
    ) -> str:
        """Get human-readable impact description for a check."""
        impacts = {
            "smart": {
                Status.CRIT: "Disk failure imminent - backup data immediately and replace drive",
                Status.WARN: "Disk showing early signs of wear - monitor closely and plan replacement",
            },
            "lvm": {
                Status.CRIT: "RAID array degraded - data redundancy compromised, replace failed drive ASAP",
                Status.WARN: "RAID sync in progress or minor issue detected",
            },
            "filesystem": {
                Status.CRIT: "Filesystem critically full - services may fail, free space immediately",
                Status.WARN: "Filesystem running low on space - plan cleanup or expansion",
            },
            "journal": {
                Status.CRIT: "Critical storage errors in system logs - immediate investigation required",
                Status.WARN: "Storage warnings detected in logs - review for potential issues",
            },
        }

        check_impacts = impacts.get(check_name, {})
        return check_impacts.get(status, "")

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


def send_ack_alert(
    config: SlackConfig,
    hostname: str,
    disk: str,
    error_count: int,
    note: str | None = None,
    dashboard_url: str | None = None,
) -> bool:
    """Send an acknowledgment notification to Slack."""
    if not config.webhook_url:
        return False

    note_text = f"\n*Note:* {note}" if note else ""

    payload = {
        "text": f"✅ *ACKNOWLEDGED* - SMART errors on {disk}",
        "attachments": [
            {
                "color": "#36a64f",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"✅ ACKNOWLEDGED - {hostname}",
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
                            "text": (
                                f"*Disk:* `{disk}`\n"
                                f"*Errors Acknowledged:* {error_count}\n"
                                f"*Status:* Marked as known issue - alerts suppressed{note_text}"
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
        logger.info(f"Slack ACK alert sent for {disk}")
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send Slack ACK alert: {e}")
        return False
