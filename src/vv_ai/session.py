"""session key / lane / 復元方針の解決。"""

from __future__ import annotations

from typing import Literal

from pathlib import Path

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


class SavedSessionManifest(BaseModel):
    """保存済み session の最小 manifest。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workflow_id: str
    saved_at: str
    session_key: str
    provider: str
    lane: SessionLane
    backend: BackendName
    target_key: str
    state_ref: SessionStateRef


class ResolvedSession(BaseModel):
    """今回の実行で使う session 設定。"""

    model_config = ConfigDict(extra="forbid")

    mode: SessionMode
    lane: SessionLane
    key: SessionKey
    restore_strategy: SessionMode
    restore_manifest: SavedSessionManifest | None = None
    save_manifest_path: str
    state_ref: SessionStateRef | None = None


def resolve_session(
    repo_root: Path,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
) -> ResolvedSession:
    """command / target / provider から session を確定する。"""
    from vv_ai.session_store import (
        SessionStoreError,
        build_session_manifest_path,
        load_latest_session_manifest,
    )

    lane = _resolve_lane(resolved_command)
    backend, target_key = _resolve_scope(resolved_command)
    mode = resolved_command.session_mode or "inherit"
    key = SessionKey(
        backend=backend,
        target_key=target_key,
        provider=resolved_provider.name,
        lane=lane,
        canonical_key=(
            f"{backend}/{target_key}/{resolved_provider.name}/{lane}"
        ),
    )
    try:
        restore_manifest = _resolve_restore_manifest(
            repo_root=repo_root,
            key=key,
            mode=mode,
            resolved_provider=resolved_provider,
            load_latest_session_manifest=load_latest_session_manifest,
        )
        save_manifest_path = build_session_manifest_path(
            repo_root,
            workflow_id,
            key,
        )
    except SessionStoreError as exc:
        raise SessionResolutionError(str(exc)) from exc

    return ResolvedSession(
        mode=mode,
        lane=lane,
        key=key,
        restore_strategy=mode,
        restore_manifest=restore_manifest,
        save_manifest_path=str(save_manifest_path),
        state_ref=restore_manifest.state_ref if restore_manifest is not None else None,
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


def _resolve_restore_manifest(
    *,
    repo_root: Path,
    key: SessionKey,
    mode: SessionMode,
    resolved_provider: ResolvedProvider,
    load_latest_session_manifest,
) -> SavedSessionManifest | None:
    """mode と provider 能力に応じて復元対象を確定する。"""
    if mode == "new":
        return None

    if not resolved_provider.spec.supports_session_resume:
        raise SessionResolutionError(
            f"`{resolved_provider.name}` は session 継続に対応していません"
        )
    if mode == "compact" and not resolved_provider.spec.supports_compact:
        raise SessionResolutionError(
            f"`{resolved_provider.name}` は compact 継続に対応していません"
        )

    manifest = load_latest_session_manifest(repo_root, key)
    if manifest is None:
        raise SessionResolutionError(
            f"`{mode}` 用の保存済み session が見つかりません: {key.canonical_key}"
        )
    if manifest.state_ref.provider_session_id is None:
        raise SessionResolutionError(
            f"`{mode}` に必要な `provider_session_id` が保存されていません: "
            f"{manifest.workflow_id}"
        )
    return manifest
