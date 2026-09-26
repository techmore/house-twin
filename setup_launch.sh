#!/usr/bin/env bash
#
# setup_launch.sh — install (or remove) the House Twin LaunchAgents.
#
# Two agents, matching the two-process architecture: the poller writes to
# house.db, the web server reads from it. They never talk directly.
#
#   ./setup_launch.sh install     install and start both
#   ./setup_launch.sh uninstall   unload and delete both
#   ./setup_launch.sh status      show what is loaded
#   ./setup_launch.sh logs        tail both logs
#
set -euo pipefail

LABEL_PREFIX="com.dolbec.housetwin"
# Short names. Label is "$LABEL_PREFIX.$name", template is "setup/launchagent-$name.plist".
AGENTS=("poller" "web")
AGENT_DIR="$HOME/Library/LaunchAgents"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"

green() { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }
red() { printf '\033[31m%s\033[0m\n' "$1"; }

require_venv() {
  if [[ ! -x "$VENV_PY" ]]; then
    red "virtualenv not found at $VENV_PY"
    echo "  create it with:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
  fi
}

do_install() {
  require_venv
  mkdir -p "$AGENT_DIR"

  for name in "${AGENTS[@]}"; do
    local template="$PROJECT_ROOT/setup/launchagent-$name.plist"
    local target="$AGENT_DIR/$LABEL_PREFIX.$name.plist"

    [[ -f "$template" ]] || { red "missing template $template"; exit 1; }

    # Substitute the placeholder, escaping / and & for sed on macOS.
    sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" "$template" > "$target"
    green "wrote $target"

    # bootout tolerates a not-loaded agent, so this is safe on re-install
    launchctl bootout "gui/$(id -u)/$LABEL_PREFIX.$name" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$target"
    green "loaded $LABEL_PREFIX.$name"
  done

  echo
  green "installed. dashboard: http://127.0.0.1:5002"
  echo "  poller log: /tmp/housetwin-poller.log"
  echo "  web log:    /tmp/housetwin-web.log"
  echo
  if ! "$VENV_PY" -m house_twin.auth status | grep -q "authorised  : yes"; then
    yellow "note: the poller will idle until you authorise the Aqara account."
    yellow "      see the 'Getting authorised' section of README.md"
  fi
}

do_uninstall() {
  for name in "${AGENTS[@]}"; do
    local target="$AGENT_DIR/$LABEL_PREFIX.$name.plist"
    launchctl bootout "gui/$(id -u)/$LABEL_PREFIX.$name" 2>/dev/null || true
    if [[ -f "$target" ]]; then
      rm "$target"
      green "removed $target"
    fi
  done
}

do_status() {
  for name in "${AGENTS[@]}"; do
    local label="$LABEL_PREFIX.$name"
    if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
      local pid
      pid=$(launchctl print "gui/$(id -u)/$label" 2>/dev/null \
            | awk '/pid = /{print $3; exit}' || true)
      if [[ -n "$pid" ]]; then
        green "$label  running (pid $pid)"
      else
        yellow "$label  loaded, not running"
      fi
    else
      echo "$label  not loaded"
    fi
  done

  echo
  if curl -fsS -o /dev/null http://127.0.0.1:5002/api/house 2>/dev/null; then
    green "dashboard responding on http://127.0.0.1:5002"
  else
    yellow "dashboard not responding on http://127.0.0.1:5002"
  fi
}

do_logs() {
  tail -f /tmp/housetwin-poller.log /tmp/housetwin-web.log
}

case "${1:-install}" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
  status)    do_status ;;
  logs)      do_logs ;;
  *)
    echo "usage: $0 {install|uninstall|status|logs}"
    exit 1
    ;;
esac
