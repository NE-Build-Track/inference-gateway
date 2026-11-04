"""Export version-two runner metadata without changing version-one artifacts."""

import json
from pathlib import Path

from inference_gateway.adapters.runner_contract import PROFILES
from inference_gateway.adapters.runner_contract_v2 import MEDIA_PROFILES, RunnerMetadataV2

root = Path(__file__).resolve().parents[1] / "live-runner/contract"
for name, value in (
    ("metadata-v2.schema.json", RunnerMetadataV2.model_json_schema()),
    ("profiles-v2.json", {**PROFILES, **MEDIA_PROFILES}),
):
    (root / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
