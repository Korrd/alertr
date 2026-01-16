"""SMART disk health check."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any

from homelab_storage_monitor.checks.base import BaseCheck
from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import CheckResult, Metric, Status

logger = logging.getLogger(__name__)

# Key SMART attributes to monitor
# ID: (name, critical_if_nonzero, description)
SMART_ATTRS = {
    5: ("Reallocated_Sector_Ct", False, "Reallocated sectors"),
    187: ("Reported_Uncorrect", True, "Reported uncorrectable errors"),
    188: ("Command_Timeout", False, "Command timeouts"),
    197: ("Current_Pending_Sector", True, "Pending sector count"),
    198: ("Offline_Uncorrectable", True, "Offline uncorrectable sectors"),
    199: ("UDMA_CRC_Error_Count", False, "CRC errors (cabling)"),
}


class SmartCheck(BaseCheck):
    """Check SMART health status for configured disks."""

    name = "smart"

    def __init__(self, config: Config, db: Database):
        super().__init__(config, db)
        self._metrics: list[Metric] = []

    def run(self) -> list[CheckResult]:
        """Check all configured disks."""
        self._metrics = []

        if not self.config.smart.enabled:
            return []

        if not self.config.smart.disks:
            logger.debug("No disks configured for SMART monitoring")
            return []

        results: list[CheckResult] = []

        for disk in self.config.smart.disks:
            result = self._check_disk(disk)
            results.append(result)

        return results

    def get_metrics(self) -> list[Metric]:
        """Return metrics from last check."""
        return self._metrics

    def _check_disk(self, disk: str) -> CheckResult:
        """Check a single disk's SMART status."""
        try:
            smart_data = self._get_smart_data(disk)
        except Exception as e:
            logger.warning(f"Failed to get SMART data for {disk}: {e}")
            return CheckResult(
                name=self.name,
                status=Status.UNKNOWN,
                summary=f"{disk}: failed to read SMART data",
                details={"disk": disk, "error": str(e)},
                identifier=disk,
            )

        return self._analyze_smart(disk, smart_data)

    def _get_smart_data(self, disk: str) -> dict[str, Any]:
        """Query smartctl for disk information."""
        # Include -i for device info, -l selftest for test results, -l error for error log
        cmd = ["smartctl", "-i", "-H", "-A", "-l", "selftest", "-l", "error", "-j", disk]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # smartctl returns non-zero for various warnings, parse output anyway
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            # Fall back to text parsing if JSON mode not available
            return self._parse_smartctl_text(result.stdout, disk)

    def _parse_smartctl_text(self, output: str, disk: str) -> dict[str, Any]:
        """Parse traditional smartctl text output."""
        data: dict[str, Any] = {
            "device": {"name": disk},
            "smart_status": {"passed": True},
            "ata_smart_attributes": {"table": []},
        }

        # Check for PASSED/FAILED
        if "PASSED" in output:
            data["smart_status"]["passed"] = True
        elif "FAILED" in output:
            data["smart_status"]["passed"] = False

        # Parse attribute table
        # Format: ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
        attr_pattern = re.compile(
            r"^\s*(\d+)\s+(\S+)\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(\d+)"
        )

        for line in output.split("\n"):
            match = attr_pattern.match(line)
            if match:
                attr_id = int(match.group(1))
                data["ata_smart_attributes"]["table"].append({
                    "id": attr_id,
                    "name": match.group(2),
                    "value": int(match.group(3)),
                    "worst": int(match.group(4)),
                    "thresh": int(match.group(5)),
                    "raw": {"value": int(match.group(6))},
                })

        return data

    def _extract_device_info(self, smart_data: dict[str, Any]) -> dict[str, Any]:
        """Extract device information from smartctl output."""
        info: dict[str, Any] = {}

        # Model name - check multiple fields
        model = smart_data.get("model_name", "")
        if not model:
            model = smart_data.get("model_family", "")
        info["model"] = model

        # Model family (brand/series)
        info["family"] = smart_data.get("model_family", "")

        # Serial number
        info["serial"] = smart_data.get("serial_number", "")

        # Firmware version
        info["firmware"] = smart_data.get("firmware_version", "")

        # Capacity - use user_capacity for readable form
        user_cap = smart_data.get("user_capacity", {})
        if isinstance(user_cap, dict):
            info["capacity_bytes"] = user_cap.get("bytes", 0)
            info["capacity"] = user_cap.get("bytes", 0)
        else:
            info["capacity_bytes"] = user_cap
            info["capacity"] = user_cap

        # Format capacity as human readable
        if info.get("capacity_bytes"):
            bytes_val = info["capacity_bytes"]
            if bytes_val >= 1e12:
                info["capacity_human"] = f"{bytes_val / 1e12:.1f} TB"
            elif bytes_val >= 1e9:
                info["capacity_human"] = f"{bytes_val / 1e9:.1f} GB"
            else:
                info["capacity_human"] = f"{bytes_val / 1e6:.1f} MB"
        else:
            info["capacity_human"] = "Unknown"

        # Form factor
        info["form_factor"] = smart_data.get("form_factor", {}).get("name", "")

        # Rotation rate (0 = SSD)
        rotation = smart_data.get("rotation_rate", 0)
        info["rotation_rate"] = rotation
        info["is_ssd"] = rotation == 0

        # SATA version
        sata = smart_data.get("sata_version", {})
        if isinstance(sata, dict):
            info["interface"] = sata.get("string", "")
        else:
            info["interface"] = str(sata) if sata else ""

        # ATA version
        info["ata_version"] = smart_data.get("ata_version", {}).get("string", "")

        # SMART support
        info["smart_supported"] = smart_data.get("smart_support", {}).get("available", False)
        info["smart_enabled"] = smart_data.get("smart_support", {}).get("enabled", False)

        # Power on hours (from attributes or directly)
        info["power_on_hours"] = smart_data.get("power_on_time", {}).get("hours", 0)

        # Device type
        info["device_type"] = smart_data.get("device", {}).get("type", "")

        return info

    def _extract_selftest_results(self, smart_data: dict[str, Any]) -> dict[str, Any]:
        """Extract self-test log results from smartctl output."""
        results: dict[str, Any] = {
            "tests": [],
            "last_short": None,
            "last_long": None,
            "error_count": 0,
            "has_errors": False,
            "test_count": 0,
        }

        # Get current power-on hours for calculating hours_ago
        current_poh = 0
        power_on = smart_data.get("power_on_time", {})
        if isinstance(power_on, dict):
            current_poh = power_on.get("hours", 0)

        # Get self-test log (ATA style)
        selftest_log = smart_data.get("ata_smart_self_test_log", {})
        if selftest_log:
            standard = selftest_log.get("standard", {})
            test_table = standard.get("table", [])
            results["test_count"] = len(test_table)

            for test in test_table[:10]:  # Keep last 10 tests
                lifetime_hours = test.get("lifetime_hours", 0)
                hours_ago = current_poh - lifetime_hours if current_poh > 0 else None

                test_entry = {
                    "type": test.get("type", {}).get("string", "Unknown"),
                    "status": test.get("status", {}).get("string", "Unknown"),
                    "passed": test.get("status", {}).get("passed", True),
                    "remaining_percent": test.get("status", {}).get("remaining_percent", 0),
                    "lifetime_hours": lifetime_hours,
                    "hours_ago": hours_ago,
                }
                results["tests"].append(test_entry)

                # Track last short and long tests
                test_type = test_entry["type"].lower()
                if "short" in test_type and results["last_short"] is None:
                    results["last_short"] = test_entry
                elif ("extended" in test_type or "long" in test_type) and results["last_long"] is None:
                    results["last_long"] = test_entry

                if not test_entry["passed"]:
                    results["has_errors"] = True

        # Get error log count (ATA style)
        error_log = smart_data.get("ata_smart_error_log", {})
        if error_log:
            summary = error_log.get("summary", {})
            results["error_count"] = summary.get("count", 0)
            if results["error_count"] > 0:
                results["has_errors"] = True

        # NVMe style - check for error log entries
        nvme_health = smart_data.get("nvme_smart_health_information_log", {})
        if nvme_health:
            err_entries = nvme_health.get("num_err_log_entries", 0)
            results["error_count"] = err_entries
            if err_entries > 0:
                results["has_errors"] = True

        return results

    def _parse_temperature(self, raw_value: int, attr_id: int) -> int:
        """Extract temperature from SMART raw value.

        Temperature attributes often pack current temp in lowest byte,
        with min/max/lifetime temps in higher bytes.
        """
        # Temperature is typically in the lowest byte
        temp = raw_value & 0xFF

        # Sanity check - temps should be reasonable (0-100°C range typically)
        if 0 <= temp <= 100:
            return temp

        # Some drives use different encoding, try second byte
        temp2 = (raw_value >> 8) & 0xFF
        if 0 <= temp2 <= 100:
            return temp2

        # If still unreasonable, return raw value masked
        return raw_value & 0xFF

    def _parse_spinup_time(self, raw_value: int) -> int:
        """Extract spin-up time from SMART raw value.

        Spin-up time raw value format varies by manufacturer:
        - Some pack current time in lower 16 bits, average in upper
        - Some use lower 16 bits for milliseconds
        - Values should typically be 0-30000 ms range
        """
        # Try lower 16 bits first (most common)
        spinup = raw_value & 0xFFFF

        # Sanity check - spin-up should be reasonable (0-60 seconds)
        if 0 <= spinup <= 60000:
            return spinup

        # Try lower 8 bits
        spinup8 = raw_value & 0xFF
        if 0 <= spinup8 <= 255:
            return spinup8

        # Return as-is if nothing works
        return raw_value

    def _analyze_smart(self, disk: str, smart_data: dict[str, Any]) -> CheckResult:
        """Analyze SMART data and determine health status."""
        thresholds = self.config.smart.thresholds
        issues: list[str] = []
        warnings: list[str] = []

        # Extract device info
        device_info = self._extract_device_info(smart_data)

        # Extract self-test results
        selftest_results = self._extract_selftest_results(smart_data)

        details: dict[str, Any] = {
            "disk": disk,
            "device_info": device_info,
            "selftest": selftest_results,
            "issues": [],
            "warnings": [],
            "attributes": {},
        }

        # Store device info as metrics for the dashboard
        labels = {"disk": disk}
        if device_info.get("model"):
            self._metrics.append(
                Metric(name="disk_info", value_text=json.dumps(device_info), labels=labels)
            )

        # Store self-test results as metric
        self._metrics.append(
            Metric(name="disk_selftest", value_text=json.dumps(selftest_results), labels=labels)
        )

        # Check for self-test failures
        if selftest_results["has_errors"]:
            if selftest_results["error_count"] > 0:
                warnings.append(f"Error log has {selftest_results['error_count']} entries")
            for test in selftest_results["tests"]:
                if not test["passed"]:
                    issues.append(f"Self-test failed: {test['type']} - {test['status']}")
                    break  # Only report first failure

        # Check overall SMART status
        smart_status = smart_data.get("smart_status", {})
        overall_passed = smart_status.get("passed", True)

        labels = {"disk": disk}
        self._metrics.append(
            Metric(
                name="smart_overall_pass",
                value_num=1.0 if overall_passed else 0.0,
                labels=labels,
            )
        )

        if not overall_passed:
            issues.append("SMART overall health: FAILED")

        # Get last known values for delta detection
        last_attrs = self.db.get_last_smart_attrs(disk)
        current_attrs: dict[int, int] = {}

        # Parse attributes
        attr_table = smart_data.get("ata_smart_attributes", {}).get("table", [])
        # Also check NVMe style
        if not attr_table:
            nvme_health = smart_data.get("nvme_smart_health_information_log", {})
            if nvme_health:
                return self._analyze_nvme(disk, nvme_health, overall_passed, smart_data)

        for attr in attr_table:
            attr_id = attr.get("id")
            if attr_id is None:
                continue

            raw_value = attr.get("raw", {}).get("value", 0)
            if isinstance(raw_value, str):
                # Sometimes raw value has additional text
                raw_value = int(raw_value.split()[0]) if raw_value else 0

            # Parse temperature attributes specially (190, 194)
            # Parse spin-up time (3) specially
            display_value = raw_value
            if attr_id in (190, 194):
                display_value = self._parse_temperature(raw_value, attr_id)
            elif attr_id == 3:
                display_value = self._parse_spinup_time(raw_value)

            current_attrs[attr_id] = raw_value

            # Record metric (use display_value for specially parsed attrs)
            metric_value = display_value
            self._metrics.append(
                Metric(
                    name="smart_attr_raw",
                    value_num=float(metric_value),
                    labels={**labels, "attr": str(attr_id)},
                )
            )

            details["attributes"][attr_id] = {
                "name": attr.get("name", f"attr_{attr_id}"),
                "raw": raw_value,
                "display_value": metric_value,
                "value": attr.get("value"),
                "worst": attr.get("worst"),
                "thresh": attr.get("thresh"),
            }

            # Check thresholds
            if attr_id == 5:  # Reallocated Sector Count
                if raw_value > thresholds.realloc_warn:
                    warnings.append(f"Reallocated sectors: {raw_value}")
                # Check for increase
                if attr_id in last_attrs and raw_value > last_attrs[attr_id]:
                    delta = raw_value - last_attrs[attr_id]
                    issues.append(f"Reallocated sectors increased by {delta}")

            elif attr_id == 187:  # Reported Uncorrectable
                if raw_value > thresholds.reported_uncorr_crit:
                    issues.append(f"Uncorrectable errors: {raw_value}")

            elif attr_id == 197:  # Current Pending Sector
                if raw_value > thresholds.pending_crit:
                    issues.append(f"Pending sectors: {raw_value}")

            elif attr_id == 198:  # Offline Uncorrectable
                if raw_value > thresholds.offline_uncorr_crit:
                    issues.append(f"Offline uncorrectable: {raw_value}")

            elif attr_id == 199:  # UDMA CRC Error Count
                if attr_id in last_attrs:
                    delta = raw_value - last_attrs[attr_id]
                    if delta >= thresholds.crc_warn_delta:
                        warnings.append(f"CRC errors increased by {delta} (cabling issue?)")

        # Save current attrs for future delta detection
        if current_attrs:
            self.db.save_smart_attrs(disk, current_attrs)

        details["issues"] = issues
        details["warnings"] = warnings

        # Determine overall status
        if issues:
            return CheckResult(
                name=self.name,
                status=Status.CRIT,
                summary=f"{disk}: {issues[0]}" + (f" (+{len(issues)-1} more)" if len(issues) > 1 else ""),
                details=details,
                identifier=disk,
            )

        if warnings:
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                summary=f"{disk}: {warnings[0]}" + (f" (+{len(warnings)-1} more)" if len(warnings) > 1 else ""),
                details=details,
                identifier=disk,
            )

        return CheckResult(
            name=self.name,
            status=Status.OK,
            summary=f"{disk}: SMART healthy",
            details=details,
            identifier=disk,
        )

    def _analyze_nvme(
        self,
        disk: str,
        nvme_health: dict[str, Any],
        overall_passed: bool,
        smart_data: dict[str, Any],
    ) -> CheckResult:
        """Analyze NVMe health information."""
        issues: list[str] = []
        warnings: list[str] = []
        labels = {"disk": disk}

        # Extract and store device info
        device_info = self._extract_device_info(smart_data)
        if device_info.get("model"):
            self._metrics.append(
                Metric(name="disk_info", value_text=json.dumps(device_info), labels=labels)
            )

        details: dict[str, Any] = {
            "disk": disk,
            "type": "nvme",
            "device_info": device_info,
            "issues": [],
            "warnings": [],
            "health": nvme_health,
        }

        # Store NVMe health values as "smart_attr_raw" metrics with special IDs
        # We use IDs 1000+ to differentiate from standard SMART attributes
        # This allows the dashboard to display them in the same grid

        # Temperature (often in nvme health log)
        temp = nvme_health.get("temperature", 0)
        if temp:
            self._metrics.append(
                Metric(name="smart_attr_raw", value_num=float(temp), labels={**labels, "attr": "1001"})
            )

        # Percentage used (wear level)
        pct_used = nvme_health.get("percentage_used", 0)
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(pct_used), labels={**labels, "attr": "1002"})
        )
        if pct_used >= 100:
            warnings.append(f"NVMe wear: {pct_used}% used")
        elif pct_used >= 90:
            warnings.append(f"NVMe wear high: {pct_used}% used")

        # Available spare
        spare = nvme_health.get("available_spare", 100)
        spare_threshold = nvme_health.get("available_spare_threshold", 10)
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(spare), labels={**labels, "attr": "1003"})
        )
        if spare < spare_threshold:
            issues.append(f"NVMe spare below threshold: {spare}%")

        # Media errors
        media_errors = nvme_health.get("media_errors", 0)
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(media_errors), labels={**labels, "attr": "1004"})
        )
        if media_errors > 0:
            issues.append(f"NVMe media errors: {media_errors}")

        # Power on hours
        power_on_hours = nvme_health.get("power_on_hours", 0)
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(power_on_hours), labels={**labels, "attr": "1005"})
        )

        # Power cycles
        power_cycles = nvme_health.get("power_cycles", 0)
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(power_cycles), labels={**labels, "attr": "1006"})
        )

        # Unsafe shutdowns
        unsafe_shutdowns = nvme_health.get("unsafe_shutdowns", 0)
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(unsafe_shutdowns), labels={**labels, "attr": "1007"})
        )

        # Data units written (in 512KB units, convert to GB)
        data_written = nvme_health.get("data_units_written", 0)
        data_written_gb = (data_written * 512 * 1000) / 1e9  # Convert to GB
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(int(data_written_gb)), labels={**labels, "attr": "1008"})
        )

        # Data units read (in 512KB units, convert to GB)
        data_read = nvme_health.get("data_units_read", 0)
        data_read_gb = (data_read * 512 * 1000) / 1e9  # Convert to GB
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(int(data_read_gb)), labels={**labels, "attr": "1009"})
        )

        # Critical warning flags
        critical_warning = nvme_health.get("critical_warning", 0)
        self._metrics.append(
            Metric(name="smart_attr_raw", value_num=float(critical_warning), labels={**labels, "attr": "1010"})
        )
        if critical_warning != 0:
            issues.append(f"NVMe critical warning: {critical_warning}")

        if not overall_passed:
            issues.insert(0, "SMART overall health: FAILED")

        details["issues"] = issues
        details["warnings"] = warnings

        if issues:
            return CheckResult(
                name=self.name,
                status=Status.CRIT,
                summary=f"{disk}: {issues[0]}",
                details=details,
                identifier=disk,
            )

        if warnings:
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                summary=f"{disk}: {warnings[0]}",
                details=details,
                identifier=disk,
            )

        return CheckResult(
            name=self.name,
            status=Status.OK,
            summary=f"{disk}: NVMe healthy",
            details=details,
            identifier=disk,
        )
