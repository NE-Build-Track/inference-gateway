#!/usr/bin/env bash
# Build the three images from pinned sources (D9). Never pushes. IMAGE_REGISTRY (from deploy/.env or the
# environment) prefixes the names; leave it empty for local-only names.
#   scripts/build-images.sh            # all three
#   scripts/build-images.sh gateway    # one service
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f deploy/.env ]; then
  # shellcheck disable=SC1091
  IMAGE_REGISTRY="$( { grep -E '^IMAGE_REGISTRY=' deploy/.env || true; } | cut -d= -f2- | tr -d '[:space:]')"
fi
export IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"
SERVICES=("$@")
[ ${#SERVICES[@]} -eq 0 ] && SERVICES=(clearinghouse signer gateway)
echo "registry prefix: '${IMAGE_REGISTRY}'"
for svc in "${SERVICES[@]}"; do
  echo "== building $svc"
  docker compose -f deploy/compose.yaml build --pull "$svc"
done
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E "(chatlpt/(clearinghouse|node)|endpoint-gateway/gateway)" || true
echo "built. Publish explicitly with: docker compose -f deploy/compose.yaml push <service>"
