"""SMART attribute definitions with names, descriptions, and health importance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Importance(str, Enum):
    """Health importance level of a SMART attribute."""
    CRITICAL = "critical"  # Failure likely imminent
    HIGH = "high"          # Strong indicator of problems
    MEDIUM = "medium"      # Worth monitoring
    LOW = "low"            # Informational


@dataclass
class SmartAttr:
    """SMART attribute definition."""
    id: int
    name: str
    description: str
    importance: Importance
    higher_is_worse: bool = True  # Most error counts: higher = worse


# Comprehensive SMART attribute dictionary
SMART_ATTRS: dict[int, SmartAttr] = {
    1: SmartAttr(
        id=1,
        name="Read Error Rate",
        description="Rate of hardware read errors. Vendor-specific raw values; normalized value matters more.",
        importance=Importance.MEDIUM,
    ),
    2: SmartAttr(
        id=2,
        name="Throughput Performance",
        description="Overall throughput performance. Lower values may indicate degradation.",
        importance=Importance.LOW,
        higher_is_worse=False,
    ),
    3: SmartAttr(
        id=3,
        name="Spin-Up Time",
        description="Average time to spin up the drive. Increasing times may indicate motor issues.",
        importance=Importance.LOW,
    ),
    4: SmartAttr(
        id=4,
        name="Start/Stop Count",
        description="Number of spindle start/stop cycles. High counts on HDDs indicate wear.",
        importance=Importance.LOW,
    ),
    5: SmartAttr(
        id=5,
        name="Reallocated Sectors",
        description="Count of remapped sectors due to read errors. ANY non-zero value is concerning; rising values indicate imminent failure.",
        importance=Importance.CRITICAL,
    ),
    7: SmartAttr(
        id=7,
        name="Seek Error Rate",
        description="Rate of seek errors. Vendor-specific; may indicate head or platter issues.",
        importance=Importance.MEDIUM,
    ),
    8: SmartAttr(
        id=8,
        name="Seek Time Performance",
        description="Average seek time performance. Lower values indicate faster seeking.",
        importance=Importance.LOW,
        higher_is_worse=False,
    ),
    9: SmartAttr(
        id=9,
        name="Power-On Hours",
        description="Total hours the drive has been powered on. Useful for age tracking.",
        importance=Importance.LOW,
        higher_is_worse=False,  # Not an error, just informational
    ),
    10: SmartAttr(
        id=10,
        name="Spin Retry Count",
        description="Number of spin-up retries. Non-zero values indicate motor or power issues.",
        importance=Importance.HIGH,
    ),
    11: SmartAttr(
        id=11,
        name="Calibration Retry Count",
        description="Number of recalibration retries. Indicates head positioning issues.",
        importance=Importance.MEDIUM,
    ),
    12: SmartAttr(
        id=12,
        name="Power Cycle Count",
        description="Number of complete power on/off cycles.",
        importance=Importance.LOW,
    ),
    13: SmartAttr(
        id=13,
        name="Soft Read Error Rate",
        description="Uncorrected read errors reported to the OS.",
        importance=Importance.MEDIUM,
    ),
    183: SmartAttr(
        id=183,
        name="SATA Downshift Count",
        description="Number of times SATA link speed was reduced. May indicate cable or controller issues.",
        importance=Importance.MEDIUM,
    ),
    184: SmartAttr(
        id=184,
        name="End-to-End Error",
        description="Data integrity errors between cache and host. Critical data path issue.",
        importance=Importance.CRITICAL,
    ),
    187: SmartAttr(
        id=187,
        name="Reported Uncorrectable",
        description="Errors that could not be recovered using ECC. Direct indicator of data loss risk.",
        importance=Importance.CRITICAL,
    ),
    188: SmartAttr(
        id=188,
        name="Command Timeout",
        description="Number of aborted operations due to timeout. May indicate controller or connection issues.",
        importance=Importance.HIGH,
    ),
    189: SmartAttr(
        id=189,
        name="High Fly Writes",
        description="Number of writes where head was higher than expected. Indicates head/platter issues.",
        importance=Importance.HIGH,
    ),
    190: SmartAttr(
        id=190,
        name="Airflow Temperature",
        description="Drive temperature from airflow sensor. High temps reduce drive lifespan.",
        importance=Importance.MEDIUM,
    ),
    191: SmartAttr(
        id=191,
        name="G-Sense Error Rate",
        description="Shock/vibration events detected. High values on HDDs indicate physical stress.",
        importance=Importance.MEDIUM,
    ),
    192: SmartAttr(
        id=192,
        name="Power-Off Retract Count",
        description="Emergency head retracts due to power loss. Also called unsafe shutdown count. High values stress heads.",
        importance=Importance.MEDIUM,
    ),
    193: SmartAttr(
        id=193,
        name="Load/Unload Cycles",
        description="Head parking cycles. HDDs rated for limited cycles (typically 300K-600K).",
        importance=Importance.LOW,
    ),
    194: SmartAttr(
        id=194,
        name="Temperature",
        description="Current drive temperature in Celsius. Keep below 50°C for longevity.",
        importance=Importance.MEDIUM,
    ),
    195: SmartAttr(
        id=195,
        name="Hardware ECC Recovered",
        description="Errors corrected by hardware ECC. High rates may precede failure.",
        importance=Importance.MEDIUM,
    ),
    196: SmartAttr(
        id=196,
        name="Reallocate Event Count",
        description="Number of remap operations. Non-zero indicates sector failures occurred.",
        importance=Importance.CRITICAL,
    ),
    197: SmartAttr(
        id=197,
        name="Current Pending Sectors",
        description="Sectors waiting to be remapped. NON-ZERO IS BAD - indicates unreadable sectors.",
        importance=Importance.CRITICAL,
    ),
    198: SmartAttr(
        id=198,
        name="Offline Uncorrectable",
        description="Sectors that failed during offline scan. Cannot be read or remapped.",
        importance=Importance.CRITICAL,
    ),
    199: SmartAttr(
        id=199,
        name="UDMA CRC Error Count",
        description="Data transfer CRC errors. Usually indicates cable or connector problems, not drive failure.",
        importance=Importance.HIGH,
    ),
    200: SmartAttr(
        id=200,
        name="Multi-Zone Error Rate",
        description="Errors during multi-zone operations. Indicates firmware or head issues.",
        importance=Importance.MEDIUM,
    ),
    201: SmartAttr(
        id=201,
        name="Soft Read Error Rate",
        description="Off-track read errors. Indicates head alignment issues.",
        importance=Importance.HIGH,
    ),
    202: SmartAttr(
        id=202,
        name="Data Address Mark Errors",
        description="Errors finding data address marks. Indicates media or head issues.",
        importance=Importance.HIGH,
    ),
    220: SmartAttr(
        id=220,
        name="Disk Shift",
        description="Physical shift of platters. Indicates mechanical damage.",
        importance=Importance.CRITICAL,
    ),
    222: SmartAttr(
        id=222,
        name="Loaded Hours",
        description="Hours with heads loaded over platters.",
        importance=Importance.LOW,
    ),
    223: SmartAttr(
        id=223,
        name="Load/Unload Retry Count",
        description="Retries during head parking. Indicates mechanical wear.",
        importance=Importance.MEDIUM,
    ),
    224: SmartAttr(
        id=224,
        name="Load Friction",
        description="Resistance during head load. Indicates contamination or wear.",
        importance=Importance.HIGH,
    ),
    225: SmartAttr(
        id=225,
        name="Load/Unload Cycle Count",
        description="Alias for attribute 193.",
        importance=Importance.LOW,
    ),
    226: SmartAttr(
        id=226,
        name="Load-In Time",
        description="Time for head loading. Increasing values indicate issues.",
        importance=Importance.MEDIUM,
    ),
    227: SmartAttr(
        id=227,
        name="Torque Amplification",
        description="Extra torque needed for spindle. Indicates motor wear.",
        importance=Importance.HIGH,
    ),
    228: SmartAttr(
        id=228,
        name="Power-Off Retract Count",
        description="Emergency head retracts on power loss.",
        importance=Importance.MEDIUM,
    ),
    230: SmartAttr(
        id=230,
        name="Drive Life Protection",
        description="Remaining life indicator (some SSDs).",
        importance=Importance.HIGH,
        higher_is_worse=False,
    ),
    231: SmartAttr(
        id=231,
        name="SSD Life Left",
        description="Percentage of SSD lifespan remaining. Plan replacement below 10%.",
        importance=Importance.HIGH,
        higher_is_worse=False,
    ),
    232: SmartAttr(
        id=232,
        name="Endurance Remaining",
        description="SSD endurance remaining. Lower values mean less write capacity left.",
        importance=Importance.HIGH,
        higher_is_worse=False,
    ),
    233: SmartAttr(
        id=233,
        name="Media Wearout Indicator",
        description="SSD wear level. 100=new, 0=worn out.",
        importance=Importance.HIGH,
        higher_is_worse=False,
    ),
    234: SmartAttr(
        id=234,
        name="Average Erase Count",
        description="Average flash block erase count (SSDs).",
        importance=Importance.MEDIUM,
    ),
    235: SmartAttr(
        id=235,
        name="Good Block Count",
        description="Remaining good NAND blocks (SSDs).",
        importance=Importance.HIGH,
        higher_is_worse=False,
    ),
    240: SmartAttr(
        id=240,
        name="Head Flying Hours",
        description="Time heads spent over platters.",
        importance=Importance.LOW,
    ),
    241: SmartAttr(
        id=241,
        name="Total LBAs Written",
        description="Total data written to drive (in LBAs).",
        importance=Importance.LOW,
    ),
    242: SmartAttr(
        id=242,
        name="Total LBAs Read",
        description="Total data read from drive (in LBAs).",
        importance=Importance.LOW,
    ),
    250: SmartAttr(
        id=250,
        name="Read Error Retry Rate",
        description="Errors requiring read retries.",
        importance=Importance.MEDIUM,
    ),
    254: SmartAttr(
        id=254,
        name="Free Fall Protection",
        description="Free-fall events detected (laptops).",
        importance=Importance.LOW,
    ),
    # NVMe-specific attributes (IDs 1001+)
    1001: SmartAttr(
        id=1001,
        name="Temperature",
        description="Current NVMe drive temperature in Celsius.",
        importance=Importance.MEDIUM,
    ),
    1002: SmartAttr(
        id=1002,
        name="Percentage Used",
        description="NVMe wear indicator. 100% means drive has reached rated write endurance.",
        importance=Importance.HIGH,
    ),
    1003: SmartAttr(
        id=1003,
        name="Available Spare",
        description="Remaining spare capacity for bad block replacement. Low values indicate wear.",
        importance=Importance.HIGH,
        higher_is_worse=False,
    ),
    1004: SmartAttr(
        id=1004,
        name="Media & Data Errors",
        description="Media and Data Integrity Errors. Non-zero indicates unrecovered data errors.",
        importance=Importance.CRITICAL,
    ),
    1005: SmartAttr(
        id=1005,
        name="Power-On Hours",
        description="Total hours the drive has been powered on.",
        importance=Importance.LOW,
        higher_is_worse=False,
    ),
    1006: SmartAttr(
        id=1006,
        name="Power Cycles",
        description="Number of power on/off cycles.",
        importance=Importance.LOW,
    ),
    1007: SmartAttr(
        id=1007,
        name="Unsafe Shutdowns",
        description="Power losses without proper shutdown. High counts may affect reliability.",
        importance=Importance.MEDIUM,
    ),
    1008: SmartAttr(
        id=1008,
        name="Data Written (GB)",
        description="Total data written to the drive in gigabytes.",
        importance=Importance.LOW,
        higher_is_worse=False,
    ),
    1009: SmartAttr(
        id=1009,
        name="Data Read (GB)",
        description="Total data read from the drive in gigabytes.",
        importance=Importance.LOW,
        higher_is_worse=False,
    ),
    1010: SmartAttr(
        id=1010,
        name="Critical Warning",
        description="NVMe critical warning flags. Non-zero indicates serious issues.",
        importance=Importance.CRITICAL,
    ),
}


def get_attr_info(attr_id: int) -> SmartAttr:
    """Get attribute info, returning a generic entry for unknown attributes."""
    if attr_id in SMART_ATTRS:
        return SMART_ATTRS[attr_id]
    return SmartAttr(
        id=attr_id,
        name=f"Vendor Attribute {attr_id}",
        description="Vendor-specific attribute. Consult drive documentation for meaning.",
        importance=Importance.LOW,
    )


def get_importance_color(importance: Importance) -> str:
    """Get CSS color class for importance level."""
    return {
        Importance.CRITICAL: "importance-critical",
        Importance.HIGH: "importance-high",
        Importance.MEDIUM: "importance-medium",
        Importance.LOW: "importance-low",
    }[importance]
