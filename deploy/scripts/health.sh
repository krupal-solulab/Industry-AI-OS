#!/usr/bin/env bash
# Poll /healthz on every application service via its published host port.
# Used by `make health`. Exits non-zero if any service is not OK.
set -uo pipefail

declare -A SERVICES=(
  [gateway]=8000
  [identity]=8001
  [authz]=8002
  [orchestrator]=8003
  [knowledge]=8004
  [workflows]=8005
  [connectors]=8006
  [audit]=8007
  [admin]=8008
)

fail=0
for name in "${!SERVICES[@]}"; do
  port="${SERVICES[$name]}"
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${port}/healthz" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    printf "  \033[32m✓\033[0m %-13s :%s  healthy\n" "$name" "$port"
  else
    printf "  \033[31m✗\033[0m %-13s :%s  (HTTP %s)\n" "$name" "$port" "$code"
    fail=1
  fi
done

if [[ "$fail" == "0" ]]; then
  echo "All application services healthy."
else
  echo "Some services are not healthy." >&2
fi
exit "$fail"
