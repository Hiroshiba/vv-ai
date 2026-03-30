"""`RawInput` を実行用コマンドへ正規化する。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.input import CommandName, EventName, RawInput, SessionMode, TargetType
from vv_ai.config import ProviderName

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
    comment_id: int | None = None
    comment_author: str | None = None
    comment_body: str | None = None
    target: ResolvedTarget | None = None


def resolve_raw_input(raw_input: RawInput) -> ResolvedCommand:
    """`RawInput` を後続処理用の `ResolvedCommand` に変換する。"""
    _validate_event_requirements(raw_input)
    command = raw_input.command or "reply"
    instruction = _normalize_instruction(raw_input.instruction)
    target_url, target_type, target_number, has_target = _resolve_target_fields(raw_input)
    repo = _resolve_repo(command, raw_input)

    if command in {"reply", "plan", "implement", "review"} and not has_target:
        raise ResolutionError(f"`{command}` コマンドには target 指定が必要です")
    if command in {"reply", "issue"} and instruction is None:
        raise ResolutionError(f"`{command}` コマンドには instruction が必要です")

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
        comment_id=raw_input.comment_id,
        comment_author=raw_input.comment_author,
        comment_body=raw_input.comment_body,
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


def _normalize_instruction(instruction: str | None) -> str | None:
    """空文字 instruction を未指定として扱う。"""
    if instruction is None:
        return None
    stripped = instruction.strip()
    return stripped or None


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
    """Issue 作成時の対象 repository を確定する。"""
    if command != "issue":
        return raw_input.repo
    return raw_input.repo or raw_input.repository_full_name
