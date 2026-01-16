"""CLI entrypoints for homelab storage monitor."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import click

from homelab_storage_monitor import __version__
from homelab_storage_monitor.config import Config, load_config
from homelab_storage_monitor.db import Database


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.group()
@click.version_option(version=__version__, prog_name="hsm")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Homelab Storage Monitor - disk array monitoring and alerting."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@main.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file",
)
@click.option(
    "--loop",
    is_flag=True,
    help="Run continuously on schedule",
)
@click.pass_context
def run(ctx: click.Context, config: Path | None, loop: bool) -> None:
    """Run storage health checks."""
    from homelab_storage_monitor.runner import Runner

    cfg = load_config(config)
    db = Database(cfg.history.db_path)
    runner = Runner(cfg, db)

    logger = logging.getLogger("hsm.run")

    if loop:
        logger.info(
            f"Starting collector loop (interval: {cfg.scheduler.interval_seconds}s)"
        )
        while True:
            try:
                result = runner.run_checks()
                logger.info(
                    f"Check complete: {result.overall_status} "
                    f"({len(result.check_results)} checks)"
                )
            except Exception as e:
                logger.exception(f"Check run failed: {e}")

            time.sleep(cfg.scheduler.interval_seconds)
    else:
        result = runner.run_checks()
        click.echo(f"Overall status: {result.overall_status}")
        for check in result.check_results:
            status_color = {
                "OK": "green",
                "WARN": "yellow",
                "CRIT": "red",
                "UNKNOWN": "white",
            }.get(str(check.status), "white")
            click.secho(f"  [{check.status}] ", fg=status_color, nl=False)
            click.echo(f"{check.name}: {check.summary}")

        # Exit with appropriate code
        if result.overall_status.value == "CRIT":
            sys.exit(2)
        elif result.overall_status.value == "WARN":
            sys.exit(1)


@main.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file",
)
@click.option(
    "--bind",
    default=None,
    help="Bind address (host:port), overrides config",
)
@click.pass_context
def serve(ctx: click.Context, config: Path | None, bind: str | None) -> None:
    """Start the dashboard web server."""
    import uvicorn

    from homelab_storage_monitor.web.app import create_app

    cfg = load_config(config)
    app = create_app(cfg)

    host = cfg.dashboard.bind_host
    port = cfg.dashboard.bind_port

    if bind:
        if ":" in bind:
            host, port_str = bind.rsplit(":", 1)
            port = int(port_str)
        else:
            host = bind

    logger = logging.getLogger("hsm.serve")
    logger.info(f"Starting dashboard on {host}:{port}")

    uvicorn.run(app, host=host, port=port, log_level="info")


@main.command("test-alerts")
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file",
)
@click.option("--slack", is_flag=True, help="Test Slack alerting")
@click.option("--email", is_flag=True, help="Test email alerting")
@click.pass_context
def test_alerts(
    ctx: click.Context,
    config: Path | None,
    slack: bool,
    email: bool,
) -> None:
    """Send test alerts to verify configuration."""
    from homelab_storage_monitor.alerts.email import EmailAlerter
    from homelab_storage_monitor.alerts.slack import SlackAlerter
    from homelab_storage_monitor.models import CheckResult, RunResult, Status

    cfg = load_config(config)
    logger = logging.getLogger("hsm.test-alerts")

    # Create a fake test result
    test_result = RunResult(
        hostname=cfg.target.get_hostname(),
        ts_start=__import__("datetime").datetime.now(),
        ts_end=__import__("datetime").datetime.now(),
        check_results=[
            CheckResult(
                name="test_check",
                status=Status.WARN,
                summary="This is a test alert",
                details={"test": True},
            )
        ],
    )

    if not slack and not email:
        # Test all enabled backends
        slack = cfg.alerts.slack.enabled
        email = cfg.alerts.email.enabled

    if slack:
        if not cfg.alerts.slack.enabled or not cfg.alerts.slack.webhook_url:
            click.secho("Slack not configured", fg="yellow")
        else:
            try:
                alerter = SlackAlerter(cfg.alerts.slack)
                alerter.send(test_result, is_test=True)
                click.secho("Slack test sent successfully", fg="green")
            except Exception as e:
                click.secho(f"Slack test failed: {e}", fg="red")
                logger.exception("Slack test failed")

    if email:
        if not cfg.alerts.email.enabled or not cfg.alerts.email.smtp_host:
            click.secho("Email not configured", fg="yellow")
        else:
            try:
                alerter = EmailAlerter(cfg.alerts.email)
                alerter.send(test_result, is_test=True)
                click.secho("Email test sent successfully", fg="green")
            except Exception as e:
                click.secho(f"Email test failed: {e}", fg="red")
                logger.exception("Email test failed")


@main.command("migrate-db")
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file",
)
@click.pass_context
def migrate_db(ctx: click.Context, config: Path | None) -> None:
    """Initialize or migrate the database schema."""
    cfg = load_config(config)
    logger = logging.getLogger("hsm.migrate-db")

    db_path = Path(cfg.history.db_path)
    existed = db_path.exists()

    # Database constructor handles schema init/migration
    Database(cfg.history.db_path)

    if existed:
        click.secho(f"Database migrated: {db_path}", fg="green")
    else:
        click.secho(f"Database created: {db_path}", fg="green")


@main.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file",
)
@click.option("--vacuum", is_flag=True, help="Run VACUUM after cleanup")
@click.pass_context
def retention(ctx: click.Context, config: Path | None, vacuum: bool) -> None:
    """Clean up old data based on retention settings."""
    cfg = load_config(config)
    db = Database(cfg.history.db_path)

    deleted = db.run_retention(cfg)

    click.echo("Retention cleanup complete:")
    for table, count in deleted.items():
        click.echo(f"  {table}: {count} rows deleted")

    if vacuum:
        click.echo("Running VACUUM...")
        db.vacuum()
        click.secho("VACUUM complete", fg="green")


@main.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file",
)
@click.pass_context
def status(ctx: click.Context, config: Path | None) -> None:
    """Show current system status."""
    cfg = load_config(config)
    db = Database(cfg.history.db_path)

    latest = db.get_latest_run()

    if latest is None:
        click.secho("No check runs recorded yet", fg="yellow")
        return

    click.echo(f"Hostname: {latest['hostname']}")
    click.echo(f"Last run: {latest['ts_end']}")

    status_val = latest["overall_status"]
    status_color = {
        "OK": "green",
        "WARN": "yellow",
        "CRIT": "red",
        "UNKNOWN": "white",
    }.get(status_val, "white")
    click.secho(f"Status: {status_val}", fg=status_color)

    click.echo("\nCheck results:")
    for check in latest["check_results"]:
        check_color = {
            "OK": "green",
            "WARN": "yellow",
            "CRIT": "red",
            "UNKNOWN": "white",
        }.get(check["status"], "white")
        click.secho(f"  [{check['status']}] ", fg=check_color, nl=False)
        click.echo(f"{check['name']}: {check['summary']}")

    # Show open issues
    issues = db.get_open_issues()
    if issues:
        click.echo(f"\nOpen issues ({len(issues)}):")
        for issue in issues:
            click.secho(f"  [{issue['status']}] ", fg="red", nl=False)
            click.echo(f"{issue['key']} (since {issue['last_change_ts']})")


if __name__ == "__main__":
    main()
