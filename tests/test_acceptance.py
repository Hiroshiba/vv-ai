"""主要シナリオの受け入れ確認テスト。"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vv_ai.commands.runner import run_command
from vv_ai.cli import main
from vv_ai.config import VVAIConfig
from vv_ai.artifacts.execution import SavedExecutionArtifacts
from vv_ai.executions.result import ExecutionResult, ExecutionStatus
from vv_ai.backends.github.models import (
    GitHubActor,
    GitHubComment,
    GitHubIssue,
    GitHubIssueTimelineEvent,
    GitHubPullRequest,
    RepoInfo,
)
from vv_ai.artifacts.metrics import MetricsBehavior, MetricsUsage, ProviderSpecificMetrics
from vv_ai.workflow.preflight import ReadyExecution
from vv_ai.providers.selection import ResolvedProvider, get_provider_spec
from vv_ai.artifacts.report import ReportSections
from vv_ai.inputs.resolve import BackendName, ResolvedCommand, ResolvedTarget
from vv_ai.sessions.models import ResolvedSession, SessionKey, SessionStateRef


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
        requested_mode="new",
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


def _make_github_pr(
    repo: str,
    number: int,
    head_ref: str,
) -> GitHubPullRequest:
    """テスト用の GitHubPullRequest を生成する。"""
    return GitHubPullRequest(
        repository_full_name=repo,
        number=number,
        title="テスト PR",
        body="テスト本文",
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url=f"https://github.com/{repo}/pull/{number}",
        head_ref_name=head_ref,
        base_ref_name="main",
        head_repository_full_name=repo,
        is_cross_repository=False,
        maintainer_can_modify=True,
    )


def _make_github_issue(repo: str, number: int) -> GitHubIssue:
    """テスト用 GitHubIssue を生成する。"""
    return GitHubIssue(
        id=number,
        repository_full_name=repo,
        number=number,
        title="テスト Issue",
        body="Issue 本文",
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url=f"https://github.com/{repo}/issues/{number}",
    )


def _make_github_comment(comment_id: int, body: str) -> GitHubComment:
    """テスト用 GitHubComment を生成する。"""
    return GitHubComment(
        id=comment_id,
        body=body,
        author=GitHubActor(login="Hiroshiba"),
        created_at="2026-05-08T00:00:00Z",
        updated_at="2026-05-08T00:00:00Z",
        url=f"https://github.com/org/repo/issues/1#issuecomment-{comment_id}",
    )


def _make_github_timeline_comment(
    event_id: int,
    body: str,
) -> GitHubIssueTimelineEvent:
    """テスト用 GitHubIssueTimelineEvent comment を生成する。"""
    return GitHubIssueTimelineEvent(
        id=event_id,
        event="commented",
        actor=GitHubActor(login="Hiroshiba"),
        created_at="2026-05-08T00:00:00Z",
        body=body,
        label_name=None,
    )


def _make_github_labeled_event(
    event_id: int,
    label_name: str,
) -> GitHubIssueTimelineEvent:
    """テスト用 GitHubIssueTimelineEvent label を生成する。"""
    return GitHubIssueTimelineEvent(
        id=event_id,
        event="labeled",
        label_name=label_name,
        actor=GitHubActor(login="Hiroshiba"),
        created_at="2026-05-08T00:00:00Z",
        body=None,
    )


def _make_ready_execution_for_label(dry_run: bool) -> ReadyExecution:
    """ラベル起動済みの ReadyExecution を生成する。"""
    target = ResolvedTarget(
        backend="github",
        kind="issue",
        canonical_id="github:org/repo#1",
        repository_full_name="org/repo",
        number=1,
        url="https://github.com/org/repo/issues/1",
    )
    command = ResolvedCommand(
        event_name="issues",
        command="confirm",
        target_type="issue",
        target_number=1,
        has_target=True,
        dry_run=dry_run,
        repository_full_name="org/repo",
        actor="Hiroshiba",
        trigger_label_name="vv-ai:confirm",
        target=target,
    )
    return ReadyExecution(
        command=command,
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=ResolvedProvider(
            spec=get_provider_spec("codex"),
            source="explicit",
        ),
        workflow_id="test-workflow",
    )


def _enter_common_patches(
    stack: contextlib.ExitStack,
    tmp_path: Path,
    session: ResolvedSession,
    result: ExecutionResult,
    github_client: MagicMock,
) -> MagicMock:
    """テスト共通のパッチ群を ExitStack に登録する。"""
    stack.enter_context(patch("vv_ai.cli.find_repo_root", return_value=tmp_path))
    stack.enter_context(patch("vv_ai.cli.resolve_session", return_value=session))
    execute_provider = stack.enter_context(
        patch("vv_ai.commands.runner.execute_provider", return_value=result)
    )
    stack.enter_context(
        patch(
            "vv_ai.cli.save_execution_artifacts",
            return_value=MagicMock(spec=SavedExecutionArtifacts),
        )
    )
    stack.enter_context(
        patch("vv_ai.commands.runner.build_github_client", return_value=github_client)
    )
    stack.enter_context(patch.dict("os.environ", {"VV_OPENAI_API_KEY": "dummy-key"}))
    if isinstance(github_client, MagicMock):
        github_client.get_repo_info.return_value = RepoInfo(
            is_fork=False, parent_full_name=None, parent_default_branch=None
        )
        github_client.get_issue.return_value = _make_github_issue("org/repo", 1)
        github_client.list_issue_comments.return_value = []
        github_client.list_issue_timeline_events.return_value = []
    return execute_provider


def _enter_next_patches(
    stack: contextlib.ExitStack,
    tmp_path: Path,
    session: ResolvedSession,
    result: ExecutionResult,
    github_client: MagicMock,
) -> tuple[MagicMock, MagicMock]:
    """next CLI テスト用のパッチ群を ExitStack に登録する。"""
    stack.enter_context(patch("vv_ai.cli.find_repo_root", return_value=tmp_path))
    resolve_session = stack.enter_context(
        patch("vv_ai.cli.resolve_session", return_value=session)
    )
    execute_provider = stack.enter_context(
        patch("vv_ai.commands.runner.execute_provider", return_value=result)
    )
    stack.enter_context(
        patch(
            "vv_ai.cli.save_execution_artifacts",
            return_value=MagicMock(spec=SavedExecutionArtifacts),
        )
    )
    stack.enter_context(
        patch("vv_ai.commands.runner.build_github_client", return_value=github_client)
    )
    stack.enter_context(
        patch("vv_ai.commands.next.build_github_client", return_value=github_client)
    )
    stack.enter_context(patch.dict("os.environ", {"VV_OPENAI_API_KEY": "dummy-key"}))
    github_client.get_repo_info.return_value = RepoInfo(
        is_fork=False,
        parent_full_name=None,
        parent_default_branch=None,
    )
    github_client.get_issue.return_value = _make_github_issue("org/repo", 1)
    github_client.list_issue_comments.return_value = []
    github_client.list_issue_timeline_events.return_value = []
    return resolve_session, execute_provider


class TestImplementIssueDryRun:
    """implement コマンド Issue 起点 dry-run のシナリオ。"""

    def test_exits_zero_with_branch_creation(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "implement",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--instruction", "この Issue を実装して",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "実装完了")
        mock_gh = MagicMock()

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, mock_gh)
            mock_branch = stack.enter_context(
                patch("vv_ai.commands.runner.create_and_checkout_branch")
            )
            exit_code = main(argv)

        assert exit_code == 0
        mock_branch.assert_called_once()
        mock_gh.create_issue_comment.assert_not_called()


class TestImplementPRDryRun:
    """implement コマンド PR 起点 dry-run のシナリオ。"""

    def test_exits_zero_with_branch_checkout(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "implement",
            "--target-url", "https://github.com/org/repo/pull/5",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#5", "codex")
        result = _make_execution_result("success", "追加コミット完了")
        pr = _make_github_pr("org/repo", 5, "feature-branch")

        mock_gh = MagicMock()
        mock_gh.get_pull_request.return_value = pr

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, github_client=mock_gh)
            mock_checkout = stack.enter_context(
                patch("vv_ai.commands.runner.fetch_and_checkout_branch")
            )
            exit_code = main(argv)

        assert exit_code == 0
        mock_gh.get_pull_request.assert_called_once_with("org/repo", 5)
        mock_checkout.assert_called_once()
        mock_gh.create_issue_comment.assert_not_called()


class TestReviewDryRun:
    """review コマンド dry-run のシナリオ。"""

    def test_review_pr_exits_zero(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "review",
            "--target-url", "https://github.com/org/repo/pull/10",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#10", "codex")
        result = _make_execution_result("success", "レビューコメント")
        mock_gh = MagicMock()
        mock_gh.get_pull_request.return_value = _make_github_pr(
            "org/repo", 10, "feature-branch"
        )

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, mock_gh)
            exit_code = main(argv)

        assert exit_code == 0

    def test_review_issue_exits_1(self, tmp_path: Path) -> None:
        """review コマンドに Issue target を指定すると exit code 1。"""
        _write_config(tmp_path)
        argv = [
            "--command", "review",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "レビューコメント")

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, MagicMock())
            exit_code = main(argv)

        assert exit_code == 1


class TestAddressDryRun:
    """address コマンド dry-run のシナリオ。"""

    def test_address_pr_exits_zero(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "address",
            "--target-url", "https://github.com/org/repo/pull/10",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#10", "codex")
        result = _make_execution_result("success", "対応しました")
        mock_gh = MagicMock()
        mock_gh.get_pull_request.return_value = _make_github_pr(
            "org/repo", 10, "feature-branch"
        )

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, mock_gh)
            mock_checkout = stack.enter_context(
                patch("vv_ai.commands.runner.fetch_and_checkout_branch")
            )
            exit_code = main(argv)

        assert exit_code == 0
        mock_checkout.assert_called_once_with(tmp_path, "feature-branch")

    def test_address_issue_exits_1(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "address",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "未使用")

        with contextlib.ExitStack() as stack:
            execute_provider = _enter_common_patches(
                stack, tmp_path, session, result, MagicMock()
            )
            exit_code = main(argv)

        assert exit_code == 1
        execute_provider.assert_not_called()


class TestIssueDryRun:
    """issue コマンド dry-run のシナリオ。"""

    def test_issue_command_exits_zero(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "issue",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--instruction", "この不具合を Issue 化して",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
            "--repo", "org/repo",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        ai_response = "TITLE: テスト Issue\nBODY:\nこれはテスト本文です。"
        result = _make_execution_result("success", ai_response)

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, MagicMock())
            exit_code = main(argv)

        assert exit_code == 0


class TestNextDryRun:
    """next コマンド dry-run のシナリオ。"""

    def test_issue_history_empty_runs_as_confirm_with_cli_options(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "## 要望確認\n\n確認しました")
        mock_gh = MagicMock()
        mock_gh.get_issue_parent_number.return_value = None

        with contextlib.ExitStack() as stack:
            resolve_session, execute_provider = _enter_next_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            exit_code = main(argv)

        session_command = resolve_session.call_args.args[2]
        ready_execution = execute_provider.call_args.args[1]
        assert exit_code == 0
        assert session_command.command == "confirm"
        assert ready_execution.command.command == "confirm"
        assert ready_execution.command.provider == "codex"
        assert ready_execution.command.session_mode == "new"
        assert ready_execution.command.dry_run is True

    def test_pr_history_empty_runs_as_review_before_session_resolution(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/pull/5",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#5", "codex")
        result = _make_execution_result("success", "## レビュー\n\nレビューしました")
        mock_gh = MagicMock()
        mock_gh.get_pull_request.return_value = _make_github_pr(
            "org/repo",
            5,
            "feature-branch",
        )

        with contextlib.ExitStack() as stack:
            resolve_session, execute_provider = _enter_next_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            exit_code = main(argv)

        session_command = resolve_session.call_args.args[2]
        ready_execution = execute_provider.call_args.args[1]
        assert exit_code == 0
        assert session_command.command == "review"
        assert ready_execution.command.command == "review"

    def test_pr_after_review_runs_as_address(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/pull/5",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#5", "codex")
        result = _make_execution_result("success", "対応しました")
        mock_gh = MagicMock()
        mock_gh.get_pull_request.return_value = _make_github_pr(
            "org/repo",
            5,
            "feature-branch",
        )

        with contextlib.ExitStack() as stack:
            _, execute_provider = _enter_next_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_timeline_comment(1000, "@vv-ai review"),
            ]
            mock_checkout = stack.enter_context(
                patch("vv_ai.commands.runner.fetch_and_checkout_branch")
            )
            exit_code = main(argv)

        ready_execution = execute_provider.call_args.args[1]
        assert exit_code == 0
        assert ready_execution.command.command == "address"
        mock_checkout.assert_called_once()

    def test_issue_after_breakdown_exits_two_without_provider(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "未使用")
        mock_gh = MagicMock()
        mock_gh.get_issue_parent_number.return_value = None

        with contextlib.ExitStack() as stack:
            resolve_session, execute_provider = _enter_next_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_timeline_comment(1000, "@vv-ai confirm"),
                _make_github_timeline_comment(1001, "@vv-ai requirements"),
                _make_github_timeline_comment(1002, "@vv-ai arch"),
                _make_github_timeline_comment(1003, "@vv-ai detail"),
                _make_github_timeline_comment(1004, "@vv-ai breakdown"),
            ]
            exit_code = main(argv)

        assert exit_code == 2
        resolve_session.assert_not_called()
        execute_provider.assert_not_called()

    def test_issue_after_detail_next_decision_breakdown_runs_breakdown(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        breakdown_dir = tmp_path / "hiho_temp" / "hiho.test"
        breakdown_dir.mkdir(parents=True)
        (breakdown_dir / "01.md").write_text(
            "TITLE: テストタスク\nBODY:\n本文",
            encoding="utf-8",
        )
        decision_result = _make_execution_result("success", "COMMAND: breakdown")
        breakdown_result = _make_execution_result(
            "success",
            f"BREAKDOWN_DIR: {breakdown_dir}",
        )
        mock_gh = MagicMock()
        mock_gh.get_issue_parent_number.return_value = None

        with contextlib.ExitStack() as stack:
            _, execute_provider = _enter_next_patches(
                stack,
                tmp_path,
                session,
                decision_result,
                mock_gh,
            )
            execute_provider.side_effect = [decision_result, breakdown_result]
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_timeline_comment(1000, "@vv-ai confirm"),
                _make_github_timeline_comment(1001, "@vv-ai requirements"),
                _make_github_timeline_comment(1002, "@vv-ai arch"),
                _make_github_timeline_comment(1003, "@vv-ai detail"),
            ]
            exit_code = main(argv)

        ready_execution = execute_provider.call_args_list[1].args[1]
        assert exit_code == 0
        assert execute_provider.call_count == 2
        assert ready_execution.command.command == "breakdown"

    def test_issue_after_detail_next_decision_implement_runs_implement(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        decision_result = _make_execution_result("success", "COMMAND: implement")
        implement_result = _make_execution_result("success", "未使用")
        mock_gh = MagicMock()
        mock_gh.get_issue_parent_number.return_value = None

        with contextlib.ExitStack() as stack:
            _, execute_provider = _enter_next_patches(
                stack,
                tmp_path,
                session,
                decision_result,
                mock_gh,
            )
            execute_provider.side_effect = [decision_result, implement_result]
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_timeline_comment(1000, "@vv-ai confirm"),
                _make_github_timeline_comment(1001, "@vv-ai requirements"),
                _make_github_timeline_comment(1002, "@vv-ai arch"),
                _make_github_timeline_comment(1003, "@vv-ai detail"),
            ]
            mock_branch = stack.enter_context(
                patch("vv_ai.commands.runner.create_and_checkout_branch")
            )
            exit_code = main(argv)

        ready_execution = execute_provider.call_args_list[1].args[1]
        assert exit_code == 0
        assert execute_provider.call_count == 2
        assert ready_execution.command.command == "implement"
        mock_branch.assert_called_once()

    def test_issue_after_detail_next_decision_continues_decision_session(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "inherit",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        session.restore_strategy = "inherit"
        session.restored_provider_session_path = "/tmp/restored-old-session"
        session.state_ref = SessionStateRef(provider_session_id="detail-session")
        decision_result = _make_execution_result(
            "success",
            "COMMAND: implement",
        ).model_copy(
            update={"state_ref": SessionStateRef(provider_session_id="decision-session")}
        )
        implement_result = _make_execution_result("success", "未使用")
        mock_gh = MagicMock()
        mock_gh.get_issue_parent_number.return_value = None
        provider_call_count = 0

        def execute_provider_mock(
            repo_root: Path,
            ready_execution: ReadyExecution,
            env: dict[str, str],
            preflight_duration_seconds: float,
            provider_prompt: str,
        ) -> ExecutionResult:
            nonlocal provider_call_count
            provider_call_count += 1
            if provider_call_count == 1:
                assert ready_execution.resolved_session is session
                assert session.restored_provider_session_path == "/tmp/restored-old-session"
                return decision_result
            assert ready_execution.resolved_session is session
            assert session.restore_strategy == "inherit"
            assert session.state_ref is not None
            assert session.state_ref.provider_session_id == "decision-session"
            assert session.restored_provider_session_path is None
            return implement_result

        with contextlib.ExitStack() as stack:
            _enter_next_patches(
                stack,
                tmp_path,
                session,
                decision_result,
                mock_gh,
            )
            execute_provider = stack.enter_context(
                patch(
                    "vv_ai.commands.runner.execute_provider",
                    side_effect=execute_provider_mock,
                )
            )
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_timeline_comment(1000, "@vv-ai confirm"),
                _make_github_timeline_comment(1001, "@vv-ai requirements"),
                _make_github_timeline_comment(1002, "@vv-ai arch"),
                _make_github_timeline_comment(1003, "@vv-ai detail"),
            ]
            stack.enter_context(patch("vv_ai.commands.runner.create_and_checkout_branch"))
            exit_code = main(argv)

        assert exit_code == 0
        assert execute_provider.call_count == 2

    def test_issue_after_detail_next_invalid_decision_exits_one(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        argv = [
            "--command", "next",
            "--target-url", "https://github.com/org/repo/issues/1",
            "--provider", "codex",
            "--session_mode", "new",
            "--dry-run",
        ]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        decision_result = _make_execution_result("success", "COMMAND: confirm")
        mock_gh = MagicMock()
        mock_gh.get_issue_parent_number.return_value = None

        with contextlib.ExitStack() as stack:
            _, execute_provider = _enter_next_patches(
                stack,
                tmp_path,
                session,
                decision_result,
                mock_gh,
            )
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_timeline_comment(1000, "@vv-ai confirm"),
                _make_github_timeline_comment(1001, "@vv-ai requirements"),
                _make_github_timeline_comment(1002, "@vv-ai arch"),
                _make_github_timeline_comment(1003, "@vv-ai detail"),
            ]
            exit_code = main(argv)

        assert exit_code == 1
        execute_provider.assert_called_once()


class TestIssueCommentEvent:
    """issue_comment イベント経由の実行シナリオ。"""

    def _write_event_file(self, tmp_path: Path, comment_body: str, sender: str) -> Path:
        """issue_comment event payload を JSON ファイルとして書き出す。"""
        payload = {
            "action": "created",
            "comment": {
                "id": 1001,
                "body": comment_body,
                "user": {"login": sender},
            },
            "issue": {
                "number": 42,
                "pull_request": None,
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": sender},
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(payload), encoding="utf-8")
        return event_path

    def test_authorized_user_exits_zero(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_event_file(
            tmp_path, "@vv-ai arch 基本設計して", "Hiroshiba"
        )
        argv = [
            "--event", "issue_comment",
            "--event-file", str(event_path),
        ]
        session = _make_resolved_session("github", "org/repo#42", "codex")
        result = _make_execution_result("success", "方針です")

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, MagicMock())
            exit_code = main(argv)

        assert exit_code == 0

    def test_provider_prompt_includes_target_context(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        event_path = self._write_event_file(
            tmp_path, "@vv-ai arch 基本設計して", "Hiroshiba"
        )
        argv = [
            "--event", "issue_comment",
            "--event-file", str(event_path),
        ]
        session = _make_resolved_session("github", "org/repo#42", "codex")
        result = _make_execution_result("success", "方針です")
        mock_gh = MagicMock()

        with contextlib.ExitStack() as stack:
            execute_provider = _enter_common_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            mock_gh.get_issue.return_value = _make_github_issue("org/repo", 42)
            mock_gh.list_issue_comments.return_value = [
                _make_github_comment(1000, "過去コメント"),
                _make_github_comment(1001, "@vv-ai arch 基本設計して"),
            ]
            exit_code = main(argv)

        provider_prompt = execute_provider.call_args.args[4]
        assert exit_code == 0
        assert "対象の Issue / PR コンテキスト" in provider_prompt
        assert "テスト Issue" in provider_prompt
        assert "Issue 本文" in provider_prompt
        assert "過去コメント" in provider_prompt
        assert "@vv-ai arch 基本設計して" not in provider_prompt

    def test_unauthorized_user_silent_skip(self, tmp_path: Path) -> None:
        """allowed_users 外のユーザーは silent skip で exit code 0。"""
        _write_config(tmp_path)
        event_path = self._write_event_file(
            tmp_path, "@vv-ai arch 基本設計して", "unknown-user"
        )
        argv = [
            "--event", "issue_comment",
            "--event-file", str(event_path),
        ]

        with (
            patch("vv_ai.cli.find_repo_root", return_value=tmp_path),
            patch.dict("os.environ", {"VV_OPENAI_API_KEY": "dummy-key"}),
        ):
            exit_code = main(argv)

        assert exit_code == 0


class TestLabelEvent:
    """ラベル起動イベント経由の実行シナリオ。"""

    def _write_issue_labeled_event(self, tmp_path: Path, label_name: str) -> Path:
        """issues labeled event payload を JSON ファイルとして書き出す。"""
        payload = {
            "action": "labeled",
            "issue": {"number": 1, "updated_at": "2026-05-08T00:00:00Z"},
            "label": {"name": label_name},
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(payload), encoding="utf-8")
        return event_path

    def _write_pull_request_labeled_event(
        self,
        tmp_path: Path,
        label_name: str,
    ) -> Path:
        """pull_request labeled event payload を JSON ファイルとして書き出す。"""
        payload = {
            "action": "labeled",
            "pull_request": {"number": 1, "updated_at": "2026-05-08T00:00:00Z"},
            "label": {"name": label_name},
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(payload), encoding="utf-8")
        return event_path

    def test_issue_label_removes_trigger_label(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_issue_labeled_event(tmp_path, "vv-ai:confirm")
        argv = ["--event", "issues", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "## 要望確認\n\n確認しました")
        mock_gh = MagicMock()

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, mock_gh)
            exit_code = main(argv)

        assert exit_code == 0
        mock_gh.create_issue_comment.assert_called_once_with(
            "org/repo",
            1,
            "## 要望確認\n\n確認しました",
        )
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:confirm",
        )

    def test_issue_next_label_removes_trigger_label(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_issue_labeled_event(tmp_path, "vv-ai:next")
        argv = ["--event", "issues", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "## 要望確認\n\n確認しました")
        mock_gh = MagicMock()
        mock_gh.get_issue_parent_number.return_value = None

        with contextlib.ExitStack() as stack:
            _enter_next_patches(stack, tmp_path, session, result, mock_gh)
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_labeled_event(1000, "vv-ai:next"),
            ]
            exit_code = main(argv)

        assert exit_code == 0
        mock_gh.create_issue_comment.assert_called_once_with(
            "org/repo",
            1,
            "## 要望確認\n\n確認しました",
        )
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:next",
        )

    def test_pull_request_label_removes_trigger_label(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_pull_request_labeled_event(tmp_path, "vv-ai:review")
        argv = ["--event", "pull_request", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "## レビュー\n\nレビューしました")
        mock_gh = MagicMock()
        mock_gh.get_pull_request.return_value = _make_github_pr(
            "org/repo",
            1,
            "feature-branch",
        )

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, mock_gh)
            exit_code = main(argv)

        assert exit_code == 0
        mock_gh.create_issue_comment.assert_called_once_with(
            "org/repo",
            1,
            "## レビュー\n\nレビューしました",
        )
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:review",
        )

    def test_pull_request_next_label_removes_trigger_label(
        self, tmp_path: Path
    ) -> None:
        _write_config(tmp_path)
        event_path = self._write_pull_request_labeled_event(tmp_path, "vv-ai:next")
        argv = ["--event", "pull_request", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "## レビュー\n\nレビューしました")
        mock_gh = MagicMock()
        mock_gh.get_pull_request.return_value = _make_github_pr(
            "org/repo",
            1,
            "feature-branch",
        )

        with contextlib.ExitStack() as stack:
            _enter_next_patches(stack, tmp_path, session, result, mock_gh)
            mock_gh.list_issue_timeline_events.return_value = [
                _make_github_labeled_event(1000, "vv-ai:next"),
            ]
            exit_code = main(argv)

        assert exit_code == 0
        mock_gh.create_issue_comment.assert_called_once_with(
            "org/repo",
            1,
            "## レビュー\n\nレビューしました",
        )
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:next",
        )

    def test_provider_failure_still_removes_trigger_label(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_issue_labeled_event(tmp_path, "vv-ai:confirm")
        argv = ["--event", "issues", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "未使用")
        mock_gh = MagicMock()

        with contextlib.ExitStack() as stack:
            execute_provider = _enter_common_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            execute_provider.side_effect = RuntimeError("provider 失敗")
            exit_code = main(argv)

        assert exit_code == 1
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:confirm",
        )

    def test_label_removal_failure_exits_one(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_issue_labeled_event(tmp_path, "vv-ai:confirm")
        argv = ["--event", "issues", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "## 要望確認\n\n確認しました")
        mock_gh = MagicMock()
        mock_gh.remove_issue_label.side_effect = RuntimeError("label 削除失敗")

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, mock_gh)
            exit_code = main(argv)

        assert exit_code == 1
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:confirm",
        )

    def test_label_removal_failure_keeps_created_pr_session_fork(
        self,
        tmp_path: Path,
    ) -> None:
        _write_config(tmp_path)
        event_path = self._write_issue_labeled_event(tmp_path, "vv-ai:implement")
        argv = ["--event", "issues", "--event-file", str(event_path)]
        session = _make_resolved_session(
            "github",
            "org/repo#1",
            "codex",
        ).model_copy(update={"requested_mode": "inherit"})
        result = _make_execution_result(
            "success",
            (
                "TITLE: AI PR\n"
                "COMMIT_MESSAGE: feat: ai commit\n"
                "BODY:\n"
                "AI が考えた本文"
            ),
        )
        mock_gh = MagicMock()
        mock_gh.get_default_branch.return_value = "main"
        mock_gh.create_pull_request.return_value = _make_github_pr(
            "org/repo",
            12,
            "vv-ai/issue-1",
        )
        mock_gh.remove_issue_label.side_effect = RuntimeError("label 削除失敗")

        with contextlib.ExitStack() as stack:
            _enter_common_patches(stack, tmp_path, session, result, mock_gh)
            stack.enter_context(
                patch("vv_ai.commands.runner.create_and_checkout_branch")
            )
            stack.enter_context(
                patch("vv_ai.commands.post_execution.commit_all_changes", return_value=True)
            )
            stack.enter_context(
                patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=True)
            )
            stack.enter_context(patch("vv_ai.commands.post_execution.push_branch"))
            fork_session = stack.enter_context(patch("vv_ai.cli._fork_session_for_pr"))
            exit_code = main(argv)

        assert exit_code == 1
        fork_session.assert_called_once()
        assert fork_session.call_args.args[3] == 12
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:implement",
        )

    def test_provider_failure_is_primary_when_label_removal_also_fails(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_config(tmp_path)
        event_path = self._write_issue_labeled_event(tmp_path, "vv-ai:confirm")
        argv = ["--event", "issues", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("success", "未使用")
        mock_gh = MagicMock()
        mock_gh.remove_issue_label.side_effect = RuntimeError("label 削除失敗")

        with contextlib.ExitStack() as stack:
            execute_provider = _enter_common_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            execute_provider.side_effect = RuntimeError("provider 失敗")
            exit_code = main(argv)

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "実行エラー: RuntimeError: provider 失敗" in captured.err
        assert "ラベル削除に失敗しました: RuntimeError: label 削除失敗" in captured.err
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:confirm",
        )

    def test_provider_failure_result_is_primary_when_label_removal_also_fails(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_config(tmp_path)
        event_path = self._write_issue_labeled_event(tmp_path, "vv-ai:confirm")
        argv = ["--event", "issues", "--event-file", str(event_path)]
        session = _make_resolved_session("github", "org/repo#1", "codex")
        result = _make_execution_result("failure", "provider が失敗しました")
        mock_gh = MagicMock()
        mock_gh.remove_issue_label.side_effect = RuntimeError("label 削除失敗")

        with contextlib.ExitStack() as stack:
            _enter_common_patches(
                stack,
                tmp_path,
                session,
                result,
                mock_gh,
            )
            exit_code = main(argv)

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "実行エラー" not in captured.err
        assert "ラベル削除に失敗しました: RuntimeError: label 削除失敗" in captured.err
        mock_gh.remove_issue_label.assert_called_once_with(
            "org/repo",
            1,
            "vv-ai:confirm",
        )

    def test_dry_run_does_not_remove_trigger_label(self, tmp_path: Path) -> None:
        ready_execution = _make_ready_execution_for_label(dry_run=True)
        result = _make_execution_result("success", "## 要望確認\n\n確認しました")
        mock_gh = MagicMock()
        mock_gh.get_repo_info.return_value = RepoInfo(
            is_fork=False,
            parent_full_name=None,
            parent_default_branch=None,
        )
        mock_gh.get_issue.return_value = _make_github_issue("org/repo", 1)
        mock_gh.list_issue_comments.return_value = []

        with (
            patch("vv_ai.commands.runner.build_github_client", return_value=mock_gh),
            patch("vv_ai.commands.runner.execute_provider", return_value=result),
        ):
            run_command(tmp_path, ready_execution, {}, 0.0)

        mock_gh.remove_issue_label.assert_not_called()
