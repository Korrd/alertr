"""Check runner - orchestrates all checks and alerting."""

from __future__ import annotations

import logging
from datetime import datetime

from homelab_storage_monitor.alerts.email import EmailAlerter
from homelab_storage_monitor.alerts.slack import SlackAlerter
from homelab_storage_monitor.checks.filesystem import FilesystemCheck
from homelab_storage_monitor.checks.journal import JournalCheck
from homelab_storage_monitor.checks.lvm import LvmCheck
from homelab_storage_monitor.checks.smart import SmartCheck
from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import CheckResult, Metric, RunResult, Status
from homelab_storage_monitor.state import StateManager

logger = logging.getLogger(__name__)


class Runner:
    """Orchestrates check execution, metrics collection, and alerting."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.state_manager = StateManager(config, db)

        # Initialize checks
        self.checks = [
            LvmCheck(config, db),
            FilesystemCheck(config, db),
            SmartCheck(config, db),
            JournalCheck(config, db),
        ]

        # Initialize alerters
        self.slack_alerter = (
            SlackAlerter(config.alerts.slack)
            if config.alerts.slack.enabled
            else None
        )
        self.email_alerter = (
            EmailAlerter(config.alerts.email)
            if config.alerts.email.enabled
            else None
        )

    def run_checks(self) -> RunResult:
        """Execute all checks and process results."""
        ts_start = datetime.now()
        all_results: list[CheckResult] = []
        all_metrics: list[Metric] = []

        # Run each check
        for check in self.checks:
            try:
                results = check.run()
                all_results.extend(results)

                # Collect metrics
                metrics = check.get_metrics()
                all_metrics.extend(metrics)

            except Exception as e:
                logger.exception(f"Check {check.name} failed: {e}")
                all_results.append(
                    CheckResult(
                        name=check.name,
                        status=Status.UNKNOWN,
                        summary=f"Check failed: {e}",
                        details={"error": str(e)},
                    )
                )

        ts_end = datetime.now()

        # Build run result
        run_result = RunResult(
            hostname=self.config.target.get_hostname(),
            ts_start=ts_start,
            ts_end=ts_end,
            check_results=all_results,
        )

        # Save to database
        self._save_results(run_result, all_metrics)

        # Process alerts
        self._process_alerts(run_result)

        return run_result

    def _save_results(self, run: RunResult, metrics: list[Metric]) -> None:
        """Save run results and metrics to database."""
        try:
            self.db.save_run(run)
        except Exception as e:
            logger.error(f"Failed to save run: {e}")

        try:
            if metrics:
                self.db.save_metrics(metrics)
        except Exception as e:
            logger.error(f"Failed to save metrics: {e}")

    def _process_alerts(self, run: RunResult) -> None:
        """Process check results through state manager and send alerts."""
        results_to_alert: list[CheckResult] = []
        recovery_results: list[CheckResult] = []

        for result in run.check_results:
            should_alert, reason = self.state_manager.process_result(result)

            if should_alert:
                if reason == "recovery":
                    recovery_results.append(result)
                else:
                    results_to_alert.append(result)

        # Send problem alerts
        if results_to_alert:
            self._send_alerts(run, results_to_alert)

        # Send recovery alerts if enabled
        if recovery_results and self.config.alerts.send_recovery:
            self._send_recovery_alerts(run.hostname, recovery_results)

    def _send_alerts(self, run: RunResult, results: list[CheckResult]) -> None:
        """Send alerts for problem results."""
        # Create a run result with only the alertable checks for context
        # but include all results for full picture
        dashboard_url = self.config.dashboard.base_url

        # Slack
        if self.slack_alerter:
            try:
                success = self.slack_alerter.send(run, dashboard_url=dashboard_url)
                self.state_manager.record_alert_sent(results, "slack", success)
            except Exception as e:
                logger.error(f"Slack alert failed: {e}")
                self.state_manager.record_alert_sent(results, "slack", False)

        # Email
        if self.email_alerter:
            try:
                success = self.email_alerter.send(run, dashboard_url=dashboard_url)
                self.state_manager.record_alert_sent(results, "email", success)
            except Exception as e:
                logger.error(f"Email alert failed: {e}")
                self.state_manager.record_alert_sent(results, "email", False)

    def _send_recovery_alerts(
        self,
        hostname: str,
        results: list[CheckResult],
    ) -> None:
        """Send recovery notifications."""
        recovered_names = [f"{r.name}: {r.identifier or 'global'}" for r in results]
        dashboard_url = self.config.dashboard.base_url

        # Slack recovery
        if self.slack_alerter:
            from homelab_storage_monitor.alerts.slack import send_recovery_alert

            try:
                send_recovery_alert(
                    self.config.alerts.slack,
                    hostname,
                    recovered_names,
                    dashboard_url,
                )
            except Exception as e:
                logger.error(f"Slack recovery alert failed: {e}")

        # Email recovery
        if self.email_alerter:
            from homelab_storage_monitor.alerts.email import send_recovery_email

            try:
                send_recovery_email(
                    self.config.alerts.email,
                    hostname,
                    recovered_names,
                    dashboard_url,
                )
            except Exception as e:
                logger.error(f"Email recovery alert failed: {e}")
