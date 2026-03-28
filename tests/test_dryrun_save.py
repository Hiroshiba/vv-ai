"""dry-run と finally-save 保証の単体テスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from vv_ai.cli import _run_ready_execution
from vv_ai.command_handler import (
    _handle_implement_issue_post_execution,
    _handle_implement_pr_post_execution,
    _handle_issue_post_execution,
    _post_response_comment,
)
from vv_ai.config import VVAIConfig
from vv_ai.execution import ExecutionResult, ExecutionStatus, SavedExecutionArtifacts
from vv_ai.metrics_artifact import (
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.provider import ProviderSpec, ResolvedProvider
from vv_ai.report_artifact import ReportSections
from vv_ai.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.session import ResolvedSession, SessionKey, SessionStateRef


def _make_command(**overrides: object) -> ResolvedCommand:
    """テスト用の最小 ResolvedCommand を生成する。"""
    defaults: dict[str, object] = {
        "event_name": "local",
        "command": "plan",
        "has_target": True,
        "dry_run": True,
        "target": ResolvedTarget(
            backend="github",
            kind="issue",
            canonical_id="org/repo#1",
            repository_full_name="org/repo",
            number=1,
        ),
    }
    defaults.update(overrides)
    return ResolvedCommand.model_validate(defaults)


def _make_provider() -> ResolvedProvider:
    """テスト用の最小 ResolvedProvider を生成する。"""
    return ResolvedProvider(
        spec=ProviderSpec(
            name="codex",
            api_key_env="VV_OPENAI_API_KEY",
            api_key_file_env="VV_OPENAI_API_KEY_FILE",
            cli_command="codex",
            supports_session_resume=True,
            supports_compact=True,
        ),
        source="explicit",
    )


def _make_session_key() -> SessionKey:
    """テスト用の最小 SessionKey を生成する。"""
    return SessionKey(
        backend="github",
        target_key="org/repo#1",
        provider="codex",
        lane="main",
        canonical_key="github/org/repo#1/codex/main",
    )


def _make_resolved_session() -> ResolvedSession:
    """テスト用の最小 ResolvedSession を生成する。"""
    return ResolvedSession(
        mode="new",
        lane="main",
        key=_make_session_key(),
        restore_strategy="new",
        save_manifest_path="/tmp/manifest.json",
    )


def _make_ready_execution(**overrides: object) -> ReadyExecution:
    """テスト用の最小 ReadyExecution を生成する。"""
    defaults: dict[str, object] = {
        "command": _make_command(),
        "config": VVAIConfig(allowed_users=["Hiroshiba"]),
        "resolved_provider": _make_provider(),
        "resolved_session": _make_resolved_session(),
        "workflow_id": "test-run-1",
    }
    defaults.update(overrides)
    return ReadyExecution.model_validate(defaults)


def _make_execution_result(
    status: ExecutionStatus,
    response_text: str | None = None,
) -> ExecutionResult:
    """テスト用の最小 ExecutionResult を生成する。"""
    return ExecutionResult(
        status=status,
        report_sections=ReportSections(
            summary="s",
            changes="c",
            decisions="d",
            validation="v",
            risks_open_questions="r",
            next_actions="n",
            notes="t",
        ),
        usage=MetricsUsage(),
        behavior=MetricsBehavior(),
        tools={},
        steps={},
        provider_specific=ProviderSpecificMetrics(),
        state_ref=SessionStateRef(),
        provider_session_path=None,
        allow_edits_notice_posted=False,
        response_text=response_text,
    )


def _make_saved_artifacts() -> MagicMock:
    """テスト用の SavedExecutionArtifacts 代替を生成する。"""
    return MagicMock(spec=SavedExecutionArtifacts)


class TestDryRunSuppression:
    def test_dryrun_suppresses_implement_issue_push(self) -> None:
        ready = _make_ready_execution(command=_make_command(command="implement"))
        result = _make_execution_result("success")
        github_client = MagicMock()

        with patch("vv_ai.command_handler.push_branch") as mock_push:
            _handle_implement_issue_post_execution(
                Path("/dummy"), ready, result, github_client, "vv-ai/issue-1-abc123"
            )
            mock_push.assert_not_called()

        github_client.create_pull_request.assert_not_called()

    def test_dryrun_suppresses_implement_pr_push(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", target=ResolvedTarget(
                backend="github",
                kind="pr",
                canonical_id="org/repo#2",
                repository_full_name="org/repo",
                number=2,
            ))
        )
        result = _make_execution_result("success")
        github_client = MagicMock()

        with patch("vv_ai.command_handler.push_branch") as mock_push:
            _handle_implement_pr_post_execution(
                Path("/dummy"), ready, result, github_client, "feature-branch", None, None
            )
            mock_push.assert_not_called()

    def test_dryrun_suppresses_issue_creation(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(
                command="issue",
                instruction="テスト",
                repo="org/repo",
                has_target=False,
                target=None,
            )
        )
        result = _make_execution_result(
            "success",
            response_text="TITLE: test\nBODY:\ntest body",
        )
        github_client = MagicMock()

        _handle_issue_post_execution(ready, result, github_client)

        github_client.create_issue.assert_not_called()

    def test_dryrun_suppresses_response_comment(self) -> None:
        ready = _make_ready_execution(command=_make_command(command="plan"))
        result = _make_execution_result("success", response_text="計画です")
        github_client = MagicMock()

        _post_response_comment(ready, result, github_client)

        github_client.create_issue_comment.assert_not_called()

    def test_non_dryrun_posts_response_comment(self) -> None:
        ready = _make_ready_execution(command=_make_command(command="plan", dry_run=False))
        result = _make_execution_result("success", response_text="計画です")
        github_client = MagicMock()

        _post_response_comment(ready, result, github_client)

        github_client.create_issue_comment.assert_called_once()


class TestFinallySaveGuarantee:
    @patch("vv_ai.cli.save_execution_artifacts")
    @patch("vv_ai.cli.run_command")
    def test_save_called_on_success(
        self,
        mock_run_command: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        mock_run_command.return_value = _make_execution_result("success")
        mock_save.return_value = _make_saved_artifacts()
        ready = _make_ready_execution()

        exit_code = _run_ready_execution(
            Path("/dummy"), ready, {}, preflight_duration_seconds=0.1
        )

        assert exit_code == 0
        mock_save.assert_called_once()

    @patch("vv_ai.cli.save_execution_artifacts")
    @patch("vv_ai.cli.run_command")
    def test_save_called_on_exception(
        self,
        mock_run_command: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        mock_run_command.side_effect = RuntimeError("テスト用エラー")
        mock_save.return_value = _make_saved_artifacts()
        ready = _make_ready_execution()

        exit_code = _run_ready_execution(
            Path("/dummy"), ready, {}, preflight_duration_seconds=0.1
        )

        assert exit_code == 1
        mock_save.assert_called_once()

    @patch("vv_ai.cli.save_execution_artifacts")
    @patch("vv_ai.cli.run_command")
    def test_save_called_on_keyboard_interrupt(
        self,
        mock_run_command: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        mock_run_command.side_effect = KeyboardInterrupt()
        mock_save.return_value = _make_saved_artifacts()
        ready = _make_ready_execution()

        exit_code = _run_ready_execution(
            Path("/dummy"), ready, {}, preflight_duration_seconds=0.1
        )

        assert exit_code == 130
        mock_save.assert_called_once()

    @patch("vv_ai.cli.save_execution_artifacts")
    @patch("vv_ai.cli.run_command")
    def test_failure_result_status(
        self,
        mock_run_command: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        mock_run_command.side_effect = RuntimeError("失敗")
        mock_save.return_value = _make_saved_artifacts()
        ready = _make_ready_execution()

        _run_ready_execution(
            Path("/dummy"), ready, {}, preflight_duration_seconds=0.1
        )

        saved_result: ExecutionResult = mock_save.call_args[0][3]
        assert saved_result.status == "failure"

    @patch("vv_ai.cli.save_execution_artifacts")
    @patch("vv_ai.cli.run_command")
    def test_cancelled_result_status(
        self,
        mock_run_command: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        mock_run_command.side_effect = KeyboardInterrupt()
        mock_save.return_value = _make_saved_artifacts()
        ready = _make_ready_execution()

        _run_ready_execution(
            Path("/dummy"), ready, {}, preflight_duration_seconds=0.1
        )

        saved_result: ExecutionResult = mock_save.call_args[0][3]
        assert saved_result.status == "cancelled"
