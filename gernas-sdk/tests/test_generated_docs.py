"""The generated architecture docs (HTML + instrumentation map) match the source and checklist.md."""

from __future__ import annotations

import subprocess
import sys

import pytest

from conftest import ROOT

SCRIPT = ROOT / "scripts" / "build_architecture_doc.py"


@pytest.mark.skipif(not (ROOT.parent / "checklist.md").exists(), reason="needs the full repository (checklist.md)")
def test_architecture_docs_are_up_to_date():
    result = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr or result.stdout
