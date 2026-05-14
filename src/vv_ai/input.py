"""CLI と GitHub event payload を受ける入力モデル。"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from vv_ai.config import ProviderName

CommandName = Literal[
    "confirm", "reply", "implement", "review", "issue", "next",
    "requirements", "arch", "detail", "breakdown",
]
EventName = Literal["issue_comment", "workflow_dispatch", "local"]
SessionMode = Literal["inherit", "inherit_or_new", "compact", "new"]
TargetType = Literal["issue", "pr"]

_COMMAND_NAMES: set[str] = {
    "confirm", "reply", "implement", "review", "issue", "next",
    "requirements", "arch", "detail", "breakdown",
}


class InputError(Exception):
    """入力の解釈に失敗したことを表す例外。"""


class CLIInput(BaseModel):
    """CLI から受ける生入力。"""

    model_config = ConfigDict(extra="forbid")

    event: EventName = "local"
    event_file: Path | None = None
    command: CommandName | None = None
    instruction: str | None = None
    target_url: str | None = None
    target_type: TargetType | None = None
    target_number: int | None = None
    provider: ProviderName | None = None
    session_mode: SessionMode | None = None
    dry_run: bool | None = None
    repo: str | None = None
    skip_api_key_check: bool = False


class GitHubRepository(BaseModel):
    """GitHub repository の最小表現。"""

    model_config = ConfigDict(extra="ignore")

    full_name: str


class GitHubUser(BaseModel):
    """GitHub user の最小表現。"""

    model_config = ConfigDict(extra="ignore")

    login: str


class IssueCommentTarget(BaseModel):
    """Issue comment event の対象。"""

    model_config = ConfigDict(extra="ignore")

    number: int
    pull_request: dict[str, Any] | None = None


class IssueCommentBody(BaseModel):
    """Issue comment の本文情報。"""

    model_config = ConfigDict(extra="ignore")

    id: int
    body: str
    user: GitHubUser


class IssueCommentEvent(BaseModel):
    """`issue_comment` event payload の必要最小限。"""

    model_config = ConfigDict(extra="ignore")

    action: str | None = None
    comment: IssueCommentBody
    issue: IssueCommentTarget
    repository: GitHubRepository
    sender: GitHubUser


class WorkflowDispatchEvent(BaseModel):
    """`workflow_dispatch` event payload の必要最小限。"""

    model_config = ConfigDict(extra="ignore")

    inputs: dict[str, Any] | None = None
    repository: GitHubRepository
    sender: GitHubUser


class RawInput(BaseModel):
    """後続の正規化処理に渡す共通の生入力。"""

    model_config = ConfigDict(extra="forbid")

    event_name: EventName
    command: CommandName | None = None
    instruction: str | None = None
    target_url: str | None = None
    target_type: TargetType | None = None
    target_number: int | None = None
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


class CommentInvocation(BaseModel):
    """`@vv-ai` コメント本文から抽出した生入力。"""

    model_config = ConfigDict(extra="forbid")

    command: CommandName = "reply"
    instruction: str | None = None
    provider: ProviderName | None = None
    session_mode: SessionMode | None = None
    dry_run: bool = False
    repo: str | None = None


def build_raw_input_from_cli(cli_input: CLIInput) -> RawInput:
    """CLI 入力から `RawInput` を構築する。"""
    if cli_input.event_file is not None:
        _ensure_event_file_mode(cli_input)
        return build_raw_input_from_event_file(
            event_name=_resolve_event_name_for_event_file(cli_input.event, cli_input.event_file),
            event_file=cli_input.event_file,
        )

    try:
        return RawInput(
            event_name=cli_input.event,
            command=cli_input.command,
            instruction=cli_input.instruction,
            target_url=cli_input.target_url,
            target_type=cli_input.target_type,
            target_number=cli_input.target_number,
            provider=cli_input.provider,
            session_mode=cli_input.session_mode,
            dry_run=bool(cli_input.dry_run),
            repo=cli_input.repo,
            skip_api_key_check=cli_input.skip_api_key_check,
        )
    except ValidationError as exc:
        raise InputError("CLI 引数の値が不正です") from exc


def build_raw_input_from_event_file(event_name: EventName, event_file: Path) -> RawInput:
    """event payload JSON から `RawInput` を構築する。"""
    payload = _load_event_payload(event_file)
    try:
        if event_name == "issue_comment":
            return build_raw_input_from_issue_comment_event(
                IssueCommentEvent.model_validate(payload)
            )
        if event_name == "workflow_dispatch":
            return build_raw_input_from_workflow_dispatch_event(
                WorkflowDispatchEvent.model_validate(payload)
            )
        if event_name == "local":
            raise InputError("`local` event は event-file では再現できません")
    except ValidationError as exc:
        raise InputError("event payload の値が不正です") from exc
    raise AssertionError(f"未対応の event です: {event_name}")


def build_raw_input_from_issue_comment_event(event: IssueCommentEvent) -> RawInput:
    """`issue_comment` payload から `RawInput` を構築する。"""
    invocation = parse_comment_invocation(event.comment.body)
    return RawInput(
        event_name="issue_comment",
        command=invocation.command,
        instruction=invocation.instruction,
        target_type="pr" if event.issue.pull_request is not None else "issue",
        target_number=event.issue.number,
        provider=invocation.provider,
        session_mode=invocation.session_mode,
        dry_run=invocation.dry_run,
        repo=invocation.repo,
        repository_full_name=event.repository.full_name,
        actor=event.sender.login,
        comment_id=event.comment.id,
        comment_author=event.comment.user.login,
        comment_body=event.comment.body,
    )


def build_raw_input_from_workflow_dispatch_event(
    event: WorkflowDispatchEvent,
) -> RawInput:
    """`workflow_dispatch` payload から `RawInput` を構築する。"""
    inputs = event.inputs or {}
    return RawInput(
        event_name="workflow_dispatch",
        command=_coerce_optional_literal(inputs.get("command"), "command"),
        instruction=_coerce_optional_str(inputs.get("instruction")),
        target_url=_coerce_optional_str(inputs.get("target_url")),
        target_type=_coerce_optional_literal(inputs.get("target_type"), "target_type"),
        target_number=_coerce_optional_int(inputs.get("target_number"), "target_number"),
        provider=_coerce_optional_literal(inputs.get("provider"), "provider"),
        session_mode=_coerce_optional_literal(inputs.get("session_mode"), "session_mode"),
        dry_run=_coerce_optional_bool(inputs.get("dry_run"), "dry_run") or False,
        repo=_coerce_optional_str(inputs.get("repo")),
        repository_full_name=event.repository.full_name,
        actor=event.sender.login,
    )


def parse_comment_invocation(comment_body: str) -> CommentInvocation:
    """`@vv-ai ...` コメント本文を字句的に分解する。"""
    stripped = comment_body.lstrip()
    if not stripped.startswith("@vv-ai"):
        raise InputError("`issue_comment` の本文は `@vv-ai` で始まる必要があります")

    suffix = stripped[len("@vv-ai") :]
    if suffix and not suffix[0].isspace():
        raise InputError("`issue_comment` の本文は `@vv-ai` で始まる必要があります")

    tail = suffix.strip()
    if not tail:
        return CommentInvocation()

    try:
        tokens = shlex.split(tail)
    except ValueError as exc:
        raise InputError(f"コメント本文の分解に失敗しました: {exc}") from exc

    command: CommandName = "reply"
    index = 0
    if tokens and tokens[0] in _COMMAND_NAMES:
        command = tokens[0]  # type: ignore[assignment]
        index = 1

    provider: ProviderName | None = None
    session_mode: SessionMode | None = None
    dry_run = False
    repo: str | None = None
    instruction_tokens: list[str] = []

    while index < len(tokens):
        token = tokens[index]
        if token == "--dry-run":
            dry_run = True
            index += 1
            continue
        if token == "--provider":
            provider = _expect_option_value(tokens, index, "--provider")
            index += 2
            continue
        if token == "--session_mode":
            session_mode = _expect_option_value(tokens, index, "--session_mode")
            index += 2
            continue
        if token == "--repo":
            repo = _expect_option_value(tokens, index, "--repo")
            index += 2
            continue
        if token.startswith("--"):
            raise InputError(f"未対応のオプションです: {token}")
        instruction_tokens = tokens[index:]
        break

    instruction = " ".join(instruction_tokens) if instruction_tokens else None
    try:
        return CommentInvocation.model_validate(
            {
                "command": command,
                "instruction": instruction,
                "provider": provider,
                "session_mode": session_mode,
                "dry_run": dry_run,
                "repo": repo,
            }
        )
    except ValidationError as exc:
        raise InputError("コメント本文のオプション値が不正です") from exc


def _ensure_event_file_mode(cli_input: CLIInput) -> None:
    """event-file と直接指定引数の併用を防ぐ。"""
    direct_fields = {
        "command": cli_input.command,
        "instruction": cli_input.instruction,
        "target_url": cli_input.target_url,
        "target_type": cli_input.target_type,
        "target_number": cli_input.target_number,
        "provider": cli_input.provider,
        "session_mode": cli_input.session_mode,
        "dry_run": cli_input.dry_run,
        "repo": cli_input.repo,
    }
    used_direct_fields = [
        name for name, value in direct_fields.items() if value not in (None, False)
    ]
    if used_direct_fields:
        joined_fields = ", ".join(used_direct_fields)
        raise InputError(
            "`--event-file` と直接指定引数は併用できません: "
            f"{joined_fields}"
        )


def _resolve_event_name_for_event_file(
    event_name: EventName,
    event_file: Path,
) -> EventName:
    """event-file 向けに event 名を決定する。"""
    if event_name != "local":
        return event_name

    payload = _load_event_payload(event_file)
    if _looks_like_issue_comment_event(payload):
        return "issue_comment"
    if _looks_like_workflow_dispatch_event(payload):
        return "workflow_dispatch"
    raise InputError(
        "`--event-file` から event 種別を判定できませんでした。"
        " `--event issue_comment` または `--event workflow_dispatch` を指定してください"
    )


def _load_event_payload(event_file: Path) -> dict[str, Any]:
    """event payload JSON を読み込む。"""
    try:
        content = event_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"`{event_file}` の読み込みに失敗しました") from exc

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InputError(f"`{event_file}` を JSON として解釈できませんでした") from exc

    if not isinstance(payload, dict):
        raise InputError(f"`{event_file}` は JSON object である必要があります")
    return payload


def _looks_like_issue_comment_event(payload: dict[str, Any]) -> bool:
    """`issue_comment` らしい payload かを判定する。"""
    return (
        isinstance(payload.get("comment"), dict)
        and isinstance(payload.get("issue"), dict)
        and isinstance(payload.get("repository"), dict)
    )


def _looks_like_workflow_dispatch_event(payload: dict[str, Any]) -> bool:
    """`workflow_dispatch` らしい payload かを判定する。"""
    return (
        "inputs" in payload
        and isinstance(payload.get("repository"), dict)
        and isinstance(payload.get("sender"), dict)
    )


def _expect_option_value(tokens: list[str], index: int, option_name: str) -> str:
    """オプション値を取得する。"""
    try:
        return tokens[index + 1]
    except IndexError as exc:
        raise InputError(f"{option_name} には値が必要です") from exc


def _coerce_optional_str(value: Any) -> str | None:
    """空文字を除いて文字列として扱う。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputError("文字列項目の形式が不正です")
    stripped = value.strip()
    return stripped or None


def _coerce_optional_int(value: Any, field_name: str) -> int | None:
    """整数項目を解釈する。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise InputError(f"`{field_name}` は整数である必要があります")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError as exc:
            raise InputError(f"`{field_name}` は整数である必要があります") from exc
    raise InputError(f"`{field_name}` の形式が不正です")


def _coerce_optional_bool(value: Any, field_name: str) -> bool | None:
    """真偽値項目を解釈する。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if not lowered:
            return None
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise InputError(f"`{field_name}` は true または false である必要があります")


def _coerce_optional_literal(value: Any, field_name: str) -> Any:
    """空文字を除いてリテラル候補をそのまま返す。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputError(f"`{field_name}` の形式が不正です")
    stripped = value.strip()
    return stripped or None
