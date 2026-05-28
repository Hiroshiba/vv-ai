"""provider prompt 生成の単体テスト。"""

from __future__ import annotations

from typing import Literal

from vv_ai.config import VVAIConfig
from vv_ai.workflow.preflight import ReadyExecution
from vv_ai.prompts.build import build_next_decision_prompt, build_provider_prompt
from vv_ai.providers.selection import ProviderSpec, ResolvedProvider
from vv_ai.inputs.resolve import ResolvedCommand, ResolvedTarget

_PROVIDER_ASSET_READONLY_POLICY: str = (
    "調査に必要な参照系 git コマンドは実行して構いません。"
)
_PROVIDER_ASSET_MUTATION_POLICY: str = (
    "作業ツリー、ステージング領域、ブランチ、リモートを変更する git コマンドは、"
    "明示指示がない限り実行しないでください。"
)
_OLD_GIT_INSTRUCTION: str = "git の操作は不要です"


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


def _make_ready_execution(
    kind: Literal["issue", "pr"],
    command: Literal["address", "implement"],
) -> ReadyExecution:
    """テスト用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="issue_comment",
            command=command,
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


def _make_issue_command_ready_execution() -> ReadyExecution:
    """issue コマンド用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="issue_comment",
            command="issue",
            has_target=False,
            dry_run=False,
            repository_full_name="org/repo",
            repo="org/repo",
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        workflow_id="test-run-1",
    )


def _make_requirements_ready_execution() -> ReadyExecution:
    """requirements 用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="issue_comment",
            command="requirements",
            has_target=True,
            dry_run=False,
            repository_full_name="org/repo",
            target=_make_target("issue", 1),
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        workflow_id="test-run-1",
    )


def _make_review_ready_execution() -> ReadyExecution:
    """review 用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="issue_comment",
            command="review",
            has_target=True,
            dry_run=False,
            repository_full_name="org/repo",
            target=_make_target("pr", 1),
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        workflow_id="test-run-1",
    )


