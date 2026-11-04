"""Paid probes stop on uncertainty and never replay an existing report automatically."""

import argparse
import json
import runpy
from pathlib import Path

import httpx
import pytest

probe = runpy.run_path(str(Path(__file__).parents[1] / "scripts/test_paid_catalog.py"))["run"]
reconcile = probe.__globals__["reconcile"]


def test_paid_probe_stops_after_one_failure_and_refuses_an_existing_report(tmp_path, monkeypatch):
    names = ["flux-image", "birefnet", "whisper", "ace-music", "triposr"]
    spec = {
        "paths": {
            name: {
                "post": {
                    "operationId": name,
                    "x-livepeer": {
                        "app": "app-" + name,
                        "model": "model-" + name,
                        "path": "/v1/test",
                    },
                }
            }
            for name in names
        }
    }
    catalog, key = tmp_path / "catalog.json", tmp_path / "key"
    catalog.write_text(json.dumps(spec), encoding="utf-8")
    key.write_text("existing-test-credential", encoding="utf-8")
    (tmp_path / "subject.png").write_bytes(b"fixture")
    (tmp_path / "reference.wav").write_bytes(b"fixture")
    requests = []

    def handler(request):
        if request.url.path == "/v1/allowance":
            return httpx.Response(200, json={"available": "1.00"})
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["max_attempts"] == 1 and "max_output_bytes" not in payload
        assert payload["operation_ref"].startswith("catalog-acceptance-")
        return httpx.Response(402, json={"error": "refused"})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler))
    )
    args = argparse.Namespace(
        run=True,
        output=tmp_path / "report.json",
        url="https://engine.example",
        key_file=key,
        catalog=catalog,
        fixtures=tmp_path,
    )
    with pytest.raises(RuntimeError, match="Paid request failed"):
        probe(args)
    report = json.loads(args.output.read_text())
    assert len(requests) == len(report["jobs"]) == 1
    assert report["jobs"][0]["state"] == "failed_or_unknown"
    assert "existing-test-credential" not in args.output.read_text()
    with pytest.raises(ValueError, match="Report already exists"):
        probe(args)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status,classification",
    [
        ("observed", "delivered"),
        ("pending", "delivered"),
        ("observed", "failed_after_payment"),
    ],
)
def test_reconciliation_only_reads_and_never_calls_pending_evidence_zero(
    tmp_path, monkeypatch, status, classification
):
    output, key = tmp_path / "report.json", tmp_path / "key"
    key.write_text("existing-test-credential", encoding="utf-8")
    output.write_text(
        json.dumps(
            {
                "engine": "https://engine.example",
                "jobs": [
                    {"job_id": str(number), "state": "served", "paid_attempts": 1}
                    for number in range(5)
                ],
            }
        )
    )
    client = httpx.Client

    def handler(request):
        assert request.method == "GET" and request.url.path.endswith("/cost")
        fees = {"fee_eth": "0.000001", "fee_usd": "0.02", "row_count": 1}
        return httpx.Response(
            200,
            json={
                "status": status,
                "fees": fees,
                "billable": {"fee_eth": "0.0000010"},
                "attempts": [{"cost_class": classification}],
            },
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler))
    )
    reconcile(argparse.Namespace(url="https://engine.example", key_file=key, output=output))
    accounting = json.loads(output.read_text())["accounting"]
    if status == "observed" and classification == "delivered":
        assert accounting["status"] == "observed"
        assert accounting["fee_usd"] == "0.10" and accounting["fee_eth"] == "0.000005"
    else:
        assert accounting["status"] == "pending_or_inconsistent"
        assert accounting["fee_eth"] is None and accounting["fee_usd"] is None
    assert accounting["chain_settlement"] == "not_checked"
