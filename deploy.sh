#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Oracle Cloud / Ubuntu Automated 24/7 Deployment Script
# -----------------------------------------------------------------------------
set -e

echo "=== 1. Updating System Packages & Installing Dependencies ==="
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git curl

echo "=== 2. Setting Up Python Virtual Environment ==="
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Virtual environment created."
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== 3. Ensuring Project Directories Exist ==="
mkdir -p data logs

echo "=== 4. Creating Systemd Service for 24/7 Orchestrator ==="
APP_DIR=$(pwd)
USER_NAME=$(whoami)

cat << EOF | sudo tee /etc/systemd/system/email-automation.service > /dev/null
[Unit]
Description=24/7 Multi-Agent Lead Outreach Orchestrator
After=network.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/orchestrator.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "=== 5. Enabling and Starting Email Automation Service ==="
sudo systemctl daemon-reload
sudo systemctl enable email-automation.service
sudo systemctl restart email-automation.service

echo ""
echo "================================================================="
echo " DEPLOYMENT COMPLETE! 24/7 AGENT SERVICE IS NOW RUNNING."
echo "================================================================="
echo " Useful Commands:"
echo "   - View Service Status : sudo systemctl status email-automation"
echo "   - View Live Activity  : tail -f logs/pipeline.log"
echo "   - View CLI Dashboard  : python status_report.py"
echo "   - Restart Service     : sudo systemctl restart email-automation"
echo "   - Stop Service        : sudo systemctl stop email-automation"
echo "================================================================="