def _make_next_ready_execution() -> ReadyExecution:
    """next 用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="issue_comment",
            command="next",
            instruction="次へ進めて",
            has_target=True,
            dry_run=False,
            repository_full_name="org/repo",
            target=_make_target("issue", 1),
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        workflow_id="test-run-1",
    )


def _build_prompt(
    kind: Literal["issue", "pr"],
    command: Literal["address", "implement"],
    unresolved_review_thread_count: int | None,
    auto_continuation_requested: bool = False,
) -> str:
    """対象種別ごとの provider prompt を生成する。"""
    return build_provider_prompt(
        ready_execution=_make_ready_execution(kind, command),
        target_context_block="テストコンテキスト",
        implement_branch_name="feature-branch",
        worktree_ref=None,
        unresolved_review_thread_count=unresolved_review_thread_count,
        auto_continuation_requested=auto_continuation_requested,
    )


def _build_breakdown_prompt() -> str:
    """breakdown の provider prompt を生成する。"""
    return build_provider_prompt(
        ready_execution=_make_breakdown_ready_execution(),
        target_context_block="テストコンテキスト",
        implement_branch_name=None,
        worktree_ref=None,
        unresolved_review_thread_count=None,
        auto_continuation_requested=False,
    )


def _build_issue_command_prompt() -> str:
    """issue コマンドの provider prompt を生成する。"""
    return build_provider_prompt(
        ready_execution=_make_issue_command_ready_execution(),
        target_context_block=None,
        implement_branch_name=None,
        worktree_ref=None,
        unresolved_review_thread_count=None,
        auto_continuation_requested=False,
    )


def _build_requirements_prompt(auto_continuation_requested: bool = False) -> str:
    """requirements の provider prompt を生成する。"""
    return build_provider_prompt(
        ready_execution=_make_requirements_ready_execution(),
        target_context_block="テストコンテキスト",
        implement_branch_name=None,
        worktree_ref=None,
        unresolved_review_thread_count=None,
        auto_continuation_requested=auto_continuation_requested,
    )


def _build_next_decision_prompt() -> str:
    """next AI 判断用 prompt を生成する。"""
    return build_next_decision_prompt(
        ready_execution=_make_next_ready_execution(),
        target_context_block="テストコンテキスト",
    )


def _build_review_prompt(auto_continuation_requested: bool) -> str:
    """review の provider prompt を生成する。"""
    return build_provider_prompt(
        ready_execution=_make_review_ready_execution(),
        target_context_block="テストコンテキスト",
        implement_branch_name=None,
        worktree_ref=None,
        unresolved_review_thread_count=None,
        auto_continuation_requested=auto_continuation_requested,
    )


def _assert_prompt_does_not_mention_git_command_policy(prompt: str) -> None:
    """provider prompt に git コマンド方針が含まれないことを検証する。"""
    assert _PROVIDER_ASSET_READONLY_POLICY not in prompt
    assert _PROVIDER_ASSET_MUTATION_POLICY not in prompt
    assert _OLD_GIT_INSTRUCTION not in prompt


class TestImplementPrompt:
    """implement の provider prompt を検証する。"""

    def test_issue_prompt_does_not_mention_git_command_policy(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        _assert_prompt_does_not_mention_git_command_policy(prompt)

    def test_pr_prompt_does_not_mention_git_command_policy(self) -> None:
        prompt = _build_prompt("pr", "implement", None)

        _assert_prompt_does_not_mention_git_command_policy(prompt)

    def test_pr_prompt_mentions_response_comment(self) -> None:
        prompt = _build_prompt("pr", "implement", None)

        assert (
            "GitHub 実行時は、あなたの最終出力の本文が対象 PR にコメントとして投稿されます。"
            in prompt
        )

    def test_pr_prompt_mentions_fork_patch_comment(self) -> None:
        prompt = _build_prompt("pr", "implement", None)

        assert (
            "fork PR で push できず patch コメントを投稿する場合、"
            "あなたの最終出力の本文は patch コメント内に含まれます。"
            in prompt
        )

    def test_issue_prompt_does_not_mention_pr_response_comment(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        assert "以下の Issue の内容を実装してください。" in prompt
        assert "対象 PR にコメントとして投稿されます" not in prompt
        assert "patch コメント内に含まれます" not in prompt

    def test_issue_prompt_mentions_conventional_commit_title(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        assert (
            "PR タイトルは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
            in prompt
        )

    def test_issue_prompt_mentions_commit_message_format(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        assert "2行目: COMMIT_MESSAGE: <コミットメッセージ>" in prompt

    def test_issue_prompt_mentions_no_change_comment(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        assert (
            "変更コミットがない場合、BODY は対象 Issue へのコメントとして投稿されます。"
            in prompt
        )

    def test_issue_prompt_mentions_conventional_commit_message(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        assert (
            "コミットメッセージは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
            in prompt
        )

    def test_issue_prompt_mentions_source_issue_reference(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        assert "PR 本文には元 Issue への参照を含めてください。" in prompt

    def test_issue_prompt_mentions_closing_keyword(self) -> None:
        prompt = _build_prompt("issue", "implement", None)

        assert (
            "Issue を解決する内容なら GitHub closing keyword を使っても構いません。"
            in prompt
        )

    def test_pr_prompt_does_not_mention_created_pr_title_rule(self) -> None:
        prompt = _build_prompt("pr", "implement", None)

        assert "PR タイトルは Conventional Commits 形式にしてください" not in prompt
        assert "PR 本文には元 Issue への参照を含めてください。" not in prompt

    def test_pr_prompt_mentions_commit_message_format(self) -> None:
        prompt = _build_prompt("pr", "implement", None)

        assert "1行目: COMMIT_MESSAGE: <コミットメッセージ>" in prompt
        assert "2行目: BODY:" in prompt

    def test_pr_prompt_mentions_conventional_commit_message(self) -> None:
        prompt = _build_prompt("pr", "implement", None)

        assert (
            "コミットメッセージは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
            in prompt
        )

    def test_pr_prompt_does_not_mention_review_thread_actions_dir(self) -> None:
        prompt = _build_prompt("pr", "implement", None)

        assert "REVIEW_THREAD_ACTIONS_DIR:" not in prompt
        assert "操作ディレクトリ" not in prompt


class TestIssueCommandPrompt:
    """issue コマンドの provider prompt を検証する。"""

    def test_prompt_uses_issue_create_skill(self) -> None:
        prompt = _build_issue_command_prompt()

        assert "issue-create スキルに従って" in prompt

    def test_prompt_does_not_duplicate_issue_creation_policy(self) -> None:
        prompt = _build_issue_command_prompt()

        assert "本文はユーザーが明示した内容を中心に短く整理してください。" not in prompt
        assert "細かい仕様、受入基準、対象外、実装方式、テスト方針" not in prompt
        assert "未確定の詳細は確定した仕様のように断定せず" not in prompt

    def test_prompt_keeps_title_body_format(self) -> None:
        prompt = _build_issue_command_prompt()

        assert "1行目: TITLE: <タイトル文字列>" in prompt
        assert "2行目: BODY:" in prompt
        assert "3行目以降: Markdown 本文" in prompt

    def test_requirements_prompt_uses_define_requirements(self) -> None:
        prompt = _build_requirements_prompt()

        assert "define-requirements スキルに従って要件定義を行ってください。" in prompt

    def test_normal_prompt_does_not_request_auto_status(self) -> None:
        prompt = _build_requirements_prompt()

        assert "AUTO_STATUS" not in prompt

    def test_auto_prompt_requests_auto_status(self) -> None:
        prompt = _build_requirements_prompt(auto_continuation_requested=True)

        assert "AUTO_STATUS: continue" in prompt
        assert "AUTO_STATUS: escalate" in prompt


class TestAddressPrompt:
    """address の provider prompt を検証する。"""

    def test_prompt_mentions_zero_unresolved_review_thread_count(self) -> None:
        prompt = _build_prompt("pr", "address", 0)

        assert "未解決レビュースレッド件数: 0件" in prompt

    def test_prompt_mentions_unresolved_review_thread_count(self) -> None:
        prompt = _build_prompt("pr", "address", 2)

        assert "未解決レビュースレッド件数: 2件" in prompt

    def test_prompt_requires_unresolved_review_thread_count(self) -> None:
        try:
            _build_prompt("pr", "address", None)
        except RuntimeError as exc:
            assert str(exc) == "address プロンプトには未解決レビュースレッド件数が必要です"
        else:
            raise AssertionError("RuntimeError が発生しませんでした")

    def test_implement_prompt_does_not_mention_unresolved_review_thread_count(
        self,
    ) -> None:
        prompt = _build_prompt("pr", "implement", None)

        assert "未解決レビュースレッド件数" not in prompt

    def test_prompt_mentions_review_thread_actions_format(self) -> None:
        prompt = _build_prompt("pr", "address", 2)

        assert "REVIEW_THREAD_ACTIONS_DIR: /絶対パス" in prompt
        assert "THREAD_ID: <review thread ID>" in prompt
        assert "ACTION: resolve または comment" in prompt

    def test_prompt_mentions_review_thread_actions_steps(self) -> None:
        prompt = _build_prompt("pr", "address", 2)

        assert (
            "1. `mkdir -p hiho_temp && mktemp -u hiho_temp/hiho.XXXXXXXXXX` "
            "で一時パスを取得する"
            in prompt
        )
        assert "2. そのパスをディレクトリとして `mkdir` で作成する" in prompt
        assert "3. ディレクトリ内に `01.md`, `02.md`, ... と連番ファイルを作成する" in prompt
        assert "4. 各ファイルは以下のフォーマットで記述する:" in prompt

    def test_prompt_does_not_mention_git_command_policy(self) -> None:
        prompt = _build_prompt("pr", "address", 0)

        _assert_prompt_does_not_mention_git_command_policy(prompt)

    def test_prompt_mentions_address_review_skill(self) -> None:
        prompt = _build_prompt("pr", "address", 0)

        assert "address-review スキルを使って" in prompt
        assert "レビュー指摘に対応してください。" in prompt

    def test_prompt_mentions_commit_message_format(self) -> None:
        prompt = _build_prompt("pr", "address", 0)

        assert "1行目: COMMIT_MESSAGE: <コミットメッセージ>" in prompt
        assert "2行目: BODY:" in prompt

    def test_prompt_does_not_duplicate_skill_body(self) -> None:
        prompt = _build_prompt("pr", "address", 0)

        assert "レビュー指摘を鵜呑みにせず" not in prompt
        assert "解決済み化" not in prompt
        assert "スキルの結果報告" not in prompt

    def test_prompt_does_not_use_implement_task(self) -> None:
        prompt = _build_prompt("pr", "address", 0)

        assert "追加実装してください。" not in prompt

    def test_auto_prompt_requests_address_next_command(self) -> None:
        prompt = _build_prompt(
            "pr",
            "address",
            0,
            auto_continuation_requested=True,
        )

        assert "COMMAND: review" in prompt
        assert "COMMAND: merge" in prompt


class TestReviewPrompt:
    """review の provider prompt を検証する。"""

    def test_auto_prompt_requests_review_next_command(self) -> None:
        prompt = _build_review_prompt(auto_continuation_requested=True)

        assert "COMMAND: address" in prompt
        assert "COMMAND: merge" in prompt


class TestBreakdownPrompt:
    """breakdown の provider prompt を検証する。"""

    def test_prompt_mentions_parent_issue_reference_in_task_body(self) -> None:
        prompt = _build_breakdown_prompt()

        assert (
            "各タスクの本文には、親 Issue である breakdown 対象 Issue へ"
            "GitHub 上で辿れる参照を含めてください。"
            in prompt
        )

    def test_prompt_keeps_task_file_format(self) -> None:
        prompt = _build_breakdown_prompt()

        assert "TITLE: タイトル" in prompt
        assert "BODY:" in prompt

    def test_prompt_does_not_require_fixed_parent_issue_format(self) -> None:
        prompt = _build_breakdown_prompt()

        assert "親 Issue: #<番号>" not in prompt

    def test_prompt_does_not_mention_natural_expression(self) -> None:
        prompt = _build_breakdown_prompt()

        assert "表現はタスク本文に自然に合う形で構いません。" not in prompt


class TestNextDecisionPrompt:
    """next AI 判断 prompt を検証する。"""

    def test_prompt_mentions_command_choices(self) -> None:
        prompt = _build_next_decision_prompt()

        assert "COMMAND: breakdown" in prompt
        assert "COMMAND: implement" in prompt

    def test_prompt_forbids_code_changes(self) -> None:
        prompt = _build_next_decision_prompt()

        assert "コード変更は行わないでください。" in prompt

    def test_prompt_includes_context_and_instruction(self) -> None:
        prompt = _build_next_decision_prompt()

        assert "テストコンテキスト" in prompt
        assert "次へ進めて" in prompt
