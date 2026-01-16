"""Email alerting backend."""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from homelab_storage_monitor.config import EmailConfig
from homelab_storage_monitor.models import RunResult, Status

logger = logging.getLogger(__name__)


class EmailAlerter:
    """Send alerts via email."""

    def __init__(self, config: EmailConfig):
        self.config = config

    def send(
        self,
        result: RunResult,
        is_test: bool = False,
        dashboard_url: str | None = None,
    ) -> bool:
        """
        Send alert email.

        Returns True if successful.
        """
        if not self._is_configured():
            logger.warning("Email not fully configured")
            return False

        subject = self._build_subject(result, is_test)
        body_text, body_html = self._build_body(result, is_test, dashboard_url)

        try:
            self._send_email(subject, body_text, body_html)
            logger.info(f"Email alert sent to {len(self.config.to_addrs)} recipients")
            return True

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            return False

    def _is_configured(self) -> bool:
        """Check if email is properly configured."""
        return bool(
            self.config.smtp_host
            and self.config.from_addr
            and self.config.to_addrs
        )

    def _build_subject(self, result: RunResult, is_test: bool) -> str:
        """Build email subject line."""
        status = result.overall_status
        prefix = "[TEST] " if is_test else ""

        return f"{prefix}[{status}] homelab-storage-monitor - {result.hostname}"

    def _build_body(
        self,
        result: RunResult,
        is_test: bool,
        dashboard_url: str | None,
    ) -> tuple[str, str]:
        """Build email body (text and HTML versions)."""
        status = result.overall_status
        timestamp = result.ts_end.strftime("%Y-%m-%d %H:%M:%S")

        # Plain text version
        text_lines = [
            f"{'TEST ALERT - ' if is_test else ''}Homelab Storage Monitor Alert",
            "",
            f"Hostname: {result.hostname}",
            f"Status: {status}",
            f"Timestamp: {timestamp}",
            "",
            "Check Results:",
            "-" * 40,
        ]

        for check in result.check_results:
            text_lines.append(f"[{check.status}] {check.name}")
            text_lines.append(f"    {check.summary}")
            text_lines.append("")

        # Add next actions hints for problems
        problem_checks = [c for c in result.check_results if c.status.is_problem()]
        if problem_checks:
            text_lines.extend([
                "-" * 40,
                "Suggested Actions:",
                "",
            ])
            for check in problem_checks:
                hints = self._get_action_hints(check.name, check.details)
                for hint in hints:
                    text_lines.append(f"• {hint}")

        if dashboard_url:
            text_lines.extend([
                "",
                "-" * 40,
                f"Dashboard: {dashboard_url}",
            ])

        text_body = "\n".join(text_lines)

        # HTML version
        status_color = {
            Status.OK: "#28a745",
            Status.WARN: "#ffc107",
            Status.CRIT: "#dc3545",
            Status.UNKNOWN: "#6c757d",
        }.get(status, "#6c757d")

        html_checks = []
        for check in result.check_results:
            check_color = {
                Status.OK: "#28a745",
                Status.WARN: "#ffc107",
                Status.CRIT: "#dc3545",
                Status.UNKNOWN: "#6c757d",
            }.get(check.status, "#6c757d")

            html_checks.append(f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ddd;">
                        <span style="color: {check_color}; font-weight: bold;">[{check.status}]</span>
                        {check.name}
                    </td>
                    <td style="padding: 8px; border-bottom: 1px solid #ddd;">
                        {check.summary}
                    </td>
                </tr>
            """)

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: {status_color}; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
                .content {{ background: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px; }}
                table {{ width: 100%; border-collapse: collapse; }}
                th {{ text-align: left; padding: 8px; background: #e9ecef; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2 style="margin: 0;">{'🧪 TEST: ' if is_test else ''}{status} - {result.hostname}</h2>
                    <p style="margin: 10px 0 0 0;">Homelab Storage Monitor</p>
                </div>
                <div class="content">
                    <p><strong>Timestamp:</strong> {timestamp}</p>

                    <h3>Check Results</h3>
                    <table>
                        <tr>
                            <th>Check</th>
                            <th>Summary</th>
                        </tr>
                        {''.join(html_checks)}
                    </table>
        """

        if problem_checks:
            hints_html = []
            for check in problem_checks:
                hints = self._get_action_hints(check.name, check.details)
                for hint in hints:
                    hints_html.append(f"<li>{hint}</li>")

            html_body += f"""
                    <h3>Suggested Actions</h3>
                    <ul>
                        {''.join(hints_html)}
                    </ul>
            """

        if dashboard_url:
            html_body += f"""
                    <p style="margin-top: 20px;">
                        <a href="{dashboard_url}" style="background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">
                            View Dashboard
                        </a>
                    </p>
            """

        html_body += """
                </div>
            </div>
        </body>
        </html>
        """

        return text_body, html_body

    def _get_action_hints(self, check_name: str, details: dict) -> list[str]:
        """Get suggested actions for a check (non-destructive only)."""
        hints: list[str] = []

        if check_name == "lvm_raid":
            if details.get("is_degraded"):
                hints.append("Check physical drive status with: lsblk, smartctl")
                hints.append("Review LVM status: lvs -a -o+devices")
                hints.append("Check dmesg for disk errors")
            elif details.get("stalled"):
                hints.append("Sync may be blocked by I/O issues")
                hints.append("Check system load and disk health")

        elif check_name == "smart":
            disk = details.get("disk", "disk")
            hints.append(f"Review full SMART data: smartctl -a {disk}")
            if details.get("issues"):
                hints.append("Consider scheduling disk replacement")
                hints.append("Ensure backups are current")

        elif check_name == "filesystem":
            path = details.get("path", "mount")
            hints.append(f"Check large files/dirs: du -sh {path}/*")
            hints.append("Review and clean old logs, caches, temp files")

        elif check_name == "journal":
            hints.append("Review kernel logs: journalctl -k -p err")
            hints.append("Check dmesg for recent errors")
            hints.append("Inspect disk and cabling")

        return hints or ["Review system logs and hardware status"]

    def _send_email(self, subject: str, text_body: str, html_body: str) -> None:
        """Send the email via SMTP."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.config.from_addr
        msg["To"] = ", ".join(self.config.to_addrs)

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        # Create SSL context
        context = ssl.create_default_context()

        if self.config.use_ssl:
            # Direct SSL connection (port 465)
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                context=context,
            ) as server:
                if self.config.username and self.config.password:
                    server.login(self.config.username, self.config.password)
                server.sendmail(
                    self.config.from_addr,
                    self.config.to_addrs,
                    msg.as_string(),
                )
        else:
            # Standard connection with optional STARTTLS
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                server.ehlo()
                if self.config.use_starttls:
                    server.starttls(context=context)
                    server.ehlo()
                if self.config.username and self.config.password:
                    server.login(self.config.username, self.config.password)
                server.sendmail(
                    self.config.from_addr,
                    self.config.to_addrs,
                    msg.as_string(),
                )


def send_recovery_email(
    config: EmailConfig,
    hostname: str,
    recovered_checks: list[str],
    dashboard_url: str | None = None,
) -> bool:
    """Send a recovery notification email."""
    if not config.smtp_host or not config.from_addr or not config.to_addrs:
        return False

    subject = f"[OK] homelab-storage-monitor - {hostname} - Recovery"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    text_body = f"""Homelab Storage Monitor - Recovery

Hostname: {hostname}
Status: OK (Recovered)
Timestamp: {timestamp}

Recovered Issues:
{chr(10).join(f'• {check}' for check in recovered_checks)}

{f'Dashboard: {dashboard_url}' if dashboard_url else ''}
"""

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: sans-serif;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: #28a745; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h2 style="margin: 0;">✅ RECOVERY - {hostname}</h2>
            </div>
            <div style="background: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px;">
                <p><strong>Timestamp:</strong> {timestamp}</p>
                <h3>Recovered Issues</h3>
                <ul>
                    {''.join(f'<li>{check}</li>' for check in recovered_checks)}
                </ul>
                {f'<p><a href="{dashboard_url}">View Dashboard</a></p>' if dashboard_url else ''}
            </div>
        </div>
    </body>
    </html>
    """

    try:
        alerter = EmailAlerter(config)
        alerter._send_email(subject, text_body, html_body)
        return True
    except Exception as e:
        logger.error(f"Failed to send recovery email: {e}")
        return False
