"""主要シナリオの受け入れ確認テスト。"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from vv_ai.cli import main
from vv_ai.execution import ExecutionResult, ExecutionStatus, SavedExecutionArtifacts
from vv_ai.github import (
    GitHubActor,
    GitHubComment,
    GitHubIssue,
    GitHubPullRequest,
    RepoInfo,
)
from vv_ai.metrics_artifact import MetricsBehavior, MetricsUsage, ProviderSpecificMetrics
from vv_ai.report_artifact import ReportSections
from vv_ai.resolve import BackendName
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
        patch("vv_ai.command_handler.execute_provider", return_value=result)
    )
    stack.enter_context(
        patch(
            "vv_ai.cli.save_execution_artifacts",
            return_value=MagicMock(spec=SavedExecutionArtifacts),
        )
    )
    stack.enter_context(
        patch("vv_ai.command_handler.build_github_client", return_value=github_client)
    )
    stack.enter_context(
        patch("vv_ai.next_command.build_github_client", return_value=github_client)
    )
    stack.enter_context(patch.dict("os.environ", {"VV_OPENAI_API_KEY": "dummy-key"}))
    if isinstance(github_client, MagicMock):
        github_client.get_repo_info.return_value = RepoInfo(
            is_fork=False, parent_full_name=None, parent_default_branch=None
        )
        github_client.get_issue.return_value = _make_github_issue("org/repo", 1)
        github_client.get_issue_parent_number.return_value = None
        github_client.list_issue_comments.return_value = []
    return execute_provider


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
                patch("vv_ai.command_handler.create_and_checkout_branch")
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
                patch("vv_ai.command_handler.fetch_and_checkout_branch")
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

    def _write_pr_event_file(self, tmp_path: Path, comment_body: str, sender: str) -> Path:
        """PR comment event payload を JSON ファイルとして書き出す。"""
        payload = {
            "action": "created",
            "comment": {
                "id": 1001,
                "body": comment_body,
                "user": {"login": sender},
            },
            "issue": {
                "number": 42,
                "pull_request": {"url": "https://api.github.com/repos/org/repo/pulls/42"},
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

    def test_next_issue_without_history_runs_confirm(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_event_file(
            tmp_path, "@vv-ai next", "Hiroshiba"
        )
        argv = [
            "--event", "issue_comment",
            "--event-file", str(event_path),
        ]
        session = _make_resolved_session("github", "org/repo#42", "codex")
        result = _make_execution_result("success", "確認事項です")
        mock_gh = MagicMock()

        with contextlib.ExitStack() as stack:
            execute_provider = _enter_common_patches(
                stack, tmp_path, session, result, mock_gh
            )
            exit_code = main(argv)

        ready_execution = execute_provider.call_args.args[1]
        assert exit_code == 0
        assert ready_execution.command.command == "confirm"

    def test_next_pr_after_review_runs_implement(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_pr_event_file(
            tmp_path, "@vv-ai next --dry-run", "Hiroshiba"
        )
        argv = [
            "--event", "issue_comment",
            "--event-file", str(event_path),
        ]
        session = _make_resolved_session("github", "org/repo#42", "codex")
        result = _make_execution_result("success", "追加実装しました")
        mock_gh = MagicMock()

        with contextlib.ExitStack() as stack:
            execute_provider = _enter_common_patches(
                stack, tmp_path, session, result, mock_gh
            )
            mock_gh.list_issue_comments.return_value = [
                _make_github_comment(1000, "@vv-ai review"),
                _make_github_comment(1001, "@vv-ai next --dry-run"),
            ]
            mock_gh.get_pull_request.return_value = _make_github_pr(
                "org/repo", 42, "feature-branch"
            )
            stack.enter_context(
                patch("vv_ai.command_handler.fetch_and_checkout_branch")
            )
            exit_code = main(argv)

        ready_execution = execute_provider.call_args.args[1]
        assert exit_code == 0
        assert ready_execution.command.command == "implement"

    def test_next_issue_after_breakdown_exits_2(self, tmp_path: Path) -> None:
        _write_config(tmp_path)
        event_path = self._write_event_file(
            tmp_path, "@vv-ai next", "Hiroshiba"
        )
        argv = [
            "--event", "issue_comment",
            "--event-file", str(event_path),
        ]
        session = _make_resolved_session("github", "org/repo#42", "codex")
        result = _make_execution_result("success", "実行されません")
        mock_gh = MagicMock()

        with contextlib.ExitStack() as stack:
            execute_provider = _enter_common_patches(
                stack, tmp_path, session, result, mock_gh
            )
            mock_gh.list_issue_comments.return_value = [
                _make_github_comment(1000, "@vv-ai breakdown"),
                _make_github_comment(1001, "@vv-ai next"),
            ]
            exit_code = main(argv)

        assert exit_code == 2
        execute_provider.assert_not_called()

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
