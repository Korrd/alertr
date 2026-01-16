"""Configuration loading and validation."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class LvmConfig:
    """LVM RAID monitoring configuration."""

    enabled: bool = True
    vg: str = "RAID"
    lv: str = "RAID"
    sync_stall_runs: int = 6  # CRIT if sync % unchanged for this many runs


@dataclass
class MountpointConfig:
    """Single mountpoint configuration."""

    path: str
    warn_pct: float = 85.0
    crit_pct: float = 95.0


@dataclass
class SmartThresholds:
    """SMART attribute thresholds."""

    realloc_warn: int = 10  # Attribute 5: Reallocated Sector Count
    crc_warn_delta: int = 1  # Attribute 199: CRC Error Count delta
    pending_crit: int = 0  # Attribute 197: Current Pending Sector
    offline_uncorr_crit: int = 0  # Attribute 198: Offline Uncorrectable
    reported_uncorr_crit: int = 0  # Attribute 187: Reported Uncorrectable


@dataclass
class SmartConfig:
    """SMART monitoring configuration."""

    enabled: bool = True
    disks: list[str] = field(default_factory=list)
    thresholds: SmartThresholds = field(default_factory=SmartThresholds)


@dataclass
class JournalConfig:
    """Journal/log scanning configuration."""

    enabled: bool = True
    use_journald: bool = True
    fallback_log_paths: list[str] = field(
        default_factory=lambda: ["/var/log/kern.log", "/var/log/syslog"]
    )


@dataclass
class FilesystemConfig:
    """Filesystem monitoring configuration."""

    enabled: bool = True
    mountpoints: list[MountpointConfig] = field(default_factory=list)


@dataclass
class HistoryConfig:
    """Database and history configuration."""

    db_path: str = "/var/lib/hsm/hsm.sqlite"
    retention_days_metrics: int = 90
    retention_days_events: int = 180


@dataclass
class SlackConfig:
    """Slack alerting configuration."""

    enabled: bool = False
    webhook_url: str = ""


@dataclass
class EmailConfig:
    """Email alerting configuration."""

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)
    use_starttls: bool = True
    use_ssl: bool = False


@dataclass
class AlertsConfig:
    """Alerting configuration."""

    dedupe_cooldown_seconds: int = 21600  # 6 hours
    send_recovery: bool = True
    slack: SlackConfig = field(default_factory=SlackConfig)
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""

    interval_seconds: int = 900  # 15 minutes


@dataclass
class DashboardConfig:
    """Dashboard configuration."""

    base_url: str = "http://localhost:8088"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8088
    auth_enabled: bool = False
    auth_username: str = "admin"
    auth_password: str = ""  # Empty = disabled
    auth_token: str = ""  # Bearer token alternative


@dataclass
class TargetConfig:
    """Target host identification."""

    hostname_label: str = ""

    def get_hostname(self) -> str:
        """Get hostname label or actual hostname."""
        return self.hostname_label or socket.gethostname()


@dataclass
class Config:
    """Root configuration."""

    target: TargetConfig = field(default_factory=TargetConfig)
    lvm: LvmConfig = field(default_factory=LvmConfig)
    smart: SmartConfig = field(default_factory=SmartConfig)
    journal: JournalConfig = field(default_factory=JournalConfig)
    filesystem: FilesystemConfig = field(default_factory=FilesystemConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Create Config from dictionary."""
        config = cls()

        # Target
        if "target" in data:
            config.target = TargetConfig(
                hostname_label=data["target"].get("hostname_label", "")
            )

        # LVM
        if "lvm" in data:
            lvm_data = data["lvm"]
            config.lvm = LvmConfig(
                enabled=lvm_data.get("enabled", True),
                vg=lvm_data.get("vg", "RAID"),
                lv=lvm_data.get("lv", "RAID"),
                sync_stall_runs=lvm_data.get("sync_stall_runs", 6),
            )

        # SMART
        if "smart" in data:
            smart_data = data["smart"]
            thresholds_data = smart_data.get("thresholds", {})
            config.smart = SmartConfig(
                enabled=smart_data.get("enabled", True),
                disks=smart_data.get("disks", []),
                thresholds=SmartThresholds(
                    realloc_warn=thresholds_data.get("realloc_warn", 10),
                    crc_warn_delta=thresholds_data.get("crc_warn_delta", 1),
                    pending_crit=thresholds_data.get("pending_crit", 0),
                    offline_uncorr_crit=thresholds_data.get("offline_uncorr_crit", 0),
                    reported_uncorr_crit=thresholds_data.get("reported_uncorr_crit", 0),
                ),
            )

        # Journal
        if "journal" in data:
            journal_data = data["journal"]
            config.journal = JournalConfig(
                enabled=journal_data.get("enabled", True),
                use_journald=journal_data.get("use_journald", True),
                fallback_log_paths=journal_data.get(
                    "fallback_log_paths", ["/var/log/kern.log", "/var/log/syslog"]
                ),
            )

        # Filesystem
        if "filesystem" in data:
            fs_data = data["filesystem"]
            mountpoints = []
            for mp in fs_data.get("mountpoints", []):
                if isinstance(mp, str):
                    mountpoints.append(MountpointConfig(path=mp))
                else:
                    mountpoints.append(
                        MountpointConfig(
                            path=mp["path"],
                            warn_pct=mp.get("warn_pct", 85.0),
                            crit_pct=mp.get("crit_pct", 95.0),
                        )
                    )
            config.filesystem = FilesystemConfig(
                enabled=fs_data.get("enabled", True),
                mountpoints=mountpoints,
            )

        # History
        if "history" in data:
            hist_data = data["history"]
            config.history = HistoryConfig(
                db_path=hist_data.get("db_path", "/var/lib/hsm/hsm.sqlite"),
                retention_days_metrics=hist_data.get("retention_days_metrics", 90),
                retention_days_events=hist_data.get("retention_days_events", 180),
            )

        # Alerts
        if "alerts" in data:
            alerts_data = data["alerts"]
            slack_data = alerts_data.get("slack", {})
            email_data = alerts_data.get("email", {})

            config.alerts = AlertsConfig(
                dedupe_cooldown_seconds=alerts_data.get("dedupe_cooldown_seconds", 21600),
                send_recovery=alerts_data.get("send_recovery", True),
                slack=SlackConfig(
                    enabled=slack_data.get("enabled", False),
                    webhook_url=slack_data.get("webhook_url", ""),
                ),
                email=EmailConfig(
                    enabled=email_data.get("enabled", False),
                    smtp_host=email_data.get("smtp_host", ""),
                    smtp_port=email_data.get("smtp_port", 587),
                    username=email_data.get("username", ""),
                    password=email_data.get("password", ""),
                    from_addr=email_data.get("from_addr", ""),
                    to_addrs=email_data.get("to_addrs", []),
                    use_starttls=email_data.get("use_starttls", True),
                    use_ssl=email_data.get("use_ssl", False),
                ),
            )

        # Scheduler
        if "scheduler" in data:
            sched_data = data["scheduler"]
            config.scheduler = SchedulerConfig(
                interval_seconds=sched_data.get("interval_seconds", 900),
            )

        # Dashboard
        if "dashboard" in data:
            dash_data = data["dashboard"]
            config.dashboard = DashboardConfig(
                base_url=dash_data.get("base_url", "http://localhost:8088"),
                bind_host=dash_data.get("bind_host", "0.0.0.0"),
                bind_port=dash_data.get("bind_port", 8088),
                auth_enabled=dash_data.get("auth_enabled", False),
                auth_username=dash_data.get("auth_username", "admin"),
                auth_password=dash_data.get("auth_password", ""),
                auth_token=dash_data.get("auth_token", ""),
            )

        return config

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        """Load configuration from YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data)

    @classmethod
    def from_env(cls) -> Config:
        """Create config from environment variables (for container use)."""
        config = cls()

        # Override common settings from environment
        if db_path := os.environ.get("HSM_DB_PATH"):
            config.history.db_path = db_path

        if hostname := os.environ.get("HSM_HOSTNAME"):
            config.target.hostname_label = hostname

        if webhook := os.environ.get("HSM_SLACK_WEBHOOK"):
            config.alerts.slack.enabled = True
            config.alerts.slack.webhook_url = webhook

        return config


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from file or defaults."""
    if path:
        return Config.from_yaml(path)

    # Check common locations
    for candidate in [
        Path("/config/config.yaml"),
        Path("/etc/hsm/config.yaml"),
        Path("config.yaml"),
    ]:
        if candidate.exists():
            return Config.from_yaml(candidate)

    # Fall back to defaults with env overrides
    return Config.from_env()
