"""Both documented and compatibility commands expose the same verifier CLI."""
import subprocess
import sys

import pytest

from tests.conftest import ROOT


@pytest.mark.parametrize("name", ["verify_portable", "verify_execution_portable"])
@pytest.mark.parametrize("module", [False, True])
def test_portable_verifier_help(name, module):
    command = [sys.executable]
    command += ["-m", f"scripts.{name}"] if module else [str(ROOT / "scripts" / f"{name}.py")]
    result = subprocess.run(command + ["--help"], cwd=ROOT if module else ROOT.parent,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "--execution-preview" in result.stdout
    assert "--chat-preview" in result.stdout
