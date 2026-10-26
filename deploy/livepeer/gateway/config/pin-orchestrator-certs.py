"""Pin the TLS certificates of the orchestrators in config/orchestrators.txt (trust on first use).

Livepeer orchestrators commonly serve self-signed certificates. The gateway's engine and builder-core's
discovery service verify TLS, so without a pin they cannot use such an orchestrator. This script fetches
each orchestrator's current leaf certificate into config/pinned-certs/; scripts/prepare-runner-trust.py
turns them into config/runner-trust, which both containers trust beside the system CAs through
SSL_CERT_DIR. Re-run both when an orchestrator rotates its certificate. Only pin hosts you intend to pay.
"""

import ssl
from pathlib import Path
from urllib.parse import urlparse

here = Path(__file__).resolve().parent
target = here / "pinned-certs"
target.mkdir(exist_ok=True)
lines = (line.split("#", 1)[0].strip() for line in (here / "orchestrators.txt").read_text().splitlines())
orchestrators = [line for line in lines if line]
for url in orchestrators:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        continue
    port = parsed.port or 443
    try:
        pem = ssl.get_server_certificate((parsed.hostname, port), timeout=10)
    except OSError as exc:
        print(f"skip {parsed.hostname}:{port}: {exc}")
        continue
    (target / f"{parsed.hostname}.pem").write_text(pem)
    print(f"pinned {parsed.hostname}:{port}")
