"""`next` コマンドを過去の履歴から実コマンドへ解決する。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.config import VVAIConfig
from vv_ai.github import (
    GitHubClient,
    GitHubComment,
    GitHubIssueLabeledEvent,
    build_github_client,
)
from vv_ai.input import (
    CommandName,
    InputError,
    parse_comment_invocation,
    parse_label_invocation,
)
from vv_ai.resolve import ResolvedCommand, ResolvedTarget

HistorySource = Literal["comment", "label"]
HistorySortKey = tuple[str, int, int]


class NextResolutionError(Exception):
    """`next` コマンドの解決に失敗したことを表す例外。"""


class NextHistoryEntry(BaseModel):
    """`next` 解決に使う過去コマンド履歴を表す。"""

    model_config = ConfigDict(extra="forbid")

    command: CommandName
    created_at: str
    id: int | None
    source: HistorySource


def resolve_next_command(
    repo_root: Path,
    command: ResolvedCommand,
    config: VVAIConfig,
) -> ResolvedCommand:
    """`next` コマンドを過去の履歴から実コマンドへ解決する。"""
    if command.command != "next":
        return command

    target = _require_target(command)
    github_client = _build_history_github_client(target)
    is_sub_issue = _resolve_is_sub_issue(target, github_client)
    history = _load_history(repo_root, command, target, config, github_client)
    resolved_command = _resolve_next_from_history(history, target, is_sub_issue)
    return command.model_copy(update={"command": resolved_command})


def _require_target(command: ResolvedCommand) -> ResolvedTarget:
    if command.target is None:
        raise NextResolutionError("`next` コマンドには target 解決が必要です")
    return command.target


def _build_history_github_client(target: ResolvedTarget) -> GitHubClient | None:
    if target.backend == "github":
        return build_github_client()
    if target.backend == "local":
        return None
    raise NextResolutionError("未対応の target backend です")


def _resolve_is_sub_issue(
    target: ResolvedTarget,
    github_client: GitHubClient | None,
) -> bool:
    if target.backend == "local":
        return False
    if target.kind == "pr":
        return False
    if github_client is None:
        raise NextResolutionError("GitHub client がありません")

    repository_full_name, number = _require_github_target_fields(target)
    try:
        return (
            github_client.get_issue_parent_number(repository_full_name, number)
            is not None
        )
    except Exception as exc:
        raise NextResolutionError("Issue の親番号取得に失敗しました") from exc


def _load_history(
    repo_root: Path,
    command: ResolvedCommand,
    target: ResolvedTarget,
    config: VVAIConfig,
    github_client: GitHubClient | None,
) -> list[NextHistoryEntry]:
    if target.backend == "github":
        if github_client is None:
            raise NextResolutionError("GitHub client がありません")
        return _load_github_history(command, target, config, github_client)
    if target.backend == "local":
        return _load_local_history(repo_root, target)
    raise NextResolutionError("未対応の target backend です")


def _load_github_history(
    command: ResolvedCommand,
    target: ResolvedTarget,
    config: VVAIConfig,
    github_client: GitHubClient,
) -> list[NextHistoryEntry]:
    repository_full_name, number = _require_github_target_fields(target)
    try:
        comments = github_client.list_issue_comments(repository_full_name, number)
    except Exception as exc:
        raise NextResolutionError("コメント履歴の取得に失敗しました") from exc
    try:
        labeled_events = github_client.list_issue_labeled_events(
            repository_full_name,
            number,
        )
    except Exception as exc:
        raise NextResolutionError("ラベル履歴の取得に失敗しました") from exc

    current_sort_key = _resolve_current_history_sort_key(
        command,
        comments,
        labeled_events,
    )
    history: list[NextHistoryEntry] = []
    for comment in comments:
        entry = _build_github_comment_history_entry(target, config, comment)
        if entry is not None:
            history.append(entry)
    for labeled_event in labeled_events:
        entry = _build_github_label_history_entry(target, config, labeled_event)
        if entry is not None:
            history.append(entry)

    return sorted(
        [
            entry
            for entry in history
            if _is_before_current_history(entry, current_sort_key)
        ],
        key=_history_sort_key,
    )


def _resolve_current_history_sort_key(
    command: ResolvedCommand,
    comments: list[GitHubComment],
    labeled_events: list[GitHubIssueLabeledEvent],
) -> HistorySortKey | None:
    if command.comment_id is not None:
        return _resolve_current_comment_sort_key(command, comments)
    if command.trigger_label_name is not None:
        return _resolve_current_label_sort_key(command, labeled_events)
    return None


def _resolve_current_comment_sort_key(
    command: ResolvedCommand,
    comments: list[GitHubComment],
) -> HistorySortKey:
    for comment in comments:
        if comment.id == command.comment_id:
            return _history_sort_key(
                NextHistoryEntry(
                    command=command.command,
                    created_at=comment.created_at,
                    id=comment.id,
                    source="comment",
                )
            )
    raise NextResolutionError("現在処理中のコメントが履歴内に見つかりません")


def _resolve_current_label_sort_key(
    command: ResolvedCommand,
    labeled_events: list[GitHubIssueLabeledEvent],
) -> HistorySortKey:
    if command.trigger_label_name is None:
        raise NextResolutionError("現在処理中のラベル名がありません")
    if command.actor is None:
        raise NextResolutionError("現在処理中のラベル actor がありません")

    current_events = [
        labeled_event
        for labeled_event in labeled_events
        if labeled_event.label_name == command.trigger_label_name
        and labeled_event.actor.login == command.actor
    ]
    if len(current_events) == 0:
        raise NextResolutionError("現在処理中のラベル event が履歴内に見つかりません")
    return max(
        (
            _history_sort_key(
                NextHistoryEntry(
                    command=command.command,
                    created_at=labeled_event.created_at,
                    id=labeled_event.id,
                    source="label",
                )
            )
            for labeled_event in current_events
        )
    )


def _is_before_current_history(
    entry: NextHistoryEntry,
    current_sort_key: HistorySortKey | None,
) -> bool:
    if current_sort_key is None:
        return True
    return _history_sort_key(entry) < current_sort_key


def _history_sort_key(entry: NextHistoryEntry) -> HistorySortKey:
    source_order = 0 if entry.source == "comment" else 1
    return entry.created_at, source_order, entry.id or 0


def _build_github_comment_history_entry(
    target: ResolvedTarget,
    config: VVAIConfig,
    comment: GitHubComment,
) -> NextHistoryEntry | None:
    if comment.author.login not in config.allowed_users:
        return None
    command = _parse_history_command(comment.body)
    if command is None:
        return None
    if _should_ignore_command(target, command):
        return None
    return NextHistoryEntry(
        command=command,
        created_at=comment.created_at,
        id=comment.id,
        source="comment",
    )


def _build_github_label_history_entry(
    target: ResolvedTarget,
    config: VVAIConfig,
    labeled_event: GitHubIssueLabeledEvent,
) -> NextHistoryEntry | None:
    if labeled_event.actor.login not in config.allowed_users:
        return None
    command = _parse_label_history_command(labeled_event.label_name)
    if command is None:
        return None
    if _should_ignore_command(target, command):
        return None
    return NextHistoryEntry(
        command=command,
        created_at=labeled_event.created_at,
        id=labeled_event.id,
        source="label",
    )


def _load_local_history(repo_root: Path, target: ResolvedTarget) -> list[NextHistoryEntry]:
    if target.path is None:
        raise NextResolutionError("local target の path がありません")

    target_path = Path(target.path)
    if not target_path.is_absolute():
        target_path = repo_root / target_path
    comments_dir = target_path / "comments"
    if not comments_dir.is_dir():
        raise NextResolutionError("local target の comments ディレクトリがありません")

    history: list[NextHistoryEntry] = []
    for comment_file in sorted(comments_dir.glob("*.md")):
        command = _parse_history_command(comment_file.read_text(encoding="utf-8"))
        if command is None:
            continue
        if _should_ignore_command(target, command):
            continue
        history.append(
            NextHistoryEntry(
                command=command,
                created_at=comment_file.name,
                id=None,
                source="comment",
            )
        )
    return history


def _parse_history_command(body: str) -> CommandName | None:
    try:
        return parse_comment_invocation(body).command
    except InputError:
        return None


def _parse_label_history_command(label_name: str) -> CommandName | None:
    try:
        return parse_label_invocation(label_name)
    except InputError:
        return None


def _should_ignore_command(target: ResolvedTarget, command: CommandName) -> bool:
    if command in {"reply", "issue"}:
        return True
    if target.kind == "issue":
        return command == "review"
    if target.kind == "pr":
        return command in {"confirm", "requirements", "arch", "detail", "breakdown"}
    raise NextResolutionError("未対応の target 種別です")


def _resolve_next_from_history(
    history: list[NextHistoryEntry],
    target: ResolvedTarget,
    is_sub_issue: bool,
) -> CommandName:
    resolved_history: list[CommandName] = []
    for entry in history:
        if entry.command != "next":
            resolved_history.append(entry.command)
            continue
        try:
            resolved_history.append(
                _resolve_next_command_name(resolved_history, target, is_sub_issue)
            )
        except NextResolutionError:
            continue
    return _resolve_next_command_name(resolved_history, target, is_sub_issue)


def _resolve_next_command_name(
    resolved_history: list[CommandName],
    target: ResolvedTarget,
    is_sub_issue: bool,
) -> CommandName:
    if target.kind == "issue":
        return _resolve_issue_next_command(resolved_history, is_sub_issue)
    if target.kind == "pr":
        return _resolve_pr_next_command(resolved_history)
    raise NextResolutionError("未対応の target 種別です")


def _resolve_issue_next_command(
    resolved_history: list[CommandName],
    is_sub_issue: bool,
) -> CommandName:
    if len(resolved_history) == 0:
        if is_sub_issue:
            return "implement"
        return "confirm"

    last_command = resolved_history[-1]
    if last_command == "confirm":
        return "requirements"
    if last_command == "requirements":
        return "arch"
    if last_command == "arch":
        return "detail"
    if last_command == "detail":
        return "breakdown"
    if last_command == "breakdown":
        raise NextResolutionError("Issue の breakdown 後に進めるコマンドがありません")
    if last_command == "implement":
        raise NextResolutionError("Issue の implement 後に進めるコマンドがありません")
    raise NextResolutionError("Issue の履歴から `next` を解決できません")


def _resolve_pr_next_command(resolved_history: list[CommandName]) -> CommandName:
    if len(resolved_history) == 0:
        return "review"

    last_command = resolved_history[-1]
    if last_command == "review":
        return "implement"
    if last_command == "implement":
        return "review"
    raise NextResolutionError("PR の履歴から `next` を解決できません")


def _require_github_target_fields(target: ResolvedTarget) -> tuple[str, int]:
    if target.repository_full_name is None:
        raise NextResolutionError("GitHub target の repository_full_name がありません")
    if target.number is None:
        raise NextResolutionError("GitHub target の number がありません")
    return target.repository_full_name, target.number
