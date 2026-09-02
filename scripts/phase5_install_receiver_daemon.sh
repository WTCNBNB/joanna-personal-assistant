#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_LABEL="${JOANNA_PHASE5_SERVICE_LABEL:-io.joanna.phase5.receiver}"
BASE="${JOANNA_PHASE5_DAEMON_HOME:-$HOME/.local/share/joanna-phase5}"
RUNTIME_DIR="$BASE/runtime"
DATA_DIR="$BASE/phase5-weektest"
LOG_DIR="$BASE/logs"
BIN_DIR="$HOME/.local/bin"
LAUNCH_BIN="$BIN_DIR/joanna-phase5-receiver"
PLIST="$HOME/Library/LaunchAgents/$SERVICE_LABEL.plist"
PYTHON_BIN="${JOANNA_PHASE5_PYTHON:-/opt/homebrew/opt/python@3.14/bin/python3.14}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

mkdir -p "$RUNTIME_DIR" "$DATA_DIR" "$LOG_DIR" "$BIN_DIR" "$HOME/Library/LaunchAgents" "$REPO_ROOT/.joanna"

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$REPO_ROOT/joanna/" "$RUNTIME_DIR/joanna/"

PROJECT_DATA="$REPO_ROOT/.joanna/phase5-weektest"
if [ -L "$PROJECT_DATA" ]; then
  :
elif [ -d "$PROJECT_DATA" ]; then
  rsync -a "$PROJECT_DATA/" "$DATA_DIR/"
  BACKUP="$REPO_ROOT/.joanna/phase5-weektest.before-daemon-$(date +%Y%m%d%H%M%S)"
  mv "$PROJECT_DATA" "$BACKUP"
  ln -s "$DATA_DIR" "$PROJECT_DATA"
  echo "migrated existing data to $DATA_DIR"
  echo "kept backup at $BACKUP"
elif [ ! -e "$PROJECT_DATA" ]; then
  ln -s "$DATA_DIR" "$PROJECT_DATA"
fi

if [ ! -s "$DATA_DIR/upload-token.txt" ]; then
  umask 077
  openssl rand -hex 24 > "$DATA_DIR/upload-token.txt"
fi
chmod 600 "$DATA_DIR/upload-token.txt"

cat > "$LAUNCH_BIN" <<SH
#!/bin/zsh
set -euo pipefail
BASE="$BASE"
export PYTHONPATH="\$BASE/runtime"
export PHASE5_UPLOAD_TOKEN="\$(cat "\$BASE/phase5-weektest/upload-token.txt")"
exec "$PYTHON_BIN" -m joanna.app.cli --db "\$BASE/phase5-weektest/phase5-weektest.db" phase5 --root "\$BASE/phase5-weektest" receive --host 0.0.0.0 --port 18787
SH
chmod 700 "$LAUNCH_BIN"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$SERVICE_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$LAUNCH_BIN</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/receiver.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/receiver.err</string>
</dict>
</plist>
PLIST
chmod 644 "$PLIST"

if launchctl print "gui/$(id -u)/$SERVICE_LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$SERVICE_LABEL" || true
fi

if lsof -tiTCP:18787 -sTCP:LISTEN >/tmp/joanna-phase5-listen-pids 2>/dev/null; then
  xargs kill < /tmp/joanna-phase5-listen-pids || true
  sleep 1
fi

launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$SERVICE_LABEL"
sleep 2

echo "service_label=$SERVICE_LABEL"
echo "data_dir=$DATA_DIR"
echo "runtime_dir=$RUNTIME_DIR"
echo "token_file=$DATA_DIR/upload-token.txt"
launchctl print "gui/$(id -u)/$SERVICE_LABEL" | sed -n '1,40p'
lsof -nP -iTCP:18787 -sTCP:LISTEN || true
