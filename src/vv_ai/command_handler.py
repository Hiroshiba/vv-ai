"""コマンドディスパッチ・reaction ハンドリング・後処理。"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from vv_ai.execution import ExecutionResult, ExecutionStatus
from vv_ai.git_ops import (
    GitOpsError,
    create_and_checkout_branch,
    fetch_and_checkout_branch,
    generate_implement_branch_name,
    get_default_branch,
    push_branch,
)
from vv_ai.github import (
    GitHubClient,
    GitHubClientError,
    GitHubReactionContent,
    build_github_client,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.prompt import build_provider_prompt
from vv_ai.provider_execution import execute_provider
from vv_ai.resolve import ResolvedTarget


class CommandError(Exception):
    """コマンド実行の前提条件エラー。"""


def run_command(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> ExecutionResult:
    """コマンド固有の前処理・provider 実行・後処理を行って ExecutionResult を返す。"""
    command = ready_execution.command
    target = command.target

    if command.command == "review" and (target is None or target.kind != "pr"):
        raise CommandError("`review` コマンドは PR を対象に指定してください")

    github_client = build_github_client() if _is_github_target(target) else None

    eyes_reaction_id: int | None = None
    if (
        github_client is not None
        and not command.dry_run
        and command.comment_id is not None
    ):
        assert target is not None
        assert target.repository_full_name is not None
        eyes_reaction_id = _add_reaction_safe(
            github_client,
            target.repository_full_name,
            command.comment_id,
            "eyes",
        )

    past_vvai_comments = _fetch_past_vvai_comments(github_client, target)

    implement_branch_name: str | None = None
    execution_result: ExecutionResult | None = None
    finalize_status: ExecutionStatus = "failure"
    try:
        if command.command == "implement" and target is not None and target.kind == "issue":
            assert target.number is not None
            implement_branch_name = generate_implement_branch_name(target.number)
            try:
                create_and_checkout_branch(repo_root, implement_branch_name)
            except GitOpsError as exc:
                raise CommandError(str(exc)) from exc
        elif command.command == "implement" and target is not None and target.kind == "pr":
            assert target.repository_full_name is not None
            assert target.number is not None
            if not _is_github_target(target):
                raise CommandError("ローカル PR への implement は未対応です")
            assert github_client is not None
            try:
                pr_info = github_client.get_pull_request(
                    target.repository_full_name, target.number
                )
            except GitHubClientError as exc:
                raise CommandError(str(exc)) from exc
            # TODO: fork PR の implement は別タスクで実装
            if pr_info.is_cross_repository:
                raise CommandError("fork PR への implement は未対応です")
            implement_branch_name = pr_info.head_ref_name
            try:
                fetch_and_checkout_branch(repo_root, implement_branch_name)
            except GitOpsError as exc:
                raise CommandError(str(exc)) from exc

        provider_prompt = build_provider_prompt(
            ready_execution, past_vvai_comments, implement_branch_name
        )
        execution_result = execute_provider(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            provider_prompt,
        )

        _handle_post_execution(
            repo_root, ready_execution, execution_result, github_client, implement_branch_name
        )
        assert execution_result is not None
        finalize_status = execution_result.status
    finally:
        if (
            github_client is not None
            and not command.dry_run
            and command.comment_id is not None
        ):
            assert target is not None
            assert target.repository_full_name is not None
            _finalize_reactions(
                github_client,
                target.repository_full_name,
                command.comment_id,
                eyes_reaction_id,
                finalize_status,
            )

    assert execution_result is not None
    return execution_result


def _is_github_target(target: ResolvedTarget | None) -> bool:
    """GitHub backend の target かどうかを返す。"""
    return target is not None and target.backend == "github"


def _fetch_past_vvai_comments(
    github_client: GitHubClient | None,
    target: ResolvedTarget | None,
) -> list[str]:
    """過去の @vv-ai コメントを取得する。"""
    if github_client is None or target is None:
        return []
    if target.repository_full_name is None or target.number is None:
        return []
    try:
        comments = github_client.list_issue_comments(
            target.repository_full_name, target.number
        )
    except GitHubClientError as exc:
        print(f"過去コメント取得に失敗しました: {exc}", file=sys.stderr)
        return []
    return [c.body for c in comments if c.body.startswith("@vv-ai")]


def _handle_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str | None,
) -> None:
    """コマンド固有の後処理を行う。"""
    command_name = ready_execution.command.command
    if command_name in ("reply", "plan", "review"):
        _post_response_comment(ready_execution, execution_result, github_client)
    elif command_name == "implement" and implement_branch_name is not None:
        target = ready_execution.command.target
        if target is not None and target.kind == "pr":
            _handle_implement_pr_post_execution(
                repo_root, ready_execution, execution_result, implement_branch_name
            )
        else:
            _handle_implement_issue_post_execution(
                repo_root, ready_execution, execution_result, github_client, implement_branch_name
            )


def _handle_implement_issue_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str,
) -> None:
    """implement + Issue 起点の後処理（push + PR 作成）を行う。"""
    command = ready_execution.command
    target = command.target

    if execution_result.status != "success":
        return

    if command.dry_run or not _is_github_target(target):
        print(f"[dry-run/local] push と PR 作成をスキップします。ブランチ: {implement_branch_name}")
        return

    assert target is not None
    assert target.repository_full_name is not None
    assert target.number is not None
    assert github_client is not None

    try:
        push_branch(repo_root, implement_branch_name)
    except GitOpsError as exc:
        raise CommandError(str(exc)) from exc

    try:
        issue = github_client.get_issue(target.repository_full_name, target.number)
    except GitHubClientError as exc:
        raise CommandError(str(exc)) from exc
    pr_title = issue.title
    pr_body = f"Closes #{target.number}"
    try:
        base_branch = get_default_branch(repo_root)
    except GitOpsError as exc:
        raise CommandError(str(exc)) from exc

    try:
        pr = github_client.create_pull_request(
            target.repository_full_name,
            pr_title,
            pr_body,
            implement_branch_name,
            base_branch,
            maintainer_can_modify=True,
        )
    except GitHubClientError as exc:
        raise CommandError(str(exc)) from exc
    print(f"PR を作成しました: {pr.url}")


def _handle_implement_pr_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    implement_branch_name: str,
) -> None:
    """implement + PR 起点の後処理（push のみ）を行う。"""
    command = ready_execution.command
    target = command.target

    if execution_result.status != "success":
        return

    if command.dry_run or not _is_github_target(target):
        print(f"[dry-run/local] push をスキップします。ブランチ: {implement_branch_name}")
        return

    try:
        push_branch(repo_root, implement_branch_name)
    except GitOpsError as exc:
        raise CommandError(str(exc)) from exc
    print(f"ブランチ `{implement_branch_name}` を push しました。")


def _post_response_comment(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    """reply / plan / review の応答テキストをコメント投稿する。"""
    command = ready_execution.command
    response_text = execution_result.response_text
    if response_text is None:
        return

    target = command.target
    if command.dry_run or github_client is None or not _is_github_target(target):
        print(response_text)
        return

    assert target is not None
    assert target.repository_full_name is not None
    assert target.number is not None
    try:
        github_client.create_issue_comment(
            target.repository_full_name, target.number, response_text
        )
    except GitHubClientError as exc:
        print(f"コメント投稿に失敗しました: {exc}", file=sys.stderr)


def _add_reaction_safe(
    github_client: GitHubClient,
    repository_full_name: str,
    comment_id: int,
    content: GitHubReactionContent,
) -> int | None:
    """reaction を付与し、reaction_id を返す。失敗しても None を返す。"""
    try:
        reaction = github_client.add_issue_comment_reaction(
            repository_full_name, comment_id, content
        )
        return reaction.id
    except GitHubClientError as exc:
        print(f"reaction 付与に失敗しました: {exc}", file=sys.stderr)
        return None


def _finalize_reactions(
    github_client: GitHubClient,
    repository_full_name: str,
    comment_id: int,
    eyes_reaction_id: int | None,
    status: str,
) -> None:
    """eyes を除去し、失敗時は confused を付与する。"""
    if eyes_reaction_id is not None:
        try:
            github_client.remove_issue_comment_reaction(
                repository_full_name, comment_id, eyes_reaction_id
            )
        except GitHubClientError as exc:
            print(f"eyes reaction 除去に失敗しました: {exc}", file=sys.stderr)

    if status != "success":
        _add_reaction_safe(github_client, repository_full_name, comment_id, "confused")
