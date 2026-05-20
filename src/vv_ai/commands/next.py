"""`next` コマンドを過去の履歴から実コマンドへ解決する。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.config import VVAIConfig
from vv_ai.backends.github.client import GitHubClient, build_github_client
from vv_ai.backends.github.models import GitHubIssueTimelineEvent
from vv_ai.inputs.build import (
    parse_comment_invocation,
    parse_label_invocation,
)
from vv_ai.next_decision import NextDecisionError, parse_next_decision_history_marker
from vv_ai.inputs.models import (
    CommandName,
    InputError,
)
from vv_ai.inputs.resolve import ResolvedCommand, ResolvedTarget

HistorySource = Literal["comment", "label", "artifact"]


class NextResolutionError(Exception):
    """`next` コマンドの解決に失敗したことを表す例外。"""


class NextAiDecisionRequired(NextResolutionError):
    """`next` コマンドの解決に AI 判断が必要であることを表す例外。"""


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
    try:
        resolved_command = _resolve_next_from_history(history, target, is_sub_issue)
    except NextAiDecisionRequired:
        return command
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
        timeline_events = github_client.list_issue_timeline_events(
            repository_full_name,
            number,
        )
    except Exception as exc:
        raise NextResolutionError("timeline 履歴の取得に失敗しました") from exc

    current_index = _resolve_current_history_index(command, timeline_events)
    history: list[NextHistoryEntry] = []
    for index, timeline_event in enumerate(timeline_events):
        if current_index is not None and index >= current_index:
            continue
        entry = _build_github_history_entry(target, config, timeline_event)
        if entry is not None:
            history.append(entry)
    return history


def _resolve_current_history_index(
    command: ResolvedCommand,
    timeline_events: list[GitHubIssueTimelineEvent],
) -> int | None:
    if command.comment_id is not None:
        return _resolve_current_comment_index(command, timeline_events)
    if command.trigger_label_name is not None:
        return _resolve_current_label_index(command, timeline_events)
    return None


def _resolve_current_comment_index(
    command: ResolvedCommand,
    timeline_events: list[GitHubIssueTimelineEvent],
) -> int:
    for index, timeline_event in enumerate(timeline_events):
        if (
            timeline_event.event == "commented"
            and timeline_event.comment_database_id == command.comment_id
        ):
            return index
    raise NextResolutionError("現在処理中のコメントが履歴内に見つかりません")


def _resolve_current_label_index(
    command: ResolvedCommand,
    timeline_events: list[GitHubIssueTimelineEvent],
) -> int:
    if command.trigger_label_name is None:
        raise NextResolutionError("現在処理中のラベル名がありません")
    if command.actor is None:
        raise NextResolutionError("現在処理中のラベル actor がありません")
    if command.trigger_event_created_at is None:
        raise NextResolutionError("現在処理中のラベル event 時刻がありません")

    current_indexes = [
        index
        for index, timeline_event in enumerate(timeline_events)
        if timeline_event.event == "labeled"
        and timeline_event.label_name == command.trigger_label_name
        and timeline_event.actor.login == command.actor
        and timeline_event.created_at == command.trigger_event_created_at
    ]
    if len(current_indexes) == 0:
        raise NextResolutionError("現在処理中のラベル event が履歴内に見つかりません")
    if len(current_indexes) > 1:
        raise NextResolutionError("現在処理中のラベル event が一意に見つかりません")
    return current_indexes[0]


def _build_github_history_entry(
    target: ResolvedTarget,
    config: VVAIConfig,
    timeline_event: GitHubIssueTimelineEvent,
) -> NextHistoryEntry | None:
    artifact_command = _parse_artifact_history_command(target, timeline_event)
    if artifact_command is not None:
        return NextHistoryEntry(
            command=artifact_command,
            created_at=timeline_event.created_at,
            id=None,
            source="artifact",
        )

    bot_decision_command = _parse_bot_next_decision_history_command(timeline_event)
    if bot_decision_command is not None:
        if _should_ignore_command(target, bot_decision_command):
            return None
        return NextHistoryEntry(
            command=bot_decision_command,
            created_at=timeline_event.created_at,
            id=timeline_event.comment_database_id,
            source="comment",
        )

    if timeline_event.event not in {"commented", "labeled"}:
        return None

    if timeline_event.actor.login not in config.allowed_users:
        return None
    command = _parse_timeline_history_command(timeline_event)
    if command is None:
        return None
    if _should_ignore_command(target, command):
        return None
    return NextHistoryEntry(
        command=command,
        created_at=timeline_event.created_at,
        id=timeline_event.comment_database_id
        if timeline_event.event == "commented"
        else None,
        source="comment" if timeline_event.event == "commented" else "label",
    )


def _parse_artifact_history_command(
    target: ResolvedTarget,
    timeline_event: GitHubIssueTimelineEvent,
) -> CommandName | None:
    if target.kind == "pr":
        return None
    if target.kind != "issue":
        raise NextResolutionError("未対応の target 種別です")
    if timeline_event.event == "sub_issue_added":
        return "breakdown"
    if timeline_event.event != "cross_referenced":
        return None
    if timeline_event.source_kind != "pull_request":
        return None
    if timeline_event.source_repository_full_name != target.repository_full_name:
        return None
    return "implement"


def _parse_bot_next_decision_history_command(
    timeline_event: GitHubIssueTimelineEvent,
) -> CommandName | None:
    if timeline_event.event != "commented":
        return None
    if not timeline_event.actor.login.endswith("[bot]"):
        return None
    if timeline_event.body is None:
        return None
    try:
        return parse_next_decision_history_marker(timeline_event.body)
    except NextDecisionError as exc:
        raise NextResolutionError("AI 判断済み `next` 履歴の解析に失敗しました") from exc


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


def _parse_timeline_history_command(
    timeline_event: GitHubIssueTimelineEvent,
) -> CommandName | None:
    if timeline_event.event == "commented":
        if timeline_event.body is None:
            raise NextResolutionError("commented event の本文がありません")
        return _parse_history_command(timeline_event.body)
    if timeline_event.event == "labeled":
        if timeline_event.label_name is None:
            raise NextResolutionError("labeled event のラベル名がありません")
        return _parse_label_history_command(timeline_event.label_name)
    raise NextResolutionError("未対応の timeline event です")


def _parse_label_history_command(label_name: str) -> CommandName | None:
    try:
        return parse_label_invocation(label_name)
    except InputError:
        return None


def _should_ignore_command(target: ResolvedTarget, command: CommandName) -> bool:
    if command in {"reply", "issue", "sync"}:
        return True
    if target.kind == "issue":
        return command in {"address", "review"}
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
        if is_sub_issue:
            return "implement"
        raise NextAiDecisionRequired(
            "Issue の detail 後の `next` には AI 判断が必要です"
        )
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
        return "address"
    if last_command in {"address", "implement"}:
        return "review"
    raise NextResolutionError("PR の履歴から `next` を解決できません")


def _require_github_target_fields(target: ResolvedTarget) -> tuple[str, int]:
    if target.repository_full_name is None:
        raise NextResolutionError("GitHub target の repository_full_name がありません")
    if target.number is None:
        raise NextResolutionError("GitHub target の number がありません")
    return target.repository_full_name, target.number
