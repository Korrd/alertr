"""Base class for health checks."""

from __future__ import annotations

from abc import ABC, abstractmethod

from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import CheckResult, Metric


class BaseCheck(ABC):
    """Abstract base class for all health checks."""

    name: str = "base"

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    @abstractmethod
    def run(self) -> list[CheckResult]:
        """
        Execute the check and return results.

        Returns a list because some checks (e.g., SMART) may produce
        multiple results (one per disk).
        """
        ...

    def get_metrics(self) -> list[Metric]:
        """
        Return metrics from the last check run.

        Override in subclasses to provide metrics.
        """
        return []
