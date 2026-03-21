"""session key / lane の解決。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.input import SessionMode
from vv_ai.provider import ResolvedProvider
from vv_ai.resolve import BackendName, ResolvedCommand

SessionLane = Literal["main", "review"]


class SessionResolutionError(Exception):
    """session 解決に失敗したことを表す例外。"""


class SessionKey(BaseModel):
    """artifact 検索や保存に使う session の共通キー。"""

    model_config = ConfigDict(extra="forbid")

    backend: BackendName
    target_key: str
    provider: str
    lane: SessionLane
    canonical_key: str


class SessionStateRef(BaseModel):
    """provider 固有 session の参照情報。"""

    model_config = ConfigDict(extra="forbid")

    provider_session_id: str | None = None
    summary_path: str | None = None
    artifact_hint: str | None = None


class ResolvedSession(BaseModel):
    """今回の実行で使う session 設定。"""

    model_config = ConfigDict(extra="forbid")

    mode: SessionMode
    lane: SessionLane
    key: SessionKey
    state_ref: SessionStateRef | None = None


def resolve_session(
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
) -> ResolvedSession:
    """command / target / provider から session を確定する。"""
    lane = _resolve_lane(resolved_command)
    backend, target_key = _resolve_scope(resolved_command)
    key = SessionKey(
        backend=backend,
        target_key=target_key,
        provider=resolved_provider.name,
        lane=lane,
        canonical_key=(
            f"{backend}/{target_key}/{resolved_provider.name}/{lane}"
        ),
    )
    return ResolvedSession(
        mode=resolved_command.session_mode or "inherit",
        lane=lane,
        key=key,
    )


def _resolve_lane(resolved_command: ResolvedCommand) -> SessionLane:
    """コマンドから lane を決める。"""
    if resolved_command.command == "review":
        return "review"
    return "main"


def _resolve_scope(
    resolved_command: ResolvedCommand,
) -> tuple[BackendName, str]:
    """session key 用の backend と target 識別子を返す。"""
    if resolved_command.target is not None:
        return resolved_command.target.backend, resolved_command.target.canonical_id

    if resolved_command.command == "issue" and resolved_command.repo is not None:
        return "github", f"repo:{resolved_command.repo}"

    if resolved_command.command == "issue":
        return "local", "command:issue"

    raise SessionResolutionError("session key を作るための target が見つかりません")
