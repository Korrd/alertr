"""LVM RAID health check."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from typing import Any

from homelab_storage_monitor.checks.base import BaseCheck
from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import CheckResult, Metric, Status

logger = logging.getLogger(__name__)


class LvmCheck(BaseCheck):
    """Check LVM RAID1 mirror health and sync status."""

    name = "lvm_raid"

    def __init__(self, config: Config, db: Database):
        super().__init__(config, db)
        self._metrics: list[Metric] = []

    def run(self) -> list[CheckResult]:
        """Execute LVM RAID health check."""
        self._metrics = []

        if not self.config.lvm.enabled:
            return []

        vg = self.config.lvm.vg
        lv = self.config.lvm.lv

        try:
            lv_data = self._get_lv_info()
        except Exception as e:
            logger.exception("Failed to get LVM info")
            return [
                CheckResult(
                    name=self.name,
                    status=Status.UNKNOWN,
                    summary=f"Failed to query LVM: {e}",
                    details={"error": str(e)},
                    identifier=f"{vg}/{lv}",
                )
            ]

        # Find our target LV
        target_lv = self._find_target_lv(lv_data, vg, lv)

        if target_lv is None:
            return [
                CheckResult(
                    name=self.name,
                    status=Status.CRIT,
                    summary=f"LV {vg}/{lv} not found",
                    details={"vg": vg, "lv": lv, "available_lvs": lv_data},
                    identifier=f"{vg}/{lv}",
                )
            ]

        return [self._analyze_lv(target_lv, vg, lv)]

    def get_metrics(self) -> list[Metric]:
        """Return metrics from last check."""
        return self._metrics

    def _get_lv_info(self) -> list[dict[str, Any]]:
        """Query LVM for logical volume information."""
        cmd = [
            "lvs", "-a",
            "-o", "vg_name,lv_name,segtype,lv_attr,copy_percent,devices,lv_health_status",
            "--reportformat", "json",
            "--units", "b",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # lvs returns non-zero with empty stderr when no LVM is configured
        if result.returncode != 0:
            if result.stderr.strip():
                raise RuntimeError(f"lvs failed: {result.stderr}")
            # No LVM configured - return empty list
            logger.debug("No LVM volumes found (lvs returned non-zero with no error)")
            return []

        # Handle empty output
        if not result.stdout.strip():
            return []

        data = json.loads(result.stdout)
        return data.get("report", [{}])[0].get("lv", [])

    def _find_target_lv(
        self,
        lv_data: list[dict[str, Any]],
        vg: str,
        lv: str,
    ) -> dict[str, Any] | None:
        """Find the target LV in the lvs output."""
        for lv_info in lv_data:
            if lv_info.get("vg_name") == vg and lv_info.get("lv_name") == lv:
                return lv_info
        return None

    def _analyze_lv(
        self,
        lv_info: dict[str, Any],
        vg: str,
        lv: str,
    ) -> CheckResult:
        """Analyze LV health and sync status."""
        segtype = lv_info.get("segtype", "")
        lv_attr = lv_info.get("lv_attr", "")
        copy_percent_str = lv_info.get("copy_percent", "")
        lv_health = lv_info.get("lv_health_status", "")
        devices = lv_info.get("devices", "")

        details: dict[str, Any] = {
            "vg": vg,
            "lv": lv,
            "segtype": segtype,
            "lv_attr": lv_attr,
            "copy_percent": copy_percent_str,
            "lv_health": lv_health,
            "devices": devices,
        }

        # Check segment type
        if segtype not in ("raid1", "mirror"):
            return CheckResult(
                name=self.name,
                status=Status.CRIT,
                summary=f"LV {vg}/{lv} is not RAID1 (type: {segtype})",
                details=details,
                identifier=f"{vg}/{lv}",
            )

        # Parse copy_percent
        try:
            copy_percent = float(copy_percent_str) if copy_percent_str else 100.0
        except ValueError:
            copy_percent = 100.0

        # Record metrics
        labels = {"vg": vg, "lv": lv}
        self._metrics.append(
            Metric(name="lvm_sync_pct", value_num=copy_percent, labels=labels)
        )

        # Check for degraded state
        # lv_attr position 8 (0-indexed): 'p' = partial (missing PVs)
        # lv_health: "partial" means degraded
        is_degraded = False
        degraded_reason = ""

        if len(lv_attr) > 8 and lv_attr[8] == "p":
            is_degraded = True
            degraded_reason = "partial (missing PV)"

        if lv_health and lv_health != "":
            is_degraded = True
            degraded_reason = lv_health

        # Also check if rimage count suggests degradation
        # (would need more complex parsing of devices field)

        self._metrics.append(
            Metric(
                name="lvm_degraded",
                value_num=1.0 if is_degraded else 0.0,
                labels=labels,
            )
        )

        details["is_degraded"] = is_degraded
        details["degraded_reason"] = degraded_reason

        # Determine status
        if is_degraded:
            return CheckResult(
                name=self.name,
                status=Status.CRIT,
                summary=f"LV {vg}/{lv} is DEGRADED: {degraded_reason}",
                details=details,
                identifier=f"{vg}/{lv}",
            )

        if copy_percent < 100.0:
            # Check for stalled sync
            stall_runs = self.config.lvm.sync_stall_runs
            is_stalled = self._check_sync_stall(vg, lv, copy_percent, stall_runs)

            # Save current sync pct for future stall detection
            self.db.save_sync_pct(vg, lv, copy_percent)

            if is_stalled:
                return CheckResult(
                    name=self.name,
                    status=Status.CRIT,
                    summary=f"LV {vg}/{lv} sync STALLED at {copy_percent:.1f}%",
                    details={**details, "stalled": True, "stall_runs": stall_runs},
                    identifier=f"{vg}/{lv}",
                )

            return CheckResult(
                name=self.name,
                status=Status.WARN,
                summary=f"LV {vg}/{lv} syncing: {copy_percent:.1f}%",
                details=details,
                identifier=f"{vg}/{lv}",
            )

        # All good
        return CheckResult(
            name=self.name,
            status=Status.OK,
            summary=f"LV {vg}/{lv} healthy (RAID1, 100% synced)",
            details=details,
            identifier=f"{vg}/{lv}",
        )

    def _check_sync_stall(
        self,
        vg: str,
        lv: str,
        current_pct: float,
        stall_runs: int,
    ) -> bool:
        """Check if sync has stalled (same percentage for N runs)."""
        recent_pcts = self.db.get_recent_sync_pcts(vg, lv, limit=stall_runs)

        if len(recent_pcts) < stall_runs - 1:
            # Not enough history
            return False

        # Check if all recent values are the same as current
        return all(abs(p - current_pct) < 0.01 for p in recent_pcts)
