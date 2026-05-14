"""next コマンドを既存コマンドへ解決する。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.config import VVAIConfig
from vv_ai.github import GitHubClient, GitHubClientError, build_github_client
from vv_ai.input import (
    CommandName,
    CommentInvocation,
    InputError,
    parse_comment_invocation,
)
from vv_ai.resolve import ResolvedCommand, ResolvedTarget


class NextResolutionError(Exception):
    """next コマンドの解決に失敗したことを表す例外。"""


class NextHistoryEntry(BaseModel):
    """next 解決に使う過去コマンド履歴。"""

    model_config = ConfigDict(extra="forbid")

    command: CommandName
    created_at: str
    id: int | None


def resolve_next_command(
    repo_root: Path,
    command: ResolvedCommand,
    config: VVAIConfig,
) -> ResolvedCommand:
    """next コマンドを既存コマンドに置き換えた ResolvedCommand を返す。"""
    if command.command != "next":
        return command

    target = command.target
    if target is None:
        raise NextResolutionError("`next` コマンドには target 指定が必要です")

    is_sub_issue = False
    if target.backend == "github":
        github_client = build_github_client()
        try:
            if target.kind == "issue":
                is_sub_issue = _is_github_sub_issue(target, github_client)
            history = _load_github_history(command, config, github_client)
        except GitHubClientError as exc:
            raise NextResolutionError(str(exc)) from exc
    else:
        history = _load_local_history(command)

    resolved_name = _resolve_next_name(target, history, is_sub_issue)
    return command.model_copy(update={"command": resolved_name})


def _is_github_sub_issue(
    target: ResolvedTarget,
    github_client: GitHubClient,
) -> bool:
    """GitHub Issue が親 Issue を持つかどうかを返す。"""
    repository_full_name, number = _require_github_target_fields(target)
    return github_client.get_issue_parent_number(repository_full_name, number) is not None


def _load_github_history(
    command: ResolvedCommand,
    config: VVAIConfig,
    github_client: GitHubClient,
) -> list[NextHistoryEntry]:
    """GitHub comment から next 解決用の履歴を読み込む。"""
    target = command.target
    if target is None:
        raise NextResolutionError("`next` コマンドには target 指定が必要です")
    repository_full_name, number = _require_github_target_fields(target)

    comments = github_client.list_issue_comments(repository_full_name, number)
    entries: list[NextHistoryEntry] = []
    for comment in sorted(comments, key=lambda item: (item.created_at, item.id)):
        if command.comment_id is not None and comment.id == command.comment_id:
            continue
        if comment.author.login not in config.allowed_users:
            continue
        invocation = _parse_history_comment(comment.body)
        if invocation is None:
            continue
        entries.append(
            NextHistoryEntry(
                command=invocation.command,
                created_at=comment.created_at,
                id=comment.id,
            )
        )
    return entries


def _load_local_history(command: ResolvedCommand) -> list[NextHistoryEntry]:
    """local target の comment file から next 解決用の履歴を読み込む。"""
    target = command.target
    if target is None:
        raise NextResolutionError("`next` コマンドには target 指定が必要です")
    if target.path is None:
        raise NextResolutionError("local target の path が見つかりません")

    comments_dir = Path(target.path) / "comments"
    if comments_dir.is_dir() is False:
        raise NextResolutionError(f"local target の comments が見つかりません: {comments_dir}")

    entries: list[NextHistoryEntry] = []
    try:
        comment_files = sorted(comments_dir.glob("*.md"))
        for comment_file in comment_files:
            if comment_file.is_symlink():
                raise NextResolutionError(
                    f"local comment file がシンボリックリンクです: {comment_file}"
                )
            invocation = _parse_history_comment(comment_file.read_text(encoding="utf-8"))
            if invocation is None:
                continue
            entries.append(
                NextHistoryEntry(
                    command=invocation.command,
                    created_at=comment_file.name,
                    id=None,
                )
            )
    except OSError as exc:
        raise NextResolutionError("local comment の読み込みに失敗しました") from exc
    return entries


def _resolve_next_name(
    target: ResolvedTarget,
    history: list[NextHistoryEntry],
    is_sub_issue: bool,
) -> CommandName:
    """target 種別と履歴から現在の next が実行するコマンド名を返す。"""
    state: CommandName | None = None
    if target.kind == "issue":
        for entry in history:
            state = _apply_issue_history(entry.command, state, is_sub_issue)
        return _resolve_issue_next(state, is_sub_issue, True)

    if target.kind == "pr":
        for entry in history:
            state = _apply_pr_history(entry.command, state)
        return _resolve_pr_next(state)

    raise NextResolutionError("target kind が不正です")


def _apply_issue_history(
    command: CommandName,
    state: CommandName | None,
    is_sub_issue: bool,
) -> CommandName | None:
    """Issue の履歴コマンドを工程状態へ反映する。"""
    if command == "next":
        try:
            resolved_command = _resolve_issue_next(state, is_sub_issue, False)
        except NextResolutionError:
            return state
        return _apply_issue_history(resolved_command, state, is_sub_issue)
    if command in {"confirm", "requirements", "arch", "detail", "breakdown", "implement"}:
        return command
    return state


def _apply_pr_history(
    command: CommandName,
    state: CommandName | None,
) -> CommandName | None:
    """PR の履歴コマンドを工程状態へ反映する。"""
    if command == "next":
        resolved_command = _resolve_pr_next(state)
        return _apply_pr_history(resolved_command, state)
    if command in {"review", "implement"}:
        return command
    return state


def _resolve_issue_next(
    state: CommandName | None,
    is_sub_issue: bool,
    current: bool,
) -> CommandName:
    """Issue の工程状態から次コマンド名を返す。"""
    if state is None:
        if is_sub_issue is True:
            return "implement"
        return "confirm"
    if state == "confirm":
        return "requirements"
    if state == "requirements":
        return "arch"
    if state == "arch":
        return "detail"
    if state == "detail":
        return "breakdown"
    if state == "breakdown":
        message = "`breakdown` 後の親 Issue では `next` を実行できません"
        if current is True:
            raise NextResolutionError(message)
        raise NextResolutionError(message)
    if state == "implement":
        message = "Issue の `implement` 後は `next` を実行できません"
        if current is True:
            raise NextResolutionError(message)
        raise NextResolutionError(message)
    raise NextResolutionError(f"Issue の next が未対応の状態です: {state}")


def _resolve_pr_next(state: CommandName | None) -> CommandName:
    """PR の工程状態から次コマンド名を返す。"""
    if state is None:
        return "review"
    if state == "review":
        return "implement"
    if state == "implement":
        return "review"
    raise NextResolutionError(f"PR の next が未対応の状態です: {state}")


def _parse_history_comment(comment_body: str) -> CommentInvocation | None:
    """履歴コメントを vv-ai invocation として解釈する。"""
    try:
        return parse_comment_invocation(comment_body)
    except InputError:
        return None


def _require_github_target_fields(target: ResolvedTarget) -> tuple[str, int]:
    """GitHub target に必要な repository と番号を返す。"""
    if target.backend != "github":
        raise NextResolutionError("GitHub target ではありません")
    if target.repository_full_name is None:
        raise NextResolutionError("GitHub target に repository がありません")
    if target.number is None:
        raise NextResolutionError("GitHub target に番号がありません")
    return target.repository_full_name, target.number
