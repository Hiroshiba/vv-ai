"""workflow preflight の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.inputs.resolve import ResolvedCommand, ResolvedControlLabel
from vv_ai.workflow.preflight import (
    PreflightError,
    ReadyControlExecution,
    ReadyExecution,
    SilentSkip,
    _resolve_workflow_id,
    run_preflight,
)


def _write_config(tmp_path) -> None:
    (tmp_path / "vv-ai.yml").write_text(
        "allowed_users:\n  - Hiroshiba\n",
        encoding="utf-8",
    )


def _make_command(event_name: str) -> ResolvedCommand:
    return ResolvedCommand(
        event_name=event_name,
        command="reply",
        has_target=False,
    )


def _make_labeled_command(actor: str, actor_id: int | None) -> ResolvedCommand:
    return ResolvedCommand(
        event_name="issues",
        command="next",
        has_target=True,
        target_type="issue",
        target_number=42,
        repository_full_name="org/repo",
        actor=actor,
        actor_id=actor_id,
        trigger_label_name="vv-ai:next",
        trigger_event_created_at="2026-05-18T04:00:00Z",
        skip_api_key_check=True,
    )


def _make_control_label(actor: str, actor_id: int | None) -> ResolvedControlLabel:
    return ResolvedControlLabel(
        event_name="issues",
        control_label_name="vv-ai:auto",
        target_type="issue",
        target_number=42,
        has_target=True,
        repository_full_name="org/repo",
        actor=actor,
        actor_id=actor_id,
        trigger_label_name="vv-ai:auto",
        trigger_event_created_at="2026-05-18T04:00:00Z",
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


class TestRunPreflight:
    def test_internal_bot_command_label_is_ready(self, tmp_path) -> None:
        _write_config(tmp_path)

        result = run_preflight(
            tmp_path,
            _make_labeled_command("vv-ai-public-read-github-app[bot]", 274163862),
            {},
        )

        assert isinstance(result, ReadyExecution)
        assert result.command.command == "next"

    def test_other_bot_command_label_is_silent_skip(self, tmp_path) -> None:
        _write_config(tmp_path)

        result = run_preflight(
            tmp_path,
            _make_labeled_command("vv-ai-public-read-github-app[bot]", 999),
            {},
        )

        assert isinstance(result, SilentSkip)
        assert result.reason == "unauthorized_label"

    def test_allowed_user_control_label_is_ready(self, tmp_path) -> None:
        _write_config(tmp_path)

        result = run_preflight(tmp_path, _make_control_label("Hiroshiba", 1), {})

        assert isinstance(result, ReadyControlExecution)
        assert result.control.control_label_name == "vv-ai:auto"

    def test_internal_bot_control_label_is_silent_skip(self, tmp_path) -> None:
        _write_config(tmp_path)

        result = run_preflight(
            tmp_path,
            _make_control_label("vv-ai-public-read-github-app[bot]", 274163862),
            {},
        )

        assert isinstance(result, SilentSkip)
        assert result.reason == "unauthorized_label"
