#!/usr/bin/env bash
set -euo pipefail

if [[ "$(hostname)" != "gpu4038" ]]; then
  echo "refusing to stop a service on $(hostname); expected gpu4038" >&2
  exit 2
fi
listener_pid() { ss -ltnp 2>/dev/null | sed -n "s/.*:$1[[:space:]].*pid=\\([0-9][0-9]*\\).*/\\1/p" | head -n 1; }
wait_for_port_to_close() { for _ in $(seq 1 25); do [[ -z "$(listener_pid "$1")" ]] && return 0; sleep 1; done; return 1; }

server_pid="$(listener_pid 18775)"
if [[ -n "$server_pid" ]]; then kill -TERM "$server_pid"; else echo "unified server is not listening on 18775"; fi
if ! wait_for_port_to_close 18775; then echo "18775 did not close after a graceful stop" >&2; exit 1; fi
llama_pid="$(listener_pid 18776)"
if [[ -n "$llama_pid" ]]; then kill -TERM "$llama_pid"; fi
if ! wait_for_port_to_close 18776; then echo "18776 did not close after a graceful stop" >&2; exit 1; fi
echo "Shared remote inference is stopped on gpu4038."
