"""Create the payment stack's service secrets once (adapted from builder-core's dev-stack).

Writes deploy/livepeer/gateway/secrets/: Batteries credentials (operator, a least-privilege engine credential, the
grant desk, the signer webhook, Kafka users) and the signer's webhook and Kafka files. Then copy the
payer keystore in (deploy/README.md):

    cp /path/to/key.json      deploy/livepeer/gateway/secrets/keystore.json
    cp /path/to/key.password  deploy/livepeer/gateway/secrets/keystore-password
    printf '%s' 'https://arb1...' > deploy/livepeer/gateway/secrets/rpc-url
    chmod 600 deploy/livepeer/gateway/secrets/*
"""

import os
import secrets
from pathlib import Path

os.umask(0o077)
target = Path(__file__).resolve().parent / "secrets"
target.mkdir(mode=0o700, exist_ok=True)
if any(target.iterdir()):
    raise SystemExit("deploy/livepeer/gateway/secrets/ must be empty; refusing to replace credentials")
operator, engine, grant_desk, webhook, reader, producer = (secrets.token_hex(32) for _ in range(6))
creds = f'''[[credentials]]
id = "operator"
secret = "{operator}"
[credentials.management]
allow = ["grants.*", "allocations.*", "api_keys.*", "sessions.*",
         "settlements.read", "usage.read", "ledger.read", "escrow.read"]

[[credentials]]
id = "builder-engine"
secret = "{engine}"
[credentials.management]
allow = ["grants.fund", "allocations.create", "allocations.fund", "allocations.read",
         "allocations.status", "allocations.revoke", "api_keys.create", "api_keys.read",
         "api_keys.revoke", "usage.read"]

[[credentials]]
id = "grant-desk"
secret = "{grant_desk}"
[credentials.management]
allow = ["grants.read", "grants.create"]

[[credentials]]
id = "signer"
secret = "{webhook}"
[credentials.webhook]
authorize = true

[[credentials]]
id = "accounting"
secret = "{reader}"
[credentials.kafka]
username = "accounting"
allow = ["read"]
accounting = true

[[credentials]]
id = "producer"
secret = "{producer}"
[credentials.kafka]
username = "producer"
allow = ["write"]
'''
files = {
    "clearinghouse.toml": creds,
    "operator-token": operator,
    "engine-token": engine,
    "grant-desk-token": grant_desk,
    "webhook-headers": f"Livepeer-Clearinghouse-Token:{webhook}",
    "webhook-url": "http://127.0.0.1:8080/v1/signer/authorize",
    "kafka-username": "producer",
    "kafka-password": producer,
}
for name, value in files.items():
    (target / name).write_text(value)
print(
    "Created deploy/livepeer/gateway/secrets/. Now add rpc-url, keystore.json and keystore-password "
    "(mode 0600; password without a trailing newline), then fill deploy/livepeer/gateway/.env from .env.example."
)
