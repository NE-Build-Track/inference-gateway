import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_has_only_stdlib_and_core_dependencies():
    for path in (ROOT / "src/inference_gateway/core").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [name.name for name in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"Use explicit imports in {path}"
                modules = [node.module or ""]
            for module in modules:
                assert module.split(".")[0] in sys.stdlib_module_names or module.startswith(
                    "inference_gateway.core"
                ), f"Core cannot depend on {module}; put translation in an adapter"


def test_adapters_cannot_select_backend_or_import_composition_root():
    for path in [
        *(ROOT / "src/inference_gateway/adapters").glob("*.py"),
        *(ROOT / "src/inference_gateway/backends").glob("*.py"),
    ]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {
                    "inference_gateway.app",
                    "inference_gateway.testing",
                    "inference_gateway.runtime",
                }


def test_importing_core_does_not_import_framework_or_adapters():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import sys",
                    "import inference_gateway.core.service",
                    "assert 'fastapi' not in sys.modules",
                    "assert 'pydantic' not in sys.modules",
                    "assert 'inference_gateway.app' not in sys.modules",
                ]
            ),
        ],
        check=True,
    )


def test_livepeer_imports_are_confined_to_integration_and_composition():
    allowed = {"runtime.py"}
    for path in (ROOT / "src/inference_gateway").rglob("*.py"):
        if "backends" in path.parts or path.name in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [n.name for n in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(name.startswith("livepeer_") for name in names), (
                f"{path}: keep Livepeer dependencies behind the backend or composition root"
            )
