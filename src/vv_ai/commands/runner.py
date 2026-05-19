"""コマンド実行の前処理と provider 呼び出し。"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from vv_ai.backends.github.client import GitHubClient, build_github_client
from vv_ai.backends.github.models import GitHubPullRequest
from vv_ai.commands.post_execution import handle_post_execution
from vv_ai.commands.reactions import add_reaction_safely, finalize_reactions
from vv_ai.executions.result import ExecutionResult, ExecutionStatus
from vv_ai.git_ops import (
    checkout_fork_pr,
    checkout_ref,
    create_and_checkout_branch,
    fetch_and_checkout_branch,
    fetch_remote,
    generate_implement_branch_name,
    get_head_sha,
    setup_upstream_remote,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.prompt import build_provider_prompt
from vv_ai.providers.runner import execute_provider
from vv_ai.resolve import ResolvedTarget
from vv_ai.session import SessionStateRef, TargetContextState
from vv_ai.sync_command import run_sync_command
from vv_ai.target_context import (
    build_target_context,
    empty_target_context_state,
    merge_target_context_state,
)

_ISSUE_CONTEXT_COMMANDS = frozenset(
    {"confirm", "reply", "requirements", "arch", "detail", "breakdown"}
)
_PR_CHANGE_COMMANDS = frozenset({"implement", "address"})


class CommandCleanupError(RuntimeError):
    """コマンド終了処理に失敗したことを表す例外。"""

    def __init__(
        self,
        message: str,
        created_pr: GitHubPullRequest | None,
    ) -> None:
        super().__init__(message)
        self.created_pr = created_pr


def run_command(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> tuple[ExecutionResult, GitHubPullRequest | None]:
    """コマンド固有の前処理・provider 実行・後処理を行って実行結果と作成された PR を返す。"""
    command = ready_execution.command
    target = command.target

    if command.command == "review" and (target is None or target.kind != "pr"):
        raise RuntimeError("`review` コマンドは PR を対象に指定してください")

    if command.command == "address" and (target is None or target.kind != "pr"):
        raise RuntimeError("`address` コマンドは PR を対象に指定してください")

    if command.command == "breakdown" and (
        target is None or target.kind != "issue" or target.backend != "github"
    ):
        raise RuntimeError("`breakdown` コマンドは GitHub Issue を対象に指定してください")

    github_client = (
        build_github_client()
        if _is_github_target(target) or command.command in {"issue", "breakdown"}
        else None
    )

    eyes_reaction_id: int | None = None
    if (
        github_client is not None
        and not command.dry_run
        and command.comment_id is not None
    ):
        assert target is not None
        assert target.repository_full_name is not None
        eyes_reaction_id = add_reaction_safely(
            github_client,
            target.repository_full_name,
            command.comment_id,
            "eyes",
        )

    implement_branch_name: str | None = None
    pr_info: GitHubPullRequest | None = None
    head_sha_before: str | None = None
    fork_base_ref: str | None = None
    worktree_ref: str | None = None
    execution_result: ExecutionResult | None = None
    created_pr: GitHubPullRequest | None = None
    finalize_status: ExecutionStatus = "failure"
    primary_error: BaseException | None = None
    try:
        try:
            if command.command == "sync":
                if github_client is None:
                    raise RuntimeError("`sync` コマンドには GitHub target が必要です")
                execution_result = run_sync_command(
                    repo_root,
                    ready_execution,
                    github_client,
                    env,
                    preflight_duration_seconds,
                )
                finalize_status = execution_result.status
                return execution_result, None

            if (
                command.command == "implement"
                and target is not None
                and target.kind == "issue"
            ):
                issue_identifier = (
                    str(target.number) if target.number is not None else target.local_id
                )
                assert issue_identifier is not None
                implement_branch_name = generate_implement_branch_name(issue_identifier)
                start_point: str | None = None
                if _is_github_target(target):
                    assert github_client is not None
                    fork_base_ref = _resolve_fork_base_ref(
                        repo_root,
                        target,
                        github_client,
                    )
                    start_point = fork_base_ref
                create_and_checkout_branch(
                    repo_root,
                    implement_branch_name,
                    start_point,
                )
            elif (
                command.command in _ISSUE_CONTEXT_COMMANDS
                and target is not None
                and target.kind == "issue"
                and _is_github_target(target)
            ):
                assert github_client is not None
                worktree_ref = _resolve_fork_base_ref(repo_root, target, github_client)
                if worktree_ref is not None:
                    checkout_ref(repo_root, worktree_ref)
            elif (
                command.command in _PR_CHANGE_COMMANDS
                and target is not None
                and target.kind == "pr"
            ):
                assert target.repository_full_name is not None
                assert target.number is not None
                if not _is_github_target(target):
                    raise RuntimeError(
                        f"ローカル PR への {command.command} は未対応です"
                    )
                assert github_client is not None
                pr_info = github_client.get_pull_request(
                    target.repository_full_name,
                    target.number,
                )
                implement_branch_name = pr_info.head_ref_name
                if pr_info.is_cross_repository:
                    checkout_fork_pr(
                        repo_root,
                        target.repository_full_name,
                        target.number,
                    )
                else:
                    fetch_and_checkout_branch(repo_root, implement_branch_name)

            if pr_info is not None and pr_info.is_cross_repository:
                head_sha_before = get_head_sha(repo_root)

            target_context = build_target_context(
                github_client,
                target,
                command.comment_id,
                _get_target_context_state(ready_execution),
                pr_info,
            )
            _remember_target_context_state(ready_execution, target_context.state)
            provider_prompt = build_provider_prompt(
                ready_execution,
                target_context.prompt_block,
                implement_branch_name,
                worktree_ref,
            )
            execution_result = execute_provider(
                repo_root,
                ready_execution,
                env,
                preflight_duration_seconds,
                provider_prompt,
            )
            execution_result = execution_result.model_copy(
                update={
                    "state_ref": merge_target_context_state(
                        execution_result.state_ref,
                        target_context.state,
                    )
                }
            )

            created_pr = handle_post_execution(
                repo_root,
                ready_execution,
                execution_result,
                github_client,
                implement_branch_name,
                pr_info,
                head_sha_before,
                fork_base_ref,
                env,
            )
            assert execution_result is not None
            finalize_status = execution_result.status
        except BaseException as exc:
            primary_error = exc
            raise
    finally:
        if (
            github_client is not None
            and not command.dry_run
            and command.comment_id is not None
        ):
            assert target is not None
            assert target.repository_full_name is not None
            finalize_reactions(
                github_client,
                target.repository_full_name,
                command.comment_id,
                eyes_reaction_id,
                finalize_status,
            )
        if (
            github_client is not None
            and not command.dry_run
            and command.trigger_label_name is not None
        ):
            try:
                assert target is not None
                assert target.repository_full_name is not None
                assert target.number is not None
                github_client.remove_issue_label(
                    target.repository_full_name,
                    target.number,
                    command.trigger_label_name,
                )
            except Exception as exc:
                if _has_primary_failure(primary_error, execution_result):
                    print(
                        f"ラベル削除に失敗しました: {_format_exception(exc)}",
                        file=sys.stderr,
                    )
                else:
                    message = f"ラベル削除に失敗しました: {_format_exception(exc)}"
                    raise CommandCleanupError(message, created_pr) from exc

    assert execution_result is not None
    return execution_result, created_pr


def _is_github_target(target: ResolvedTarget | None) -> bool:
    return target is not None and target.backend == "github"


def _has_primary_failure(
    primary_error: BaseException | None,
    execution_result: ExecutionResult | None,
) -> bool:
    if primary_error is not None:
        return True
    return execution_result is not None and execution_result.status != "success"


def _format_exception(error: BaseException) -> str:
    message = str(error).strip()
    if message == "":
        return error.__class__.__name__
    return f"{error.__class__.__name__}: {message}"


def _resolve_fork_base_ref(
    repo_root: Path,
    target: ResolvedTarget,
    github_client: GitHubClient,
) -> str | None:
    assert target.repository_full_name is not None
    repo_info = github_client.get_repo_info(target.repository_full_name)
    if not repo_info.is_fork:
        return None

    assert repo_info.parent_full_name is not None
    assert repo_info.parent_default_branch is not None
    upstream_url = f"https://github.com/{repo_info.parent_full_name}"
    setup_upstream_remote(repo_root, upstream_url)
    fetch_remote(repo_root, "upstream")
    return f"upstream/{repo_info.parent_default_branch}"


def _get_target_context_state(
    ready_execution: ReadyExecution,
) -> TargetContextState:
    session = ready_execution.resolved_session
    if session is None:
        return empty_target_context_state()
    state_ref = session.state_ref
    if state_ref is None:
        return empty_target_context_state()
    if state_ref.target_context_state is None:
        return empty_target_context_state()
    return state_ref.target_context_state


def _remember_target_context_state(
    ready_execution: ReadyExecution,
    target_context_state: TargetContextState,
) -> None:
    session = ready_execution.resolved_session
    if session is None:
        return
    state_ref = session.state_ref
    if state_ref is None:
        state_ref = SessionStateRef()
    session.state_ref = merge_target_context_state(state_ref, target_context_state)
