#!/bin/sh
# Dispatch the container command: server | demo | test | <anything else runs as-is>
set -e

case "$1" in
  server)
    shift
    exec python -m militech_equipment.server "$@"
    ;;
  demo)
    shift
    exec python -m militech_agent.demo "$@"
    ;;
  test)
    shift
    ruff check .
    exec pytest "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
