"""dry-run と finally-save 保証の単体テスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vv_ai.cli import _run_ready_execution
from vv_ai.commands.post_execution import (
    _handle_implement_issue_post_execution,
    _handle_pr_change_post_execution,
    _handle_issue_post_execution,
    _post_response_comment,
)
from vv_ai.config import VVAIConfig
from vv_ai.artifacts.execution import SavedExecutionArtifacts
from vv_ai.executions.result import ExecutionResult, ExecutionStatus
from vv_ai.backends.github.models import (
    GitHubActor,
    GitHubClientError,
    GitHubIssue,
    GitHubPullRequest,
)
from vv_ai.artifacts.metrics import (
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
)
from vv_ai.workflow.preflight import ReadyExecution
from vv_ai.providers.selection import ProviderSpec, ResolvedProvider
from vv_ai.artifacts.report import ReportSections
from vv_ai.inputs.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.sessions.models import ResolvedSession, SessionKey, SessionStateRef


def _make_command(**overrides: object) -> ResolvedCommand:
    """テスト用の最小 ResolvedCommand を生成する。"""
    defaults: dict[str, object] = {
        "event_name": "local",
        "command": "arch",
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
            auth_home_env="VV_CODEX_HOME",
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
        requested_mode="new",
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


def _make_github_issue() -> GitHubIssue:
    """テスト用の GitHubIssue を生成する。"""
    return GitHubIssue(
        id=1,
        repository_full_name="org/repo",
        number=1,
        title="テスト Issue",
        body="テスト本文",
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url="https://github.com/org/repo/issues/1",
    )


def _make_github_pr(
    number: int,
    is_cross_repository: bool,
) -> GitHubPullRequest:
    """テスト用の GitHubPullRequest を生成する。"""
    return GitHubPullRequest(
        repository_full_name="org/repo",
        number=number,
        title="テスト PR",
        body="テスト本文",
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url=f"https://github.com/org/repo/pull/{number}",
        head_ref_name="feature-branch",
        base_ref_name="main",
        head_repository_full_name="org/repo",
        is_cross_repository=is_cross_repository,
        maintainer_can_modify=True,
    )


class TestDryRunSuppression:
    def test_dryrun_suppresses_implement_issue_push(self) -> None:
        ready = _make_ready_execution(command=_make_command(command="implement"))
        result = _make_execution_result("success")
        github_client = MagicMock()

        with patch("vv_ai.commands.post_execution.push_branch") as mock_push:
            _handle_implement_issue_post_execution(
                Path("/dummy"), ready, result, github_client, "vv-ai/issue-1-abc123", None, {}
            )
            mock_push.assert_not_called()

        github_client.create_pull_request.assert_not_called()
        github_client.create_issue_comment.assert_not_called()

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

        with patch("vv_ai.commands.post_execution.push_branch") as mock_push:
            _handle_pr_change_post_execution(
                Path("/dummy"), ready, result, github_client, "feature-branch", None, None, {}
            )
            mock_push.assert_not_called()

        github_client.create_issue_comment.assert_not_called()

    def test_dryrun_suppresses_address_pr_push(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="address", target=ResolvedTarget(
                backend="github",
                kind="pr",
                canonical_id="org/repo#2",
                repository_full_name="org/repo",
                number=2,
            ))
        )
        result = _make_execution_result("success")
        github_client = MagicMock()

        with patch("vv_ai.commands.post_execution.push_branch") as mock_push:
            _handle_pr_change_post_execution(
                Path("/dummy"), ready, result, github_client, "feature-branch", None, None, {}
            )
            mock_push.assert_not_called()

        github_client.create_issue_comment.assert_not_called()

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
        ready = _make_ready_execution(command=_make_command(command="arch"))
        result = _make_execution_result("success", response_text="計画です")
        github_client = MagicMock()

        _post_response_comment(ready, result, github_client)

        github_client.create_issue_comment.assert_not_called()

    @pytest.mark.parametrize(
        ("command", "heading"),
        [
            ("confirm", "## 要望確認"),
            ("requirements", "## 要件定義"),
            ("arch", "## 基本設計"),
            ("detail", "## 詳細設計"),
            ("review", "## レビュー"),
        ],
    )
    def test_non_dryrun_posts_response_comment_with_heading(
        self,
        command: str,
        heading: str,
    ) -> None:
        ready = _make_ready_execution(command=_make_command(command=command, dry_run=False))
        result = _make_execution_result("success", response_text="計画です")
        github_client = MagicMock()

        _post_response_comment(ready, result, github_client)

        github_client.create_issue_comment.assert_called_once_with(
            "org/repo",
            1,
            f"{heading}\n\n計画です",
        )

    def test_non_dryrun_posts_reply_response_comment_without_heading(self) -> None:
        ready = _make_ready_execution(command=_make_command(command="reply", dry_run=False))
        result = _make_execution_result("success", response_text="返答です")
        github_client = MagicMock()

        _post_response_comment(ready, result, github_client)

        github_client.create_issue_comment.assert_called_once_with(
            "org/repo",
            1,
            "返答です",
        )


class TestImplementResponseComment:
    def test_implement_issue_does_not_post_response_to_created_pr(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result(
            "success",
            response_text=(
                "TITLE: AI PR\n"
                "COMMIT_MESSAGE: feat: ai commit\n"
                "BODY:\n"
                "AI が考えた本文"
            ),
        )
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"
        github_client.create_pull_request.return_value = _make_github_pr(
            number=12,
            is_cross_repository=False,
        )

        with (
            patch(
                "vv_ai.commands.post_execution.commit_all_changes", return_value=True
            ) as mock_commit,
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=True),
            patch("vv_ai.commands.post_execution.push_branch"),
        ):
            _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        mock_commit.assert_called_once_with(Path("/dummy"), "feat: ai commit")
        github_client.create_pull_request.assert_called_once_with(
            "org/repo",
            "AI PR",
            "AI が考えた本文",
            "vv-ai/issue-1-abc123",
            "main",
            maintainer_can_modify=True,
        )
        github_client.get_issue.assert_not_called()
        github_client.create_issue_comment.assert_not_called()

    def test_implement_issue_posts_body_when_no_commits_ahead(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result(
            "success",
            response_text=(
                "TITLE: AI PR\n"
                "COMMIT_MESSAGE: feat: ai commit\n"
                "BODY:\n"
                "変更不要と判断しました"
            ),
        )
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"

        with (
            patch(
                "vv_ai.commands.post_execution.commit_all_changes", return_value=False
            ) as mock_commit,
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=False),
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
        ):
            _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        mock_commit.assert_called_once_with(Path("/dummy"), "feat: ai commit")
        github_client.create_issue_comment.assert_called_once_with(
            "org/repo",
            1,
            "変更不要と判断しました",
        )
        mock_push.assert_not_called()
        github_client.create_pull_request.assert_not_called()

    def test_implement_issue_continues_when_no_change_comment_fails(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result(
            "success",
            response_text=(
                "TITLE: AI PR\n"
                "COMMIT_MESSAGE: feat: ai commit\n"
                "BODY:\n"
                "変更不要と判断しました"
            ),
        )
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"
        github_client.create_issue_comment.side_effect = GitHubClientError("失敗")

        with (
            patch(
                "vv_ai.commands.post_execution.commit_all_changes", return_value=False
            ),
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=False),
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
        ):
            result_pr = _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        assert result_pr is None
        assert "implement 変更なしコメント投稿に失敗しました" in capsys.readouterr().err
        mock_push.assert_not_called()
        github_client.create_pull_request.assert_not_called()

    def test_implement_issue_requires_response_text(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result("success", response_text=None)
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes", return_value=True),
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=True),
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
            pytest.raises(
                RuntimeError,
                match="AI からの PR タイトル、コミットメッセージ、本文がありません",
            ),
        ):
            _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        mock_push.assert_not_called()
        github_client.create_pull_request.assert_not_called()

    def test_implement_issue_requires_title_line(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result("success", response_text="BODY:\n本文")
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes", return_value=True),
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=True),
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
            pytest.raises(RuntimeError, match="1行目は `TITLE: <タイトル>`"),
        ):
            _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        mock_push.assert_not_called()
        github_client.create_pull_request.assert_not_called()

    def test_implement_issue_requires_body_line(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result(
            "success",
            response_text="TITLE: AI PR\nCOMMIT_MESSAGE: feat: ai commit\n本文",
        )
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes", return_value=True),
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=True),
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
            pytest.raises(RuntimeError, match="3行目は `BODY:`"),
        ):
            _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        mock_push.assert_not_called()
        github_client.create_pull_request.assert_not_called()

    def test_implement_issue_requires_commit_message_line(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result(
            "success",
            response_text="TITLE: AI PR\nBODY:\n本文",
        )
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes") as mock_commit,
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=True),
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
            pytest.raises(
                RuntimeError,
                match="2行目は `COMMIT_MESSAGE: <コミットメッセージ>`",
            ),
        ):
            _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        mock_commit.assert_not_called()
        mock_push.assert_not_called()
        github_client.create_pull_request.assert_not_called()

    def test_implement_issue_requires_non_empty_commit_message(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(command="implement", dry_run=False)
        )
        result = _make_execution_result(
            "success",
            response_text="TITLE: AI PR\nCOMMIT_MESSAGE:   \nBODY:\n本文",
        )
        github_client = MagicMock()
        github_client.get_default_branch.return_value = "main"

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes") as mock_commit,
            patch("vv_ai.commands.post_execution.has_commits_ahead", return_value=True),
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
            pytest.raises(RuntimeError, match="COMMIT_MESSAGE が空です"),
        ):
            _handle_implement_issue_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "vv-ai/issue-1-abc123",
                None,
                {},
            )

        mock_commit.assert_not_called()
        mock_push.assert_not_called()
        github_client.create_pull_request.assert_not_called()

    def test_implement_pr_posts_response_to_target_pr(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(
                command="implement",
                dry_run=False,
                target=ResolvedTarget(
                    backend="github",
                    kind="pr",
                    canonical_id="org/repo#2",
                    repository_full_name="org/repo",
                    number=2,
                ),
            )
        )
        result = _make_execution_result(
            "success",
            response_text="COMMIT_MESSAGE: fix: ai commit\nBODY:\n追コミット完了",
        )
        github_client = MagicMock()

        with (
            patch(
                "vv_ai.commands.post_execution.commit_all_changes", return_value=True
            ) as mock_commit,
            patch("vv_ai.commands.post_execution.push_branch"),
        ):
            _handle_pr_change_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "feature-branch",
                _make_github_pr(number=2, is_cross_repository=False),
                None,
                {},
            )

        mock_commit.assert_called_once_with(Path("/dummy"), "fix: ai commit")
        github_client.create_issue_comment.assert_called_once_with(
            "org/repo", 2, "追コミット完了"
        )

    def test_address_pr_posts_response_to_target_pr(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(
                command="address",
                dry_run=False,
                target=ResolvedTarget(
                    backend="github",
                    kind="pr",
                    canonical_id="org/repo#2",
                    repository_full_name="org/repo",
                    number=2,
                ),
            )
        )
        result = _make_execution_result(
            "success",
            response_text="COMMIT_MESSAGE: fix: address review\nBODY:\nレビュー指摘対応完了",
        )
        github_client = MagicMock()

        with (
            patch(
                "vv_ai.commands.post_execution.commit_all_changes", return_value=True
            ) as mock_commit,
            patch("vv_ai.commands.post_execution.push_branch"),
        ):
            _handle_pr_change_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "feature-branch",
                _make_github_pr(number=2, is_cross_repository=False),
                None,
                {},
            )

        mock_commit.assert_called_once_with(Path("/dummy"), "fix: address review")
        github_client.create_issue_comment.assert_called_once_with(
            "org/repo", 2, "レビュー指摘対応完了"
        )

    def test_fork_patch_comment_includes_response_text(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(
                command="implement",
                dry_run=False,
                target=ResolvedTarget(
                    backend="github",
                    kind="pr",
                    canonical_id="org/repo#3",
                    repository_full_name="org/repo",
                    number=3,
                ),
            )
        )
        result = _make_execution_result(
            "success",
            response_text="COMMIT_MESSAGE: fix: fork\nBODY:\n追加実装完了",
        )
        github_client = MagicMock()

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes", return_value=True),
            patch("vv_ai.commands.post_execution.try_push_current_branch", return_value=False),
            patch("vv_ai.commands.post_execution.generate_patch", return_value="diff --git a/a b/a"),
        ):
            _handle_pr_change_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "feature-branch",
                _make_github_pr(number=3, is_cross_repository=True),
                "base-sha",
                {},
            )

        github_client.create_issue_comment.assert_called_once()
        _, _, body = github_client.create_issue_comment.call_args.args
        assert "追加実装完了" in body
        assert "COMMIT_MESSAGE:" not in body
        assert "```diff\ndiff --git a/a b/a\n```" in body

    def test_implement_pr_requires_commit_message_line(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(
                command="implement",
                dry_run=False,
                target=ResolvedTarget(
                    backend="github",
                    kind="pr",
                    canonical_id="org/repo#2",
                    repository_full_name="org/repo",
                    number=2,
                ),
            )
        )
        result = _make_execution_result("success", response_text="BODY:\n追コミット完了")
        github_client = MagicMock()

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes") as mock_commit,
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
            pytest.raises(
                RuntimeError,
                match="1行目は `COMMIT_MESSAGE: <コミットメッセージ>`",
            ),
        ):
            _handle_pr_change_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "feature-branch",
                _make_github_pr(number=2, is_cross_repository=False),
                None,
                {},
            )

        mock_commit.assert_not_called()
        mock_push.assert_not_called()
        github_client.create_issue_comment.assert_not_called()

    def test_implement_pr_requires_non_empty_commit_message(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(
                command="implement",
                dry_run=False,
                target=ResolvedTarget(
                    backend="github",
                    kind="pr",
                    canonical_id="org/repo#2",
                    repository_full_name="org/repo",
                    number=2,
                ),
            )
        )
        result = _make_execution_result(
            "success",
            response_text="COMMIT_MESSAGE:   \nBODY:\n追コミット完了",
        )
        github_client = MagicMock()

        with (
            patch("vv_ai.commands.post_execution.commit_all_changes") as mock_commit,
            patch("vv_ai.commands.post_execution.push_branch") as mock_push,
            pytest.raises(RuntimeError, match="COMMIT_MESSAGE が空です"),
        ):
            _handle_pr_change_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "feature-branch",
                _make_github_pr(number=2, is_cross_repository=False),
                None,
                {},
            )

        mock_commit.assert_not_called()
        mock_push.assert_not_called()
        github_client.create_issue_comment.assert_not_called()

    def test_implement_pr_accepts_non_conventional_commit_message(self) -> None:
        ready = _make_ready_execution(
            command=_make_command(
                command="implement",
                dry_run=False,
                target=ResolvedTarget(
                    backend="github",
                    kind="pr",
                    canonical_id="org/repo#2",
                    repository_full_name="org/repo",
                    number=2,
                ),
            )
        )
        result = _make_execution_result(
            "success",
            response_text="COMMIT_MESSAGE: example\nBODY:\n追コミット完了",
        )
        github_client = MagicMock()

        with (
            patch(
                "vv_ai.commands.post_execution.commit_all_changes", return_value=True
            ) as mock_commit,
            patch("vv_ai.commands.post_execution.push_branch"),
        ):
            _handle_pr_change_post_execution(
                Path("/dummy"),
                ready,
                result,
                github_client,
                "feature-branch",
                _make_github_pr(number=2, is_cross_repository=False),
                None,
                {},
            )

        mock_commit.assert_called_once_with(Path("/dummy"), "example")


class TestFinallySaveGuarantee:
    @patch("vv_ai.cli.save_execution_artifacts")
    @patch("vv_ai.cli.run_command")
    def test_save_called_on_success(
        self,
        mock_run_command: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        mock_run_command.return_value = (_make_execution_result("success"), None)
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
