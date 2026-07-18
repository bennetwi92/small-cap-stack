"""Unit tests for the agent-PR auto-merge policy (``scripts/agent_pr_risk.py``).

The script lives outside the package (it is CI glue, not product code), so we load
it by path. The policy is fail-closed, so these tests pin both the "merge" and the
many "hold" branches — a wrong call here would auto-merge trading-engine changes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "agent_pr_risk.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_pr_risk", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


risk = _load()


# --- the happy path: a genuinely trivial change auto-merges ---------------------


def test_trivial_doc_change_auto_merges() -> None:
    ok, reason = risk.evaluate(["research/notes.md", "README.md"], 12, ["trivial", "docs"])
    assert ok is True
    assert "low-risk" in reason


def test_no_strategy_label_is_enough() -> None:
    # "labelled trivial OR has no strategy label" — an unlabelled issue qualifies.
    ok, _ = risk.evaluate(["src/small_cap_stack/util.py"], 20, [])
    assert ok is True


def test_trivial_label_overrides_strategy_label() -> None:
    ok, _ = risk.evaluate(["research/x.md"], 5, ["strategy", "trivial"])
    assert ok is True


# --- fail-closed: sensitive paths always hold ----------------------------------


def test_engine_code_holds() -> None:
    ok, reason = risk.evaluate(["src/small_cap_stack/bullflag/day.py"], 3, ["trivial"])
    assert ok is False
    assert "sensitive" in reason


def test_config_file_holds() -> None:
    ok, _ = risk.evaluate(["src/small_cap_stack/config.py"], 2, ["trivial"])
    assert ok is False


def test_workflow_file_holds() -> None:
    ok, _ = risk.evaluate([".github/workflows/ci.yml"], 4, ["trivial"])
    assert ok is False


def test_deploy_script_holds() -> None:
    ok, _ = risk.evaluate(["deploy/RUNBOOK.md"], 6, ["trivial"])
    assert ok is False


def test_infra_scripts_hold() -> None:
    ok, _ = risk.evaluate(["scripts/board.sh"], 6, ["trivial"])
    assert ok is False


def test_engine_by_name_holds() -> None:
    ok, _ = risk.evaluate(["src/small_cap_stack/strategy_helpers.py"], 6, ["trivial"])
    assert ok is False


# --- fail-closed: size and labels ----------------------------------------------


def test_over_line_cap_holds() -> None:
    ok, reason = risk.evaluate(["research/big.md"], 51, ["trivial"])
    assert ok is False
    assert "51" in reason


def test_at_line_cap_merges() -> None:
    ok, _ = risk.evaluate(["research/big.md"], 50, ["trivial"])
    assert ok is True


def test_strategy_label_without_trivial_holds() -> None:
    ok, reason = risk.evaluate(["research/x.md"], 5, ["strategy"])
    assert ok is False
    assert "strategy" in reason


def test_empty_diff_holds() -> None:
    ok, _ = risk.evaluate([], 0, ["trivial"])
    assert ok is False


# --- the CLI contract the workflow depends on ----------------------------------


def test_cli_emits_json() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--changed-files",
            "research/a.md\nresearch/b.md",
            "--changed-lines",
            "10",
            "--issue-labels",
            "trivial,docs",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["automerge"] is True
    assert "reason" in payload


def test_cli_holds_on_engine() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--changed-files",
            "src/small_cap_stack/bullflag/day.py",
            "--changed-lines",
            "3",
            "--issue-labels",
            "trivial",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["automerge"] is False
