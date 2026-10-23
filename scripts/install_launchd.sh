#!/bin/bash
# Installs 3 macOS launch agents (current user only):
#   serve  - keeps the web board running at http://127.0.0.1:8765
#   fetch  - pulls new channel messages every hour
#   digest - sends the daily summary to your Telegram Saved Messages at 09:00
# Remove with: scripts/install_launchd.sh uninstall
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/.local/share/jobboard/logs"
LABELS=(com.jobboard.serve com.jobboard.fetch com.jobboard.digest)
UID_=$(id -u)

if [[ "${1:-}" == "uninstall" ]]; then
  for l in "${LABELS[@]}"; do
    launchctl bootout "gui/$UID_/$l" 2>/dev/null || true
    rm -f "$AGENTS/$l.plist"
  done
  echo "removed"; exit 0
fi

mkdir -p "$AGENTS" "$LOGS"
plist() {  # label, subcommand, schedule-xml, extra-xml
  cat > "$AGENTS/$1.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>$UV</string><string>run</string><string>--project</string><string>$DIR</string><string>jobboard</string><string>$2</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string></dict>
  <key>StandardOutPath</key><string>$LOGS/$2.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$2.log</string>
  $3
</dict></plist>
PL
}
plist com.jobboard.serve  serve  "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>"
plist com.jobboard.fetch  fetch  "<key>RunAtLoad</key><true/><key>StartInterval</key><integer>3600</integer>"
plist com.jobboard.digest digest "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>"
for l in "${LABELS[@]}"; do
  launchctl bootout "gui/$UID_/$l" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_" "$AGENTS/$l.plist"
done
echo "installed. board: http://127.0.0.1:8765   logs: $LOGS"
