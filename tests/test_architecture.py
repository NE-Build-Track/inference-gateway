import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_has_only_stdlib_and_core_dependencies():
    for path in (ROOT / "src/endpoint_gateway/core").glob("*.py"):
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
                    "endpoint_gateway.core"
                ), f"Core cannot depend on {module}; put translation in an adapter"


def test_adapters_cannot_select_backend_or_import_composition_root():
    for path in (ROOT / "src/endpoint_gateway/adapters").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {"endpoint_gateway.app", "endpoint_gateway.testing"}


def test_importing_core_does_not_import_framework_or_adapters():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import sys",
                    "import endpoint_gateway.core.service",
                    "assert 'fastapi' not in sys.modules",
                    "assert 'pydantic' not in sys.modules",
                    "assert 'endpoint_gateway.app' not in sys.modules",
                ]
            ),
        ],
        check=True,
    )
