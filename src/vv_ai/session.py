"""session key / lane / 復元方針の解決。"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.artifact_crypto import ArtifactCryptoError, resolve_age_secret_key
from vv_ai.github import GitHubClientError, build_github_client
from vv_ai.input import SessionMode
from vv_ai.provider import ResolvedProvider
from vv_ai.resolve import BackendName, ResolvedCommand

if TYPE_CHECKING:
    from vv_ai.session_artifact import RestoredSessionArtifact

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
    restored_artifact_dir: str | None = None
    restored_provider_session_path: str | None = None


def resolve_session(
    repo_root: Path,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
    env: Mapping[str, str],
) -> ResolvedSession:
    """command / target / provider から session を確定する。"""
    from vv_ai.session_store import (
        SessionStoreError,
        build_session_manifest_path,
        load_latest_session_manifest,
    )
    from vv_ai.session_artifact import (
        SessionArtifactError,
        build_session_artifact_prefix,
        restore_downloaded_session_artifact,
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
        restore_manifest, restored_artifact = _resolve_restore_state(
            repo_root=repo_root,
            workflow_id=workflow_id,
            resolved_command=resolved_command,
            key=key,
            mode=mode,
            resolved_provider=resolved_provider,
            env=env,
            load_latest_session_manifest=load_latest_session_manifest,
            build_session_artifact_prefix=build_session_artifact_prefix,
            restore_downloaded_session_artifact=restore_downloaded_session_artifact,
        )
        save_manifest_path = build_session_manifest_path(
            repo_root,
            workflow_id,
            key,
        )
    except (
        ArtifactCryptoError,
        GitHubClientError,
        SessionArtifactError,
        SessionStoreError,
    ) as exc:
        raise SessionResolutionError(str(exc)) from exc

    return ResolvedSession(
        mode=mode,
        lane=lane,
        key=key,
        restore_strategy=mode,
        restore_manifest=restore_manifest,
        save_manifest_path=str(save_manifest_path),
        state_ref=restore_manifest.state_ref if restore_manifest is not None else None,
        restored_artifact_dir=(
            restored_artifact.restored_dir if restored_artifact is not None else None
        ),
        restored_provider_session_path=(
            restored_artifact.provider_session_path
            if restored_artifact is not None
            else None
        ),
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


def _resolve_restore_state(
    *,
    repo_root: Path,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    key: SessionKey,
    mode: SessionMode,
    resolved_provider: ResolvedProvider,
    env: Mapping[str, str],
    load_latest_session_manifest,
    build_session_artifact_prefix,
    restore_downloaded_session_artifact,
) -> tuple[SavedSessionManifest | None, RestoredSessionArtifact | None]:
    """mode と provider 能力に応じて復元対象を確定する。"""
    if mode == "new":
        return None, None

    if not resolved_provider.spec.supports_session_resume:
        raise SessionResolutionError(
            f"`{resolved_provider.name}` は session 継続に対応していません"
        )
    if mode == "compact" and not resolved_provider.spec.supports_compact:
        raise SessionResolutionError(
            f"`{resolved_provider.name}` は compact 継続に対応していません"
        )

    manifest = load_latest_session_manifest(repo_root, key)
    if manifest is not None:
        _validate_restore_manifest(mode, manifest)
        return manifest, None
    if key.backend == "local":
        raise SessionResolutionError(
            f"`{mode}` 用の保存済み session が見つかりません: {key.canonical_key}"
        )

    repository_full_name = _resolve_restore_repository_full_name(resolved_command)
    artifact_prefix = build_session_artifact_prefix(key)
    github_client = build_github_client()
    latest_artifact = github_client.find_latest_repository_artifact_by_prefix(
        repository_full_name,
        artifact_prefix,
    )
    if latest_artifact is None:
        raise SessionResolutionError(
            f"`{mode}` 用の保存済み session artifact が見つかりません: "
            f"{key.canonical_key}"
        )
    age_secret_key = resolve_age_secret_key(env)
    with tempfile.TemporaryDirectory(prefix="vv-ai-session-restore-") as temp_root:
        download_path = Path(temp_root) / f"{latest_artifact.name}.zip"
        github_client.download_repository_artifact(
            repository_full_name,
            latest_artifact.id,
            download_path,
        )
        restored_artifact = restore_downloaded_session_artifact(
            repo_root,
            workflow_id,
            latest_artifact.name,
            download_path,
            age_secret_key,
        )
    manifest = _build_manifest_from_restored_artifact(restored_artifact)
    _validate_restore_manifest(mode, manifest)
    return manifest, restored_artifact


def _validate_restore_manifest(
    mode: SessionMode,
    manifest: SavedSessionManifest,
) -> None:
    """復元に必要な状態が揃っているか確認する。"""
    if manifest.state_ref.provider_session_id is None:
        raise SessionResolutionError(
            f"`{mode}` に必要な `provider_session_id` が保存されていません: "
            f"{manifest.workflow_id}"
        )


def _resolve_restore_repository_full_name(
    resolved_command: ResolvedCommand,
) -> str:
    """GitHub artifact 検索に使う repository 名を返す。"""
    if resolved_command.target is not None:
        repository_full_name = resolved_command.target.repository_full_name
    else:
        repository_full_name = resolved_command.repo or resolved_command.repository_full_name
    if repository_full_name is None:
        raise SessionResolutionError("GitHub artifact 検索に必要な repository がありません")
    return repository_full_name


def _build_manifest_from_restored_artifact(
    restored_artifact: RestoredSessionArtifact,
) -> SavedSessionManifest:
    """復元済み session artifact から manifest 相当を構築する。"""
    meta = restored_artifact.meta
    return SavedSessionManifest(
        workflow_id=meta.workflow_id,
        saved_at=meta.saved_at,
        session_key=meta.session_key,
        provider=meta.provider,
        lane=meta.lane,
        backend=meta.backend,
        target_key=meta.target_key,
        state_ref=SessionStateRef(
            provider_session_id=meta.provider_session_id,
            artifact_hint=restored_artifact.artifact_path,
        ),
    )
