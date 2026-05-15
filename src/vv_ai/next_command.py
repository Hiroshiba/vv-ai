"""`next` コマンドを過去履歴から既存コマンドへ解決する。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.config import VVAIConfig
from vv_ai.github import GitHubClient, GitHubComment, build_github_client
from vv_ai.input import CommandName, InputError, parse_comment_invocation
from vv_ai.resolve import ResolvedCommand, ResolvedTarget


class NextResolutionError(Exception):
    """`next` コマンドの解決に失敗したことを表す例外。"""


class NextHistoryEntry(BaseModel):
    """`next` 解決に使う過去コマンド履歴。"""

    model_config = ConfigDict(extra="forbid")

    command: CommandName
    created_at: str
    id: int | None


def resolve_next_command(
    repo_root: Path,
    command: ResolvedCommand,
    config: VVAIConfig,
) -> ResolvedCommand:
    """`next` コマンドを履歴に基づいて実コマンドへ解決する。"""
    if command.command != "next":
        return command

    target = _require_target(command)
    github_client = _build_history_github_client(target)
    is_sub_issue = _resolve_is_sub_issue(target, github_client)
    history = _load_history(repo_root, command, target, config, github_client)
    resolved_command = _resolve_next_from_history(history, target, is_sub_issue)
    return command.model_copy(update={"command": resolved_command})


def _require_target(command: ResolvedCommand) -> ResolvedTarget:
    """解決済み target を取得する。"""
    if command.target is None:
        raise NextResolutionError("`next` コマンドには解決済み target が必要です")
    return command.target


def _build_history_github_client(target: ResolvedTarget) -> GitHubClient | None:
    """GitHub target 用の client を返す。"""
    if target.backend != "github":
        return None
    return build_github_client()


def _resolve_is_sub_issue(
    target: ResolvedTarget,
    github_client: GitHubClient | None,
) -> bool:
    """target が GitHub サブ Issue かを返す。"""
    if target.kind != "issue":
        return False
    if target.backend != "github":
        return False
    if github_client is None:
        raise NextResolutionError("GitHub target の履歴取得に client が必要です")
    repository_full_name, number = _require_github_target_fields(target)
    return (
        github_client.get_issue_parent_number(repository_full_name, number)
        is not None
    )


def _load_history(
    repo_root: Path,
    command: ResolvedCommand,
    target: ResolvedTarget,
    config: VVAIConfig,
    github_client: GitHubClient | None,
) -> list[NextHistoryEntry]:
    """target backend に応じて過去コマンド履歴を読み込む。"""
    if target.backend == "github":
        if github_client is None:
            raise NextResolutionError("GitHub target の履歴取得に client が必要です")
        return _load_github_history(command, target, config, github_client)
    if target.backend == "local":
        return _load_local_history(repo_root, target)
    raise NextResolutionError(f"未対応の target backend です: {target.backend}")


def _load_github_history(
    command: ResolvedCommand,
    target: ResolvedTarget,
    config: VVAIConfig,
    github_client: GitHubClient,
) -> list[NextHistoryEntry]:
    """GitHub comment から過去コマンド履歴を読み込む。"""
    repository_full_name, number = _require_github_target_fields(target)
    comments = github_client.list_issue_comments(repository_full_name, number)
    entries: list[NextHistoryEntry] = []
    for comment in sorted(comments, key=lambda item: (item.created_at, item.id)):
        entry = _build_github_history_entry(command, target, config, comment)
        if entry is not None:
            entries.append(entry)
    return entries


def _build_github_history_entry(
    command: ResolvedCommand,
    target: ResolvedTarget,
    config: VVAIConfig,
    comment: GitHubComment,
) -> NextHistoryEntry | None:
    """GitHub comment を履歴 entry に変換する。"""
    if command.comment_id is not None and comment.id == command.comment_id:
        return None
    if comment.author.login not in config.allowed_users:
        return None
    parsed_command = _parse_history_command(comment.body)
    if parsed_command is None:
        return None
    if _should_ignore_command(target, parsed_command):
        return None
    return NextHistoryEntry(
        command=parsed_command,
        created_at=comment.created_at,
        id=comment.id,
    )


def _load_local_history(
    repo_root: Path,
    target: ResolvedTarget,
) -> list[NextHistoryEntry]:
    """local comment Markdown から過去コマンド履歴を読み込む。"""
    if target.path is None:
        raise NextResolutionError("local target の path が見つかりません")

    target_path = Path(target.path)
    if not target_path.is_absolute():
        target_path = repo_root / target_path
    comments_dir = target_path / "comments"
    if not comments_dir.is_dir():
        raise NextResolutionError(f"`{comments_dir}` が見つかりません")

    entries: list[NextHistoryEntry] = []
    for comment_path in sorted(comments_dir.glob("*.md")):
        try:
            body = comment_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise NextResolutionError(
                f"`{comment_path}` の読み込みに失敗しました"
            ) from exc
        parsed_command = _parse_history_command(body)
        if parsed_command is None:
            continue
        if _should_ignore_command(target, parsed_command):
            continue
        entries.append(
            NextHistoryEntry(
                command=parsed_command,
                created_at=comment_path.name,
                id=None,
            )
        )
    return entries


def _parse_history_command(body: str) -> CommandName | None:
    """コメント本文から履歴対象コマンドを取り出す。"""
    try:
        parsed = parse_comment_invocation(body)
    except InputError:
        return None
    return parsed.command


def _should_ignore_command(target: ResolvedTarget, command: CommandName) -> bool:
    """target 種別に応じて履歴から除外する command かを返す。"""
    if command in {"reply", "issue"}:
        return True
    if target.kind == "issue":
        return command == "review"
    if target.kind == "pr":
        return command in {"confirm", "requirements", "arch", "detail", "breakdown"}
    raise NextResolutionError(f"未対応の target kind です: {target.kind}")


def _resolve_next_from_history(
    history: list[NextHistoryEntry],
    target: ResolvedTarget,
    is_sub_issue: bool,
) -> CommandName:
    """過去履歴を再生して現在の `next` を解決する。"""
    resolved_history: list[CommandName] = []
    for entry in history:
        if entry.command == "next":
            try:
                resolved_history.append(
                    _resolve_next_command_name(resolved_history, target, is_sub_issue)
                )
            except NextResolutionError:
                continue
            continue
        resolved_history.append(entry.command)
    return _resolve_next_command_name(resolved_history, target, is_sub_issue)


def _resolve_next_command_name(
    resolved_history: list[CommandName],
    target: ResolvedTarget,
    is_sub_issue: bool,
) -> CommandName:
    """現在の履歴状態から次の command 名を返す。"""
    if target.kind == "issue":
        return _resolve_issue_next_command(resolved_history, is_sub_issue)
    if target.kind == "pr":
        return _resolve_pr_next_command(resolved_history)
    raise NextResolutionError(f"未対応の target kind です: {target.kind}")


def _resolve_issue_next_command(
    resolved_history: list[CommandName],
    is_sub_issue: bool,
) -> CommandName:
    """Issue の履歴状態から次の command 名を返す。"""
    if not resolved_history:
        if is_sub_issue:
            return "implement"
        return "confirm"

    previous_command = resolved_history[-1]
    transitions: dict[CommandName, CommandName] = {
        "confirm": "requirements",
        "requirements": "arch",
        "arch": "detail",
        "detail": "breakdown",
    }
    next_command = transitions.get(previous_command)
    if next_command is None:
        raise NextResolutionError(
            f"Issue の `{previous_command}` 後に `next` は解決できません"
        )
    return next_command


def _resolve_pr_next_command(resolved_history: list[CommandName]) -> CommandName:
    """PR の履歴状態から次の command 名を返す。"""
    if not resolved_history:
        return "review"

    previous_command = resolved_history[-1]
    if previous_command == "review":
        return "implement"
    if previous_command == "implement":
        return "review"
    raise NextResolutionError(
        f"PR の `{previous_command}` 後に `next` は解決できません"
    )


def _require_github_target_fields(target: ResolvedTarget) -> tuple[str, int]:
    """GitHub target の repository と番号を取得する。"""
    if target.repository_full_name is None or target.number is None:
        raise NextResolutionError(
            "GitHub target の repository と番号が見つかりません"
        )
    return target.repository_full_name, target.number
