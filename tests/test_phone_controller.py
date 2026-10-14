"""Exercise the shipped browser script using Node's dependency-free VM."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_phone_controller_browser_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for browser controller contract tests")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "tests/phone_controller.test.cjs"], cwd=root,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
