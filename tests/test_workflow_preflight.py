"""workflow preflight の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.inputs.resolve import ResolvedCommand
from vv_ai.workflow.preflight import PreflightError, _resolve_workflow_id


def _make_command(event_name: str) -> ResolvedCommand:
    return ResolvedCommand(
        event_name=event_name,
        command="reply",
        has_target=False,
    )


class TestResolveWorkflowId:
    def test_non_local_run_id(self) -> None:
        workflow_id = _resolve_workflow_id(
            _make_command("workflow_dispatch"),
            {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"},
        )

        assert workflow_id == "run-123-attempt-2"

    def test_non_local_missing_run_id_uses_debug_id(self) -> None:
        workflow_id = _resolve_workflow_id(_make_command("workflow_dispatch"), {})

        assert workflow_id.startswith("debug-")

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(PreflightError, match="GITHUB_RUN_ID"):
            _resolve_workflow_id(
                _make_command("workflow_dispatch"),
                {"GITHUB_RUN_ID": ""},
            )

    def test_empty_run_attempt_raises(self) -> None:
        with pytest.raises(PreflightError, match="GITHUB_RUN_ATTEMPT"):
            _resolve_workflow_id(
                _make_command("workflow_dispatch"),
                {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": ""},
            )
