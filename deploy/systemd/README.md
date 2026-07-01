# systemd Deployment

This directory contains systemd unit files for running the Agent App Delivery Retry Daemon as a systemd service on bare-metal or VM hosts.

## Files

| File | Purpose |
|------|---------|
| `agent-app-daemon.service` | systemd unit file with security hardening |
| `agent-app-daemon.env` | Environment file with config paths and secrets |
| `README.md` | This file |

## Prerequisites

1. Python 3.12+ installed
2. Project installed with dependencies: `pip install -e ".[dev]"`
3. Non-root user `agent-app` created:
   ```bash
   sudo useradd -r -s /sbin/nologin -d /opt/agent-app agent-app
   ```
4. Directories created with correct ownership:
   ```bash
   sudo mkdir -p /opt/agent-app /etc/agent-app /var/lib/agent-app /var/log/agent-app
   sudo chown -R agent-app:agent-app /opt/agent-app /var/lib/agent-app /var/log/agent-app
   ```

## Installation

```bash
# 1. Copy unit file
sudo cp agent-app-daemon.service /etc/systemd/system/

# 2. Copy config template
sudo cp ../config/daemon.systemd.yaml /etc/agent-app/daemon.yaml
sudo chown agent-app:agent-app /etc/agent-app/daemon.yaml

# 3. Create environment file with real token
sudo cp agent-app-daemon.env /etc/agent-app/agent-app-daemon.env
sudo chmod 600 /etc/agent-app/agent-app-daemon.env
# Edit /etc/agent-app/agent-app-daemon.env — set AGENT_APP_CONTROL_TOKEN

# 4. Reload systemd and enable
sudo systemctl daemon-reload
sudo systemctl enable --now agent-app-daemon
```

## Management

```bash
# Status
sudo systemctl status agent-app-daemon

# Logs
sudo journalctl -u agent-app-daemon -f

# Stop (triggers graceful drain via control API)
sudo systemctl stop agent-app-daemon

# Restart
sudo systemctl restart agent-app-daemon

# Disable
sudo systemctl disable --now agent-app-daemon
```

## Health Checks

```bash
# Local health endpoint
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready

# Control API (replace token)
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8090/control/status
```

## Security Notes

- Service runs as non-root user `agent-app`
- `ProtectSystem=full` makes filesystem read-only except /dev, /proc, /sys
- `ProtectHome=true` makes /home, /root, /run/user inaccessible
- `NoNewPrivileges=true` prevents privilege escalation
- Environment file should be mode 600 (root-only read)
- Control token stored in environment file — ensure it's protected
