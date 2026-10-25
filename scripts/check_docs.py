"""Check navigation links and required sources of truth without network access."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "AGENTS.md",
    "ARCHITECTURE.md",
    "README.md",
    "docs/index.md",
    "docs/product-specs/compatibility.md",
    "docs/QUALITY.md",
    "docs/exec-plans/tech-debt-tracker.md",
]


def main() -> None:
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"Missing {name}; restore the repository knowledge map")
    paths = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    for path in paths:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            destination = target.split("#")[0]
            if not (path.parent / destination).exists():
                errors.append(f"{path.relative_to(ROOT)}: broken link {target}; update its target")
    if len((ROOT / "AGENTS.md").read_text().splitlines()) > 100:
        errors.append("AGENTS.md exceeds 100 lines; move detail into linked docs")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Documentation checks passed ({len(paths)} files)")


if __name__ == "__main__":
    main()
