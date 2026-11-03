"""Five opt-in, single-attempt production Engine API jobs; never print media or keys."""

import argparse
import base64
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx


def run(args: argparse.Namespace) -> None:
    if not args.run:
        raise ValueError("--run is required for paid requests")
    if args.output.exists():
        raise ValueError("Report already exists; inspect its jobs before another paid run")
    key = args.key_file.read_text(encoding="utf-8").strip()
    if not key or any(char.isspace() for char in key):
        raise ValueError("Expected an existing Builder bearer credential")
    document = json.loads(args.catalog.read_text(encoding="utf-8"))
    bindings = [item["post"] for item in document["paths"].values()]
    if len(bindings) != 5:
        raise ValueError("Expected exactly five reviewed model operations")
    media = (
        "data:image/png;base64,"
        + base64.b64encode((args.fixtures / "subject.png").read_bytes()).decode()
    )
    audio = (
        "data:audio/wav;base64,"
        + base64.b64encode((args.fixtures / "reference.wav").read_bytes()).decode()
    )
    inputs = {
        "flux-image": {"prompt": "A red teapot on a wooden table", "size": "256x256"},
        "birefnet": {"image": media},
        "whisper": {"file": audio},
        "ace-music": {"prompt": "Upbeat instrumental acoustic guitar", "seconds": 5},
        "triposr": {"image": media},
    }
    if {item["operationId"] for item in bindings} != set(inputs):
        raise ValueError("Unexpected paid model selection")
    report: dict = {"engine": args.url, "state": "prepared", "jobs": []}

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(
        base_url=args.url.rstrip("/"),
        headers={"Authorization": "Bearer " + key},
        timeout=660,
        follow_redirects=False,
    ) as client:
        allowance = client.get("/v1/allowance")
        allowance.raise_for_status()
        report["allowance_before"] = allowance.json()
        save()
        for operation in bindings:
            identifier = operation["operationId"]
            binding = operation["x-livepeer"]
            reference = "catalog-acceptance-" + uuid4().hex
            row = {
                "capability": identifier,
                "operation_ref": reference,
                "state": "dispatch_unknown",
            }
            report["jobs"].append(row)
            save()  # If interrupted, do not repeat an uncertain paid operation.
            started = time.monotonic()
            response = client.post(
                "/v1/jobs",
                params={"stream": "false"},
                json={
                    "app": binding["app"],
                    "model": binding["model"],
                    "path": binding["path"],
                    "payload": {"model": binding["model"], **inputs[identifier]},
                    "operation_ref": reference,
                    "max_attempts": 1,
                    "timeout_s": 600,
                },
            )
            row.update(
                status=response.status_code,
                seconds=time.monotonic() - started,
                job_id=response.headers.get("livepeer-job-id"),
                output_bytes=len(response.content),
                response_sha256=hashlib.sha256(response.content).hexdigest(),
            )
            if response.status_code != 200 or not row["job_id"]:
                row["state"] = "failed_or_unknown"
                report["state"] = "failed_or_unknown; inspect operation before any repeat"
                save()
                raise RuntimeError(
                    "Paid request failed or lacks job correlation; inspect the saved operation"
                )
            body = response.json()
            if identifier == "whisper":
                valid = "quick brown fox" in body.get("text", "").lower()
            else:
                output = base64.b64decode(body["data"][0]["b64_json"], validate=True)
                valid = len(output) > 100 and (
                    output.startswith(b"\x89PNG")
                    if identifier in {"flux-image", "birefnet"}
                    else output.startswith(b"RIFF")
                    if identifier == "ace-music"
                    else b"\nv " in output and b"\nf " in output
                )
            job = client.get("/v1/jobs/" + row["job_id"])
            job.raise_for_status()
            attempts = job.json().get("attempts", [])
            row["paid_attempts"] = sum(bool(attempt.get("payment_sent")) for attempt in attempts)
            row["output_valid"] = valid
            cost = client.get("/v1/jobs/" + row["job_id"] + "/cost")
            cost.raise_for_status()
            row["cost"] = cost.json()  # Pending remains pending; never fabricate settled spend.
            row["state"] = "served" if valid and row["paid_attempts"] == 1 else "acceptance_failed"
            save()
            if row["state"] != "served":
                raise RuntimeError("Model output or paid attempt count failed acceptance")
        remaining = client.get("/v1/allowance")
        remaining.raise_for_status()
        report["allowance_after"] = remaining.json()
        report["state"] = "served; reconcile recorded costs before claiming accounting acceptance"
        save()


def reconcile(args: argparse.Namespace) -> None:
    """Read existing jobs only; pending or inconsistent evidence never becomes zero cost."""
    report = json.loads(args.output.read_text(encoding="utf-8"))
    if report["engine"].rstrip("/") != args.url.rstrip("/"):
        raise ValueError("Report belongs to another engine")
    key = args.key_file.read_text(encoding="utf-8").strip()
    observed = len(report["jobs"]) == 5
    total_eth, total_usd = Decimal(0), Decimal(0)
    usd_known = True
    with httpx.Client(
        base_url=args.url.rstrip("/"),
        headers={"Authorization": "Bearer " + key},
        timeout=30,
        follow_redirects=False,
    ) as client:
        for row in report["jobs"]:
            if not row.get("job_id"):
                observed = False
                continue
            response = client.get("/v1/jobs/" + row["job_id"] + "/cost")
            response.raise_for_status()
            cost = response.json()
            row["cost"] = cost
            fees, billable = cost["fees"], cost.get("billable", {})
            valid = (
                row.get("state") == "served"
                and row.get("paid_attempts") == 1
                and cost["status"] in {"observed", "corrected"}
                and len(cost["attempts"]) == 1
                and cost["attempts"][0]["cost_class"] == "delivered"
                and fees["row_count"] > 0
                and Decimal(fees["fee_eth"]) > 0
                and billable.get("fee_eth") is not None
                and Decimal(billable["fee_eth"]) == Decimal(fees["fee_eth"])
            )
            row["accounting_valid"] = valid
            observed &= valid
            total_eth += Decimal(fees["fee_eth"])
            if fees.get("fee_usd") is None:
                usd_known = False
            else:
                total_usd += Decimal(fees["fee_usd"])
    report["accounting"] = {
        "status": "observed" if observed else "pending_or_inconsistent",
        "fee_eth": str(total_eth) if observed else None,
        "fee_usd": str(total_usd) if observed and usd_known else None,
        "chain_settlement": "not_checked",
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--reconcile", action="store_true", help="Read existing job costs; never resubmit"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.reconcile:
        reconcile(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
