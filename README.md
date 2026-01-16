# Homelab Storage Monitor

Monitoring and alerting for homelab disk arrays. Detects disk failure risks, RAID degradation, filesystem issues, and sends alerts via Slack and email.

## Features

- **LVM RAID1 Monitoring**: Detect mirror degradation, sync issues, and stalled rebuilds
- **SMART Health Checks**: Monitor disk health attributes with delta detection
- **Filesystem Capacity**: Track usage with configurable warning thresholds
- **Kernel Log Scanning**: Detect I/O errors, ext4 errors, and SATA issues
- **Smart Alerting**: Deduplicated alerts via Slack and email with cooldown periods
- **Web Dashboard**: Real-time status, historical charts, and event timeline
- **Simple Backup**: Single SQLite database file

## Quick Start

### 1. Clone and Configure

```bash
git clone https://github.com/alertr/homelab-storage-monitor.git
cd homelab-storage-monitor

# Create config directory and copy example
mkdir -p config data
cp config/config.example.yaml config/config.yaml

# Edit configuration for your setup
nano config/config.yaml
```

### 2. Configure Monitored Filesystems

Edit `docker-compose.yml` to add bind mounts for filesystems you want to monitor:

```yaml
volumes:
  # ... existing mounts ...

  # Add your monitored filesystems
  - /media/DATOS:/hostfs/media/DATOS:ro
  - /mnt/storage:/hostfs/mnt/storage:ro
```

Then in `config/config.yaml`:

```yaml
filesystem:
  mountpoints:
    - path: /hostfs/media/DATOS
      warn_pct: 85
      crit_pct: 95
```

### 3. Configure Disks for SMART Monitoring

```yaml
smart:
  disks:
    - /dev/sda
    - /dev/sdb
```

### 4. Start Services

```bash
docker-compose up -d
```

### 5. Access Dashboard

Open http://your-server:8088 in your browser.

## Architecture

Two-container model for security:

1. **Collector** (privileged): Runs health checks, needs host device access
2. **Dashboard** (unprivileged): Web UI with read-only database access

```
┌─────────────────┐     ┌─────────────────┐
│    Collector    │     │    Dashboard    │
│   (privileged)  │     │  (unprivileged) │
│                 │     │                 │
│  - LVM checks   │     │  - FastAPI      │
│  - SMART checks │     │  - Charts       │
│  - Log scanning │     │  - API          │
│  - Alerting     │     │                 │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │    ┌──────────────┐   │
         └───►│  SQLite DB   │◄──┘
              │ (hsm.sqlite) │
              └──────────────┘
```

## Host Mounts Explained

The collector container requires several host mounts:

| Mount | Purpose |
|-------|---------|
| `/dev` | Access to disk devices for SMART checks |
| `/run/lvm`, `/etc/lvm` | LVM metadata and runtime |
| `/run/udev` | Device information |
| `/run/log/journal` | Journald logs for error scanning |
| `/etc/machine-id` | Required for journald access |
| `/hostfs/*` | Bind-mounted filesystems to monitor |

## Configuration Reference

See [config/config.example.yaml](config/config.example.yaml) for full documentation.

### Key Settings

```yaml
# Check interval
scheduler:
  interval_seconds: 900  # 15 minutes

# Alert deduplication
alerts:
  dedupe_cooldown_seconds: 21600  # 6 hours between repeated alerts
  send_recovery: true              # Notify when issues resolve

# Data retention
history:
  retention_days_metrics: 90
  retention_days_events: 180
```

## CLI Commands

```bash
# Run checks once
hsm run --config /path/to/config.yaml

# Run checks in loop mode (for collector)
hsm run --config /path/to/config.yaml --loop

# Start dashboard server
hsm serve --config /path/to/config.yaml --bind 0.0.0.0:8088

# Show current status
hsm status --config /path/to/config.yaml

# Test alerting
hsm test-alerts --config /path/to/config.yaml --slack --email

# Run database migrations
hsm migrate-db --config /path/to/config.yaml

# Clean up old data
hsm retention --config /path/to/config.yaml --vacuum
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard overview |
| `GET /filesystem` | Filesystem status page |
| `GET /lvm` | LVM RAID status page |
| `GET /smart` | SMART disk health page |
| `GET /events` | Event timeline |
| `GET /api/status/current` | Current status JSON |
| `GET /api/runs?limit=50` | Recent check runs |
| `GET /api/metrics?name=...` | Query metrics |
| `GET /api/events?severity=...` | Query events |
| `GET /api/issues/open` | Open issues |
| `GET /health` | Health check (no auth) |

## Authentication

For security, enable authentication when exposing the dashboard:

```yaml
dashboard:
  auth_enabled: true
  auth_username: admin
  auth_password: your-secure-password
```

Or use a bearer token:

```yaml
dashboard:
  auth_enabled: true
  auth_token: your-secret-token
```

Use the token as the password with any username.

## Backup and Restore

### Backup

The entire state is in a single SQLite file:

```bash
# Stop collector to ensure consistency
docker-compose stop hsm_collector

# Copy database
cp data/hsm.sqlite /backup/hsm-$(date +%Y%m%d).sqlite

# Restart collector
docker-compose start hsm_collector
```

### Restore

```bash
docker-compose stop hsm_collector hsm_dashboard
cp /backup/hsm-20240115.sqlite data/hsm.sqlite
docker-compose up -d
```

## Adding New Disks or Mountpoints

1. Stop the collector:
   ```bash
   docker-compose stop hsm_collector
   ```

2. Edit `config/config.yaml`:
   ```yaml
   smart:
     disks:
       - /dev/sda
       - /dev/sdb
       - /dev/sdc  # New disk

   filesystem:
     mountpoints:
       - path: /hostfs/media/DATOS
       - path: /hostfs/mnt/newdrive  # New mount
   ```

3. If adding a new mountpoint, update `docker-compose.yml`:
   ```yaml
   volumes:
     - /mnt/newdrive:/hostfs/mnt/newdrive:ro
   ```

4. Restart:
   ```bash
   docker-compose up -d
   ```

## Troubleshooting

### Journald Access Issues

If journald logs aren't accessible in the container:

1. Check that journal files exist on host:
   ```bash
   ls -la /run/log/journal/
   ```

2. If using fallback log scanning:
   ```yaml
   journal:
     use_journald: false
     fallback_log_paths:
       - /var/log/kern.log
   ```

   And mount the log files:
   ```yaml
   volumes:
     - /var/log:/var/log:ro
   ```

### LVM Not Detected

Ensure LVM runtime is mounted:

```yaml
volumes:
  - /run/lvm:/run/lvm:rw
  - /etc/lvm:/etc/lvm:ro
```

### SMART Access Denied

The collector must run with `privileged: true` for SMART access.

### Dashboard Shows No Data

1. Check collector logs:
   ```bash
   docker-compose logs hsm_collector
   ```

2. Verify database exists:
   ```bash
   ls -la data/hsm.sqlite
   ```

3. Check database permissions match dashboard user.

## Development

### Local Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check .
mypy homelab_storage_monitor/
```

### Running Locally

```bash
# Create test config
cp config/config.example.yaml config.yaml

# Run single check
hsm run -c config.yaml

# Start dashboard
hsm serve -c config.yaml --bind 127.0.0.1:8088
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for details
