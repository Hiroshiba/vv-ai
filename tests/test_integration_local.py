"""local 実行の統合テスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from vv_ai.cli import main
from vv_ai.execution import ExecutionResult, ExecutionStatus, SavedExecutionArtifacts
from vv_ai.resolve import BackendName
from vv_ai.metrics_artifact import MetricsBehavior, MetricsUsage, ProviderSpecificMetrics
from vv_ai.report_artifact import ReportSections
from vv_ai.session import ResolvedSession, SessionKey, SessionStateRef


def _write_config(tmp_path: Path) -> None:
    """tmp_path に最小の vv-ai.yml を配置する。"""
    config_path = tmp_path / "vv-ai.yml"
    config_path.write_text("allowed_users:\n  - Hiroshiba\n", encoding="utf-8")


def _make_execution_result(
    status: ExecutionStatus,
    response_text: str | None,
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


def _make_resolved_session(
    backend: BackendName,
    target_key: str,
    provider: str,
) -> ResolvedSession:
    """テスト用の最小 ResolvedSession を生成する。"""
    lane = "main"
    canonical_key = f"{backend}/{target_key}/{provider}/{lane}"
    return ResolvedSession(
        mode="new",
        lane=lane,
        key=SessionKey(
            backend=backend,
            target_key=target_key,
            provider=provider,
            lane=lane,
            canonical_key=canonical_key,
        ),
        restore_strategy="new",
        save_manifest_path=f"/tmp/{canonical_key}/manifest.json",
    )


class TestGitHubTargetDryRun:
    def test_plan_github_issue_exits_zero(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "plan",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--instruction", "テスト指示",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "テスト応答")
        env_patch = {"VV_OPENAI_API_KEY": "dummy-key"}

        with (
            patch("vv_ai.cli.find_repo_root", return_value=tmp_path),
            patch("vv_ai.cli.resolve_session", return_value=session),
            patch("vv_ai.command_handler.execute_provider", return_value=result) as mock_provider,
            patch("vv_ai.cli.save_execution_artifacts", return_value=MagicMock(spec=SavedExecutionArtifacts)) as mock_save,
            patch("vv_ai.command_handler.build_github_client", return_value=MagicMock()),
            patch.dict("os.environ", env_patch),
        ):
            exit_code = main(argv)

        assert exit_code == 0
        mock_provider.assert_called_once()
        mock_save.assert_called_once()

        ready_execution = mock_provider.call_args[0][1]
        assert ready_execution.command.command == "plan"
        assert ready_execution.command.dry_run is True
        assert ready_execution.command.target is not None
        assert ready_execution.command.target.backend == "github"
        assert ready_execution.command.target.canonical_id == "org/repo#1"


class TestLocalTargetDryRun:
    def test_reply_local_issue_exits_zero(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        issue_dir = tmp_path / ".vv-ai" / "issues" / "test-1"
        issue_dir.mkdir(parents=True)
        (issue_dir / "comments").mkdir()
        (issue_dir / "issue.md").write_text("テスト Issue", encoding="utf-8")

        argv = [
            "--command", "reply",
            "--target-url", str(issue_dir),
            "--instruction", "テスト指示",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("local", "issue:test-1", "codex")
        result = _make_execution_result("success", "テスト応答")
        env_patch = {"VV_OPENAI_API_KEY": "dummy-key"}

        with (
            patch("vv_ai.cli.find_repo_root", return_value=tmp_path),
            patch("vv_ai.cli.resolve_session", return_value=session),
            patch("vv_ai.command_handler.execute_provider", return_value=result) as mock_provider,
            patch("vv_ai.cli.save_execution_artifacts", return_value=MagicMock(spec=SavedExecutionArtifacts)),
            patch.dict("os.environ", env_patch),
        ):
            exit_code = main(argv)

        assert exit_code == 0
        mock_provider.assert_called_once()

        ready_execution = mock_provider.call_args[0][1]
        assert ready_execution.command.target is not None
        assert ready_execution.command.target.backend == "local"
        assert ready_execution.command.target.kind == "issue"
        assert ready_execution.command.target.local_id == "test-1"


class TestInputErrors:
    def test_missing_target_exits_2(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "plan",
            "--instruction", "テスト指示",
            "--provider", "codex",
            "--session_mode", "new",
        ]

        with (
            patch("vv_ai.cli.find_repo_root", return_value=tmp_path),
            patch.dict("os.environ", {"VV_OPENAI_API_KEY": "dummy-key"}),
        ):
            exit_code = main(argv)

        assert exit_code == 2

    def test_missing_instruction_exits_2(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "reply",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
        ]

        with (
            patch("vv_ai.cli.find_repo_root", return_value=tmp_path),
            patch.dict("os.environ", {"VV_OPENAI_API_KEY": "dummy-key"}),
        ):
            exit_code = main(argv)

        assert exit_code == 2


class TestProviderFailure:
    def test_provider_error_exits_1_and_saves(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "plan",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--instruction", "テスト指示",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        env_patch = {"VV_OPENAI_API_KEY": "dummy-key"}

        with (
            patch("vv_ai.cli.find_repo_root", return_value=tmp_path),
            patch("vv_ai.cli.resolve_session", return_value=session),
            patch("vv_ai.command_handler.execute_provider", side_effect=RuntimeError("provider crashed")),
            patch("vv_ai.cli.save_execution_artifacts", return_value=MagicMock(spec=SavedExecutionArtifacts)) as mock_save,
            patch("vv_ai.command_handler.build_github_client", return_value=MagicMock()),
            patch.dict("os.environ", env_patch),
        ):
            exit_code = main(argv)

        assert exit_code == 1
        mock_save.assert_called_once()

        saved_result: ExecutionResult = mock_save.call_args[0][3]
        assert saved_result.status == "failure"
