"""session key / lane / 復元方針を解決する。"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from vv_ai.artifacts.crypto import ArtifactCryptoError, resolve_age_secret_key
from vv_ai.backends.github.client import build_github_client
from vv_ai.backends.github.models import GitHubClientError
from vv_ai.inputs.models import SessionMode
from vv_ai.inputs.resolve import BackendName, ResolvedCommand
from vv_ai.providers.selection import ResolvedProvider
from vv_ai.sessions.models import (
    ResolvedSession,
    RestoreStrategy,
    SavedSessionManifest,
    SessionKey,
    SessionLane,
    SessionStateRef,
)

if TYPE_CHECKING:
    from vv_ai.backends.github.client import GitHubClient
    from vv_ai.artifacts.session import RestoredSessionArtifact

class SessionResolutionError(Exception):
    """session 解決に失敗したことを表す例外。"""


def resolve_session(
    repo_root: Path,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
    env: Mapping[str, str],
) -> ResolvedSession:
    """command / target / provider から session を確定する。"""
    from vv_ai.artifacts.store import (
        SessionStoreError,
        build_session_manifest_path,
        load_latest_session_manifest,
    )
    from vv_ai.artifacts.session import (
        SessionArtifactError,
        build_session_artifact_prefix,
        cleanup_restored_session_artifact,
        is_restored_session_artifact_resumable,
        restore_downloaded_session_artifact,
    )

    lane = _resolve_lane(resolved_command)
    backend, target_key = _resolve_scope(resolved_command)
    requested_mode: SessionMode = resolved_command.session_mode or "inherit_or_new"
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
        restore_manifest, restored_artifact, restore_strategy = _resolve_restore_state(
            repo_root=repo_root,
            workflow_id=workflow_id,
            resolved_command=resolved_command,
            key=key,
            requested_mode=requested_mode,
            resolved_provider=resolved_provider,
            env=env,
            load_latest_session_manifest=load_latest_session_manifest,
            build_session_artifact_prefix=build_session_artifact_prefix,
            restore_downloaded_session_artifact=restore_downloaded_session_artifact,
            build_github_client_func=build_github_client,
            is_restored_session_artifact_resumable=(
                is_restored_session_artifact_resumable
            ),
            cleanup_restored_session_artifact=cleanup_restored_session_artifact,
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
        requested_mode=requested_mode,
        lane=lane,
        key=key,
        restore_strategy=restore_strategy,
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
        allow_edits_notice_posted=(
            restored_artifact.meta.allow_edits_notice_posted
            if restored_artifact is not None
            else False
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
    requested_mode: SessionMode,
    resolved_provider: ResolvedProvider,
    env: Mapping[str, str],
    load_latest_session_manifest: Callable[
        [Path, SessionKey],
        SavedSessionManifest | None,
    ],
    build_session_artifact_prefix: Callable[[SessionKey], str],
    restore_downloaded_session_artifact: Callable[
        [Path, str, str, Path, str],
        "RestoredSessionArtifact",
    ],
    build_github_client_func: Callable[[], "GitHubClient"],
    is_restored_session_artifact_resumable: Callable[
        ["RestoredSessionArtifact"],
        bool,
    ],
    cleanup_restored_session_artifact: Callable[
        ["RestoredSessionArtifact"],
        None,
    ],
) -> tuple[
    SavedSessionManifest | None,
    RestoredSessionArtifact | None,
    RestoreStrategy,
]:
    """要求モードと provider 能力に応じて復元対象と実際の復元戦略を確定する。"""
    if requested_mode == "new":
        return None, None, "new"

    if not resolved_provider.spec.supports_session_resume:
        raise SessionResolutionError(
            f"`{resolved_provider.name}` は session 継続に対応していません"
        )
    if requested_mode == "compact" and not resolved_provider.spec.supports_compact:
        raise SessionResolutionError(
            f"`{resolved_provider.name}` は compact 継続に対応していません"
        )

    strict_missing = requested_mode in ("inherit", "compact")
    restore_strategy: RestoreStrategy = (
        "compact" if requested_mode == "compact" else "inherit"
    )

    manifest = load_latest_session_manifest(repo_root, key)
    if manifest is not None:
        _validate_restore_manifest(requested_mode, manifest)
        return manifest, None, restore_strategy

    if key.backend == "local":
        if strict_missing:
            raise SessionResolutionError(
                f"`{requested_mode}` 用の保存済み session が見つかりません: "
                f"{key.canonical_key}"
            )
        return None, None, "new"

    repository_full_name = _resolve_restore_repository_full_name(resolved_command)
    artifact_prefix = build_session_artifact_prefix(key)
    github_client = build_github_client_func()
    artifacts = github_client.list_repository_artifacts_by_prefix(
        repository_full_name,
        artifact_prefix,
    )
    if len(artifacts) == 0:
        if strict_missing:
            raise SessionResolutionError(
                f"`{requested_mode}` 用の保存済み session artifact が見つかりません: "
                f"{key.canonical_key}"
            )
        return None, None, "new"

    age_secret_key = resolve_age_secret_key(env)
    for artifact in artifacts:
        with tempfile.TemporaryDirectory(prefix="vv-ai-session-restore-") as temp_root:
            download_path = Path(temp_root) / f"{artifact.name}.zip"
            github_client.download_repository_artifact(
                repository_full_name,
                artifact.id,
                download_path,
            )
            restored_artifact = restore_downloaded_session_artifact(
                repo_root,
                workflow_id,
                artifact.name,
                download_path,
                age_secret_key,
            )
        if is_restored_session_artifact_resumable(restored_artifact):
            manifest = _build_manifest_from_restored_artifact(restored_artifact)
            _validate_restore_manifest(requested_mode, manifest)
            return manifest, restored_artifact, restore_strategy
        cleanup_restored_session_artifact(restored_artifact)

    if strict_missing:
        raise SessionResolutionError(
            f"`{requested_mode}` 用の保存済み session artifact が見つかりません: "
            f"{key.canonical_key}"
        )
    return None, None, "new"


def _validate_restore_manifest(
    requested_mode: SessionMode,
    manifest: SavedSessionManifest,
) -> None:
    """復元に必要な状態が揃っているか確認する。"""
    if manifest.state_ref.provider_session_id is None:
        raise SessionResolutionError(
            f"`{requested_mode}` に必要な `provider_session_id` が保存されていません: "
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
            target_context_state=meta.target_context_state,
        ),
    )
