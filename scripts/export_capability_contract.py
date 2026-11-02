"""Export version-two runner metadata without changing version-one artifacts."""

import json
from pathlib import Path

from inference_gateway.adapters.capability_contract import MEDIA_PROFILES, CapabilityMetadata
from inference_gateway.adapters.runner_contract import PROFILES

root = Path(__file__).resolve().parents[1] / "live-runner/contract"
for name, value in (
    ("metadata-v2.schema.json", CapabilityMetadata.model_json_schema()),
    ("profiles-v2.json", {**PROFILES, **MEDIA_PROFILES}),
):
    (root / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
