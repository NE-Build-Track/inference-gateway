"""Prepare readable, OpenSSL-hashed public certificate pins without fetching certificates."""

import shutil
import ssl
import subprocess
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "deploy/livepeer/gateway/config/pinned-certs"
    target = root / "deploy/livepeer/gateway/config/runner-trust"
    pins = sorted(source.glob("*.pem"))
    if not pins:
        raise SystemExit("No existing public certificate pins in the gateway config directory")
    # Validate all inputs before replacing generated files. Private keys never belong here.
    for pin in pins:
        pem = pin.read_text()
        if "PRIVATE KEY" in pem:
            raise SystemExit("Refusing a private key in the public certificate directory")
        ssl.PEM_cert_to_DER_cert(pem)
    target.mkdir(mode=0o755, exist_ok=True)
    target.chmod(0o755)
    for old in target.iterdir():
        if old.is_file() or old.is_symlink():
            old.unlink()
    for pin in pins:
        destination = target / pin.name
        shutil.copyfile(pin, destination)
        destination.chmod(0o644)
    subprocess.run(["openssl", "rehash", str(target)], check=True, capture_output=True)
    print(f"Prepared {len(pins)} existing public certificate pins for the gateway")


if __name__ == "__main__":
    main()
