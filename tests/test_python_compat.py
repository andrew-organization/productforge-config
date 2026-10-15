"""The host-side scripts run on Python versions older than the one the suite runs on.

The starter runs on Lambda's python3.12 and the idle stop and the supervisor on the host's python3 (Ubuntu 24.04
has 3.12; the README says 3.10 or later), so they must parse as 3.10. The suite itself runs on a newer Python, which
accepts syntax those do not, such as an `except A, B:` without parentheses. black, targeting that newer Python, strips
the parentheses, so the shipped scripts mark those lines `# fmt: skip`.
"""

import ast
from pathlib import Path

import pytest

OLDEST = (3, 10)
SCRIPTS = ["supervisor/starter/handler.py", "supervisor/idle_stop.py", "supervisor/runner_supervisor.py"]


def parses_as_oldest(source: str) -> bool:
    try:
        ast.parse(source, feature_version=OLDEST)
    except SyntaxError:
        return False
    return True


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_host_side_scripts_parse_as_python_3_10(shipped: Path, script: str) -> None:
    assert parses_as_oldest((shipped / script).read_text())


def test_the_check_rejects_an_except_without_parentheses() -> None:
    assert not parses_as_oldest("try:\n    pass\nexcept ValueError, KeyError:\n    pass\n")
    assert parses_as_oldest("try:\n    pass\nexcept (ValueError, KeyError):\n    pass\n")
