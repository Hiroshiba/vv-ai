"""`RawInput` を実行用コマンドへ正規化する。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.config import ProviderName
from vv_ai.inputs.models import (
    CommandName,
    ControlLabelName,
    EventName,
    RawInput,
    SessionMode,
    TargetType,
)

BackendName = Literal["github", "local"]


class ResolutionError(Exception):
    """入力正規化に失敗したことを表す例外。"""


class ResolvedTarget(BaseModel):
    """後続処理で共通利用する target 表現。"""

    model_config = ConfigDict(extra="forbid")

    backend: BackendName
    kind: TargetType
    canonical_id: str
    local_id: str | None = None
    path: str | None = None
    repository_full_name: str | None = None
    number: int | None = None
    url: str | None = None


class ResolvedCommand(BaseModel):
    """実行可能な形へ確定したコマンド。"""

    model_config = ConfigDict(extra="forbid")

    event_name: EventName
    command: CommandName
    instruction: str | None = None
    target_url: str | None = None
    target_type: TargetType | None = None
    target_number: int | None = None
    has_target: bool
    provider: ProviderName | None = None
    session_mode: SessionMode | None = None
    dry_run: bool = False
    repo: str | None = None
    skip_api_key_check: bool = False
    repository_full_name: str | None = None
    actor: str | None = None
    actor_id: int | None = None
    comment_id: int | None = None
    comment_author: str | None = None
    comment_body: str | None = None
    trigger_label_name: str | None = None
    trigger_event_created_at: str | None = None
    target: ResolvedTarget | None = None


class ResolvedControlLabel(BaseModel):
    """実行可能な形へ確定した制御ラベル入力。"""

    model_config = ConfigDict(extra="forbid")

    event_name: EventName
    control_label_name: ControlLabelName
    target_type: TargetType
    target_number: int
    has_target: bool
    repository_full_name: str
    actor: str
    actor_id: int | None = None
    trigger_label_name: str
    trigger_event_created_at: str
    target: ResolvedTarget | None = None


class ResolvedPullRequestClosed(BaseModel):
    """provider を起動しない Pull Request close 入力。"""

    model_config = ConfigDict(extra="forbid")

    event_name: EventName
    target_type: TargetType
    target_number: int
    has_target: bool
    repository_full_name: str
    actor: str
    actor_id: int | None = None
    pull_request_merged: bool


ResolvedInput = ResolvedCommand | ResolvedControlLabel | ResolvedPullRequestClosed


def resolve_raw_input(raw_input: RawInput) -> ResolvedInput:
    """`RawInput` を後続処理用の実行入力へ変換する。"""
    if raw_input.command is not None and raw_input.control_label_name is not None:
        raise ResolutionError("command と control_label_name は同時に指定できません")
    _validate_event_requirements(raw_input)
    if raw_input.pull_request_merged is not None:
        return _resolve_pull_request_closed_input(raw_input)
    if raw_input.control_label_name is not None:
        return _resolve_control_label_input(raw_input)
    command = raw_input.command or "reply"
    instruction = _resolve_instruction(raw_input.instruction)
    target_url, target_type, target_number, has_target = _resolve_target_fields(raw_input)
    repo = _resolve_repo(command, raw_input)

    target_required_commands: set[CommandName] = {
        "confirm", "reply", "implement", "address", "review", "next",
        "requirements", "arch", "detail", "breakdown", "sync",
    }
    if command in target_required_commands and not has_target:
        raise ResolutionError(f"`{command}` コマンドには target 指定が必要です")
    if command == "sync" and target_type == "issue":
        raise ResolutionError("`sync` コマンドは PR 専用です")

    return ResolvedCommand(
        event_name=raw_input.event_name,
        command=command,
        instruction=instruction,
        target_url=target_url,
        target_type=target_type,
        target_number=target_number,
        has_target=has_target,
        provider=raw_input.provider,
        session_mode=raw_input.session_mode,
        dry_run=raw_input.dry_run,
        repo=repo,
        skip_api_key_check=raw_input.skip_api_key_check,
        repository_full_name=raw_input.repository_full_name,
        actor=raw_input.actor,
        actor_id=raw_input.actor_id,
        comment_id=raw_input.comment_id,
        comment_author=raw_input.comment_author,
        comment_body=raw_input.comment_body,
        trigger_label_name=raw_input.trigger_label_name,
        trigger_event_created_at=raw_input.trigger_event_created_at,
    )


def _resolve_control_label_input(raw_input: RawInput) -> ResolvedControlLabel:
    """`RawInput` を制御ラベル入力へ変換する。"""
    if raw_input.control_label_name is None:
        raise ResolutionError("control_label_name がありません")
    if raw_input.event_name not in {"issues", "pull_request"}:
        raise ResolutionError("制御ラベルは labeled event でのみ使用できます")

    required_fields = {
        "repository_full_name": raw_input.repository_full_name,
        "actor": raw_input.actor,
        "target_type": raw_input.target_type,
        "target_number": raw_input.target_number,
        "trigger_label_name": raw_input.trigger_label_name,
        "trigger_event_created_at": raw_input.trigger_event_created_at,
    }
    _raise_for_missing_fields(raw_input.event_name, required_fields)

    if raw_input.target_type is None:
        raise ResolutionError("制御ラベル入力に target_type がありません")
    if raw_input.target_number is None:
        raise ResolutionError("制御ラベル入力に target_number がありません")
    if raw_input.repository_full_name is None:
        raise ResolutionError("制御ラベル入力に repository_full_name がありません")
    if raw_input.actor is None:
        raise ResolutionError("制御ラベル入力に actor がありません")
    if raw_input.trigger_label_name is None:
        raise ResolutionError("制御ラベル入力に trigger_label_name がありません")
    if raw_input.trigger_event_created_at is None:
        raise ResolutionError("制御ラベル入力に trigger_event_created_at がありません")
    if raw_input.target_number <= 0:
        raise ResolutionError("`target_number` は 1 以上である必要があります")

    return ResolvedControlLabel(
        event_name=raw_input.event_name,
        control_label_name=raw_input.control_label_name,
        target_type=raw_input.target_type,
        target_number=raw_input.target_number,
        has_target=True,
        repository_full_name=raw_input.repository_full_name,
        actor=raw_input.actor,
        actor_id=raw_input.actor_id,
        trigger_label_name=raw_input.trigger_label_name,
        trigger_event_created_at=raw_input.trigger_event_created_at,
    )


def _resolve_pull_request_closed_input(
    raw_input: RawInput,
) -> ResolvedPullRequestClosed:
    """`RawInput` を Pull Request close 入力へ変換する。"""
    if raw_input.event_name != "pull_request":
        raise ResolutionError("Pull Request close 入力は pull_request event でのみ使用できます")
    required_fields = {
        "repository_full_name": raw_input.repository_full_name,
        "actor": raw_input.actor,
        "target_type": raw_input.target_type,
        "target_number": raw_input.target_number,
        "pull_request_merged": raw_input.pull_request_merged,
    }
    _raise_for_missing_fields(raw_input.event_name, required_fields)
    if raw_input.target_type != "pr":
        raise ResolutionError("Pull Request close 入力の target_type は pr である必要があります")
    if raw_input.target_number is None:
        raise ResolutionError("Pull Request close 入力に target_number がありません")
    if raw_input.repository_full_name is None:
        raise ResolutionError("Pull Request close 入力に repository_full_name がありません")
    if raw_input.actor is None:
        raise ResolutionError("Pull Request close 入力に actor がありません")
    if raw_input.pull_request_merged is None:
        raise ResolutionError("Pull Request close 入力に merged がありません")
    if raw_input.target_number <= 0:
        raise ResolutionError("`target_number` は 1 以上である必要があります")
    return ResolvedPullRequestClosed(
        event_name=raw_input.event_name,
        target_type=raw_input.target_type,
        target_number=raw_input.target_number,
        has_target=True,
        repository_full_name=raw_input.repository_full_name,
        actor=raw_input.actor,
        actor_id=raw_input.actor_id,
        pull_request_merged=raw_input.pull_request_merged,
    )


def _validate_event_requirements(raw_input: RawInput) -> None:
    """event ごとの最低限の必須項目を検証する。"""
    if raw_input.event_name == "issue_comment":
        required_fields = {
            "repository_full_name": raw_input.repository_full_name,
            "actor": raw_input.actor,
            "comment_id": raw_input.comment_id,
            "comment_author": raw_input.comment_author,
            "comment_body": raw_input.comment_body,
        }
        _raise_for_missing_fields(raw_input.event_name, required_fields)
        return

    if raw_input.event_name == "workflow_dispatch":
        required_fields = {
            "repository_full_name": raw_input.repository_full_name,
            "actor": raw_input.actor,
        }
        _raise_for_missing_fields(raw_input.event_name, required_fields)
        return

    if raw_input.event_name == "pull_request" and raw_input.pull_request_merged is not None:
        required_fields = {
            "repository_full_name": raw_input.repository_full_name,
            "actor": raw_input.actor,
            "target_type": raw_input.target_type,
            "target_number": raw_input.target_number,
            "pull_request_merged": raw_input.pull_request_merged,
        }
        _raise_for_missing_fields(raw_input.event_name, required_fields)
        return

    if raw_input.event_name in {"issues", "pull_request"}:
        required_fields = {
            "repository_full_name": raw_input.repository_full_name,
            "actor": raw_input.actor,
            "target_type": raw_input.target_type,
            "target_number": raw_input.target_number,
            "trigger_label_name": raw_input.trigger_label_name,
            "trigger_event_created_at": raw_input.trigger_event_created_at,
        }
        _raise_for_missing_fields(raw_input.event_name, required_fields)


def _raise_for_missing_fields(
    event_name: EventName,
    required_fields: dict[str, object | None],
) -> None:
    """欠落フィールドがあれば例外を送出する。"""
    missing_fields = [
        field_name
        for field_name, value in required_fields.items()
        if value is None or value == ""
    ]
    if missing_fields:
        joined_fields = ", ".join(missing_fields)
        raise ResolutionError(
            f"`{event_name}` 入力に必須の項目が不足しています: {joined_fields}"
        )


def _resolve_instruction(instruction: str | None) -> str | None:
    """instruction を正規化する。"""
    if instruction is None:
        return None
    stripped = instruction.strip()
    if stripped == "":
        raise ResolutionError("`instruction` は空文字にできません")
    return stripped


def _resolve_target_fields(
    raw_input: RawInput,
) -> tuple[str | None, TargetType | None, int | None, bool]:
    """target 指定を正規化する。"""
    if raw_input.target_url is not None:
        return raw_input.target_url, None, None, True

    target_type = raw_input.target_type
    target_number = raw_input.target_number
    if target_type is None and target_number is None:
        return None, None, None, False
    if target_type is None or target_number is None:
        raise ResolutionError(
            "`target_type` と `target_number` は両方そろえて指定する必要があります"
        )
    if target_number <= 0:
        raise ResolutionError("`target_number` は 1 以上である必要があります")
    return None, target_type, target_number, True


def _resolve_repo(command: CommandName, raw_input: RawInput) -> str | None:
    """Issue 作成先の repository を確定する。"""
    if command != "issue":
        return raw_input.repo
    if raw_input.repo is not None:
        if raw_input.repo.strip() == "":
            raise ResolutionError("`repo` は空文字にできません")
        return raw_input.repo
    return raw_input.repository_full_name
