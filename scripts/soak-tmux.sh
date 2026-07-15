#!/usr/bin/env bash
#
# Run the soak inside a detached tmux session so it survives SSH disconnects.
#
#   ./scripts/soak-tmux.sh start [seed|run]   # start detached (default: run)
#   ./scripts/soak-tmux.sh attach             # attach to watch the live display
#   ./scripts/soak-tmux.sh status             # is it still running?
#   ./scripts/soak-tmux.sh logs               # tail the captured output
#   ./scripts/soak-tmux.sh stop               # graceful stop (prints the report)
#   ./scripts/soak-tmux.sh kill               # hard kill the session
#
# Credentials come from env vars (see config.example.yaml); export them before
# `start` so the detached session inherits them.
set -euo pipefail

cd "$(dirname "$0")/.."
SESSION="${SOAK_TMUX_SESSION:-cb-soak}"
LOG="${SOAK_LOG:-$PWD/soak-tmux.log}"

usage() {
  echo "usage: $0 {start [seed|run]|attach|status|logs|stop|kill}" >&2
  exit 2
}

cmd="${1:-}"
case "$cmd" in
  start)
    action="${2:-run}"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "session '$SESSION' already exists — attach with: $0 attach" >&2
      exit 1
    fi
    : > "$LOG"
    # Keep the pane open after the soak exits so the report stays on screen.
    tmux new-session -d -s "$SESSION" \
      "./scripts/run-ec2.sh $action; echo; echo '[soak finished — press Enter to close]'; read"
    tmux pipe-pane -t "$SESSION" -o "cat >> '$LOG'"
    echo "started soak in tmux session '$SESSION' (action: $action)"
    echo "  attach : $0 attach"
    echo "  status : $0 status"
    echo "  logs   : $0 logs   (or: tail -f $LOG)"
    echo "  stop   : $0 stop   (graceful — prints the report)"
    ;;
  attach)
    tmux attach -t "$SESSION"
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "session '$SESSION' is RUNNING"
    else
      echo "session '$SESSION' is NOT running"
      exit 1
    fi
    ;;
  logs)
    tail -f "$LOG"
    ;;
  stop)
    # Ctrl-C in the pane triggers the tester's graceful shutdown + report.
    tmux send-keys -t "$SESSION" C-c
    echo "sent graceful stop to '$SESSION' — the report prints in the session."
    echo "watch it with: $0 attach   (or: $0 logs)"
    ;;
  kill)
    tmux kill-session -t "$SESSION"
    echo "killed session '$SESSION'"
    ;;
  *)
    usage
    ;;
esac
