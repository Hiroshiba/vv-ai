"""provider prompt 生成の単体テスト。"""

from __future__ import annotations

from typing import Literal

from vv_ai.config import VVAIConfig
from vv_ai.preflight import ReadyExecution
from vv_ai.prompt import build_provider_prompt
from vv_ai.provider import ProviderSpec, ResolvedProvider
from vv_ai.resolve import ResolvedCommand, ResolvedTarget


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


def _make_target(kind: Literal["issue", "pr"], number: int) -> ResolvedTarget:
    """テスト用の ResolvedTarget を生成する。"""
    return ResolvedTarget(
        backend="github",
        kind=kind,
        canonical_id=f"org/repo#{number}",
        repository_full_name="org/repo",
        number=number,
    )


def _make_ready_execution(kind: Literal["issue", "pr"]) -> ReadyExecution:
    """テスト用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="issue_comment",
            command="implement",
            has_target=True,
            dry_run=False,
            repository_full_name="org/repo",
            target=_make_target(kind, 1),
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        workflow_id="test-run-1",
    )


def _make_breakdown_ready_execution() -> ReadyExecution:
    """breakdown 用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="issue_comment",
            command="breakdown",
            has_target=True,
            dry_run=False,
            repository_full_name="org/repo",
            target=_make_target("issue", 1),
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        workflow_id="test-run-1",
    )


def _build_prompt(kind: Literal["issue", "pr"]) -> str:
    """対象種別ごとの provider prompt を生成する。"""
    return build_provider_prompt(
        ready_execution=_make_ready_execution(kind),
        target_context_block="テストコンテキスト",
        implement_branch_name="feature-branch",
        worktree_ref=None,
    )


def _build_breakdown_prompt() -> str:
    """breakdown の provider prompt を生成する。"""
    return build_provider_prompt(
        ready_execution=_make_breakdown_ready_execution(),
        target_context_block="テストコンテキスト",
        implement_branch_name=None,
        worktree_ref=None,
    )


class TestImplementPrompt:
    """implement の provider prompt を検証する。"""

    def test_pr_prompt_mentions_response_comment(self) -> None:
        prompt = _build_prompt("pr")

        assert (
            "GitHub 実行時は、あなたの最終出力の本文が対象 PR にコメントとして投稿されます。"
            in prompt
        )

    def test_pr_prompt_mentions_fork_patch_comment(self) -> None:
        prompt = _build_prompt("pr")

        assert (
            "fork PR で push できず patch コメントを投稿する場合、"
            "あなたの最終出力の本文は patch コメント内に含まれます。"
            in prompt
        )

    def test_issue_prompt_does_not_mention_pr_response_comment(self) -> None:
        prompt = _build_prompt("issue")

        assert "以下の Issue の内容を実装してください。" in prompt
        assert "対象 PR にコメントとして投稿されます" not in prompt
        assert "patch コメント内に含まれます" not in prompt

    def test_issue_prompt_mentions_conventional_commit_title(self) -> None:
        prompt = _build_prompt("issue")

        assert (
            "PR タイトルは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
            in prompt
        )

    def test_issue_prompt_mentions_commit_message_format(self) -> None:
        prompt = _build_prompt("issue")

        assert "2行目: COMMIT_MESSAGE: <コミットメッセージ>" in prompt

    def test_issue_prompt_mentions_conventional_commit_message(self) -> None:
        prompt = _build_prompt("issue")

        assert (
            "コミットメッセージは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
            in prompt
        )

    def test_issue_prompt_mentions_source_issue_reference(self) -> None:
        prompt = _build_prompt("issue")

        assert "PR 本文には元 Issue への参照を含めてください。" in prompt

    def test_issue_prompt_mentions_closing_keyword(self) -> None:
        prompt = _build_prompt("issue")

        assert (
            "Issue を解決する内容なら GitHub closing keyword を使っても構いません。"
            in prompt
        )

    def test_pr_prompt_does_not_mention_created_pr_title_rule(self) -> None:
        prompt = _build_prompt("pr")

        assert "PR タイトルは Conventional Commits 形式にしてください" not in prompt
        assert "PR 本文には元 Issue への参照を含めてください。" not in prompt

    def test_pr_prompt_mentions_commit_message_format(self) -> None:
        prompt = _build_prompt("pr")

        assert "1行目: COMMIT_MESSAGE: <コミットメッセージ>" in prompt
        assert "2行目: BODY:" in prompt

    def test_pr_prompt_mentions_conventional_commit_message(self) -> None:
        prompt = _build_prompt("pr")

        assert (
            "コミットメッセージは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
            in prompt
        )


class TestBreakdownPrompt:
    """breakdown の provider prompt を検証する。"""

    def test_prompt_mentions_parent_issue_reference_in_task_body(self) -> None:
        prompt = _build_breakdown_prompt()

        assert (
            "各タスクの本文には、breakdown 対象 Issue が親 Issue であることを"
            "読者が辿れる言及を含めてください。"
            in prompt
        )

    def test_prompt_keeps_task_file_format(self) -> None:
        prompt = _build_breakdown_prompt()

        assert "TITLE: タイトル" in prompt
        assert "BODY:" in prompt

    def test_prompt_does_not_require_fixed_parent_issue_format(self) -> None:
        prompt = _build_breakdown_prompt()

        assert "親 Issue: #<番号>" not in prompt
