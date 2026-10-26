#!/usr/bin/env bash
# One-time bootstrap of the shared allocation (see deploy/README.md).
# Creates a grant and an API key in Batteries, then writes LPB_SHARED_PAYMENT_KEY and
# LPB_SHARED_ALLOCATION_ID into deploy/livepeer/gateway/.env. Requires the clearinghouse service to be healthy.
set -euo pipefail
cd "$(dirname "$0")"
AMOUNT_USD="${1:?Usage: deploy/livepeer/gateway/first-run.sh AMOUNT_USD (creates a funded grant)}"
ENV_FILE=.env
[ -f "$ENV_FILE" ] || { echo "copy .env.example to .env first" >&2; exit 1; }
if grep -qE '^LPB_SHARED_PAYMENT_KEY=lpg_' "$ENV_FILE"; then
  echo "LPB_SHARED_PAYMENT_KEY is already set in $ENV_FILE; refusing to create another allocation" >&2
  exit 1
fi
ch() { docker compose -f compose.yaml --profile payment-stack exec -T clearinghouse clearinghouse --db-path /data/clearinghouse.db "$@"; }
echo "creating grant (\$$AMOUNT_USD)…"
GRANT_JSON=$(ch grant create --name EndpointGateway --sponsor EndpointGateway --amount-usd "$AMOUNT_USD" --status active)
GRANT_ID=$(printf '%s' "$GRANT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("id") or d["grant"]["id"])')
echo "grant $GRANT_ID"
echo "creating allocation + api key…"
KEY_JSON=$(ch api-key create --grant-id "$GRANT_ID" --name endpoint-gateway --amount-usd "$AMOUNT_USD")
read -r ALLOCATION_ID API_KEY < <(printf '%s' "$KEY_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["allocation_id"], d["api_key"])')
python3 - "$ENV_FILE" "$ALLOCATION_ID" "$API_KEY" "$GRANT_ID" <<'PY'
import re, sys, pathlib
path, allocation, key, grant = sys.argv[1:]
text = pathlib.Path(path).read_text()
def put(name, value):
    global text
    line = f"{name}={value}"
    text, n = re.subn(rf"^{name}=.*$", line, text, flags=re.M)
    if n == 0:
        text += f"\n{line}\n"
put("LPB_SHARED_PAYMENT_KEY", key)
put("LPB_SHARED_ALLOCATION_ID", allocation)
put("GATEWAY_GRANT_ID", grant)
pathlib.Path(path).write_text(text)
PY
chmod 600 "$ENV_FILE"
echo "wrote LPB_SHARED_PAYMENT_KEY and LPB_SHARED_ALLOCATION_ID=$ALLOCATION_ID to $ENV_FILE"
echo "now: docker compose up -d gateway"
