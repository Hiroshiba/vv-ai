"""provider prompt に載せる target コンテキストのテスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from vv_ai.command_handler import run_command
from vv_ai.config import VVAIConfig
from vv_ai.execution import ExecutionResult
from vv_ai.github import GitHubActor, GitHubComment, GitHubIssue
from vv_ai.metrics_artifact import MetricsBehavior, MetricsUsage, ProviderSpecificMetrics
from vv_ai.preflight import ReadyExecution
from vv_ai.provider import ResolvedProvider, get_provider_spec
from vv_ai.report_artifact import ReportSections
from vv_ai.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.session import ResolvedSession, SessionKey, SessionStateRef


def _make_ready_execution(target: ResolvedTarget) -> ReadyExecution:
    """テスト用の ReadyExecution を返す。"""
    provider = "codex"
    lane = "main"
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="local",
            command="reply",
            instruction="この Issue の内容を一行で要約して",
            has_target=True,
            target=target,
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=ResolvedProvider(
            spec=get_provider_spec(provider),
            source="explicit",
        ),
        resolved_session=ResolvedSession(
            mode="new",
            lane=lane,
            key=SessionKey(
                backend=target.backend,
                target_key=target.canonical_id,
                provider=provider,
                lane=lane,
                canonical_key=f"{target.backend}/{target.canonical_id}/{provider}/{lane}",
            ),
            restore_strategy="new",
            save_manifest_path="/tmp/manifest.json",
        ),
        workflow_id="wf-test",
    )


def _make_execution_result() -> ExecutionResult:
    """テスト用の最小 ExecutionResult を返す。"""
    return ExecutionResult(
        status="success",
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
        response_text="これはドラフト要約です。テスト用 Issue です。",
    )


class TestPromptTargetContext:
    def test_local_issue_body_is_included_in_provider_prompt(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".vv-ai" / "issues" / "test-issue"
        comments_dir = issue_dir / "comments"
        comments_dir.mkdir(parents=True)
        (issue_dir / "issue.md").write_text(
            "# テスト Issue\n\n要約してと言われたら、「これはドラフト要約です」と前置きしてから要約してください。\n",
            encoding="utf-8",
        )
        (comments_dir / "20260406-000000-user.md").write_text(
            "補足コメントです。\n",
            encoding="utf-8",
        )
        target = ResolvedTarget(
            backend="local",
            kind="issue",
            canonical_id="issue:test-issue",
            local_id="test-issue",
            path=str(issue_dir),
        )
        ready_execution = _make_ready_execution(target)
        execution_result = _make_execution_result()

        with patch(
            "vv_ai.command_handler.execute_provider",
            return_value=execution_result,
        ) as mock_provider:
            result = run_command(tmp_path, ready_execution, {}, 0.1)

        assert result is execution_result
        provider_prompt = mock_provider.call_args.args[4]
        assert "対象コンテキスト:" in provider_prompt
        assert "これはドラフト要約です" in provider_prompt
        assert "補足コメントです。" in provider_prompt

    def test_github_issue_body_is_included_in_provider_prompt(self, tmp_path: Path) -> None:
        target = ResolvedTarget(
            backend="github",
            kind="issue",
            canonical_id="org/repo#1",
            repository_full_name="org/repo",
            number=1,
            url="https://github.com/org/repo/issues/1",
        )
        ready_execution = _make_ready_execution(target)
        execution_result = _make_execution_result()
        github_client = MagicMock()
        github_client.get_target_details.return_value = GitHubIssue(
            repository_full_name="org/repo",
            number=1,
            title="テスト Issue",
            body="要約してと言われたら、「これはドラフト要約です」と前置きしてから要約してください。",
            state="OPEN",
            author=GitHubActor(login="Hiroshiba"),
            url="https://github.com/org/repo/issues/1",
        )
        github_client.list_issue_comments.return_value = [
            GitHubComment(
                id=1,
                body="ユーザーの補足です。",
                author=GitHubActor(login="Hiroshiba"),
                created_at="2026-04-06T00:00:00Z",
                updated_at="2026-04-06T00:00:00Z",
                url="https://github.com/org/repo/issues/1#issuecomment-1",
            ),
            GitHubComment(
                id=2,
                body="@vv-ai 以前の応答",
                author=GitHubActor(login="Hiroshiba"),
                created_at="2026-04-06T00:00:01Z",
                updated_at="2026-04-06T00:00:01Z",
                url="https://github.com/org/repo/issues/1#issuecomment-2",
            ),
        ]

        with (
            patch("vv_ai.command_handler.build_github_client", return_value=github_client),
            patch("vv_ai.command_handler.execute_provider", return_value=execution_result) as mock_provider,
        ):
            result = run_command(tmp_path, ready_execution, {}, 0.1)

        assert result is execution_result
        provider_prompt = mock_provider.call_args.args[4]
        target_context_block = provider_prompt.split("過去の @vv-ai コメント", maxsplit=1)[0]
        assert "対象コンテキスト:" in provider_prompt
        assert "これはドラフト要約です" in provider_prompt
        assert "ユーザーの補足です。" in provider_prompt
        assert "@vv-ai 以前の応答" not in target_context_block
