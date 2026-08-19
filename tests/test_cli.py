"""Tests for CLI helpers: live config reload in the collector loop."""

from __future__ import annotations

from homelab_storage_monitor.cli import refresh_runner
from homelab_storage_monitor.config import load_config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.runner import Runner


def write_config(path, warn_pct: int) -> None:
    path.write_text(f"""
history:
  db_path: {path.parent / "hsm.sqlite"}
filesystem:
  mountpoints:
    - path: /hostfs/data
      warn_pct: {warn_pct}
      crit_pct: 95
""")


class TestConfigReload:
    def test_unchanged_config_keeps_runner(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        write_config(cfg_path, warn_pct=85)
        cfg = load_config(cfg_path)
        db = Database(cfg.history.db_path)
        runner = Runner(cfg, db)

        new_cfg, new_db, new_runner = refresh_runner(cfg_path, cfg, db, runner)
        assert new_runner is runner
        assert new_db is db

    def test_edited_thresholds_rebuild_runner(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        write_config(cfg_path, warn_pct=85)
        cfg = load_config(cfg_path)
        db = Database(cfg.history.db_path)
        runner = Runner(cfg, db)

        # The user raises the warn threshold while the loop is running
        write_config(cfg_path, warn_pct=90)
        new_cfg, new_db, new_runner = refresh_runner(cfg_path, cfg, db, runner)

        assert new_runner is not runner
        assert new_cfg.filesystem.mountpoints[0].warn_pct == 90
        # Same database: db_path did not change
        assert new_db is db

    def test_broken_config_keeps_current_setup(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        write_config(cfg_path, warn_pct=85)
        cfg = load_config(cfg_path)
        db = Database(cfg.history.db_path)
        runner = Runner(cfg, db)

        cfg_path.write_text("{ this is: [not valid yaml")
        new_cfg, new_db, new_runner = refresh_runner(cfg_path, cfg, db, runner)
        assert new_runner is runner
        assert new_cfg is cfg
