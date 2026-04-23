#!/bin/bash
# ============================================================
# RoboScout Daily Runner — Setup Script
# ============================================================
# Run this once to install the macOS LaunchAgent for daily runs.
#
# What it does:
#   1. Detects your Python path (prefers venv if active)
#   2. Creates a personalized LaunchAgent plist
#   3. Installs it to ~/Library/LaunchAgents/
#   4. Loads it with launchctl
#
# After setup, run_daily.py will execute every day at 6:00 AM.
# If your Mac is asleep at 6 AM, it runs when the Mac wakes up.
#
# Usage:
#   chmod +x setup_daily.sh
#   ./setup_daily.sh
#
# To uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.halo.roboscout-daily.plist
#   rm ~/Library/LaunchAgents/com.halo.roboscout-daily.plist
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.halo.roboscout-daily.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== RoboScout Daily Runner Setup ==="
echo ""

# 1. Find Python
if [ -n "$VIRTUAL_ENV" ]; then
    PYTHON_PATH="$VIRTUAL_ENV/bin/python"
    echo "Using venv Python: $PYTHON_PATH"
elif command -v python3 &>/dev/null; then
    PYTHON_PATH="$(which python3)"
    echo "Using system Python: $PYTHON_PATH"
else
    echo "ERROR: python3 not found. Install Python 3 first."
    exit 1
fi

# Verify it works
$PYTHON_PATH -c "import dotenv, gspread, requests, anthropic" 2>/dev/null || {
    echo ""
    echo "WARNING: Some required packages may be missing."
    echo "Run: $PYTHON_PATH -m pip install python-dotenv gspread google-auth requests anthropic"
    echo ""
}

# 2. Create the plist with correct paths
echo "Creating LaunchAgent plist..."

cat > "$PLIST_DEST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.halo.roboscout-daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>-m</string>
        <string>roboscout.run_daily</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$(dirname "$PYTHON_PATH")</string>
    </dict>

    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/logs/launchagent_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/logs/launchagent_stderr.log</string>

    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
PLIST

echo "  Written to: $PLIST_DEST"

# 3. Create logs directory
mkdir -p "$SCRIPT_DIR/logs"

# 4. Unload if already loaded, then load
if launchctl list | grep -q "com.halo.roboscout-daily" 2>/dev/null; then
    echo "Unloading previous version..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

echo "Loading LaunchAgent..."
launchctl load "$PLIST_DEST"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "RoboScout will run daily at 6:00 AM local time."
echo "If your Mac is asleep at 6 AM, it will run when it wakes up."
echo ""
echo "Useful commands:"
echo "  # Check if it's loaded"
echo "  launchctl list | grep roboscout"
echo ""
echo "  # Run it now (for testing)"
echo "  python -m roboscout.run_daily --dry-run"
echo ""
echo "  # Uninstall"
echo "  launchctl unload $PLIST_DEST"
echo "  rm $PLIST_DEST"
echo ""
echo "  # View logs"
echo "  tail -f $SCRIPT_DIR/logs/daily_$(date +%Y%m%d).log"
echo ""
echo "Don't forget to set these env vars (in .env or system):"
echo "  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/..."
echo "  GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json"
