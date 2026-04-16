#!/bin/bash
# Install Clawvatar as systemd services
# Run: sudo bash deploy/install.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "Installing Clawvatar systemd services..."

# Copy service files
cp "$SCRIPT_DIR/clawvatar-server.service" /etc/systemd/system/
cp "$SCRIPT_DIR/clawvatar-agent.service" /etc/systemd/system/

# Create .env if not exists
if [ ! -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    echo "Created .env from .env.example — edit it with your credentials"
fi

# Reload systemd
systemctl daemon-reload

# Enable services (start on boot)
systemctl enable clawvatar-server clawvatar-agent

echo ""
echo "Installed! Next steps:"
echo "  1. Edit $REPO_DIR/.env with your credentials"
echo "  2. sudo systemctl start clawvatar-server"
echo "  3. sudo systemctl start clawvatar-agent"
echo ""
echo "Logs: journalctl -u clawvatar-server -f"
echo "      journalctl -u clawvatar-agent -f"
