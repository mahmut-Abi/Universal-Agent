from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.unit
def test_report_test_ratio_counts_behavior_contract_unit_markers(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    tests.joinpath("test_sample.py").write_text(
        """
import pytest

pytestmark = pytest.mark.unit

@pytest.mark.behavior
def test_behavior():
    pass

@pytest.mark.contract
def test_contract():
    pass

async def test_unit_by_module_marker():
    pass

def helper():
    pass
""".lstrip(),
        encoding="utf-8",
    )
    module = _load_report_module()

    assert module.test_marker_counts(tests) == Counter(
        {"behavior": 1, "contract": 1, "unit": 3}
    )


def _load_report_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "report-test-ratio.py"
    spec = importlib.util.spec_from_file_location("report_test_ratio", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
