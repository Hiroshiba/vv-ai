"""コマンドディスパッチ・reaction ハンドリング・後処理。"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from vv_ai.execution import ExecutionResult
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


def run_command(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> ExecutionResult:
    """コマンド固有の前処理・provider 実行・後処理を行って ExecutionResult を返す。"""
    command = ready_execution.command
    target = command.target
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
    provider_prompt = build_provider_prompt(ready_execution, past_vvai_comments)
    execution_result = execute_provider(
        repo_root,
        ready_execution,
        env,
        preflight_duration_seconds,
        provider_prompt,
    )

    _handle_post_execution(ready_execution, execution_result, github_client)

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
            execution_result.status,
        )

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
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    """コマンド固有の後処理を行う。"""
    command_name = ready_execution.command.command
    if command_name in ("reply", "plan"):
        _post_response_comment(ready_execution, execution_result, github_client)


def _post_response_comment(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    """reply / plan の応答テキストをコメント投稿する。"""
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
