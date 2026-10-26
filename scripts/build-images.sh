#!/usr/bin/env bash
# Build pinned gateway/payment images; never publish implicitly.
set -euo pipefail
cd "$(dirname "$0")/.."
services=("$@")
[ ${#services[@]} -eq 0 ] && services=(clearinghouse signer gateway)
for service in "${services[@]}"; do
  docker compose -f deploy/livepeer/gateway/compose.yaml build "$service"
done
