"""session artifact の保存形式。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from vv_ai.artifacts.crypto import (
    ArtifactCryptoError,
    decrypt_directory,
    encrypt_directory,
)
from vv_ai.providers.selection import ResolvedProvider
from vv_ai.inputs.resolve import BackendName, ResolvedCommand
from vv_ai.sessions.models import (
    ResolvedSession,
    SessionKey,
    SessionLane,
    SessionStateRef,
    TargetContextState,
)
from vv_ai.artifacts.store import load_latest_session_manifest, save_session_manifest

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class SessionArtifactError(Exception):
    """session artifact の保存に失敗したことを表す例外。"""


class GitSnapshot(BaseModel):
    """保存時点の Git 状態。"""

    model_config = ConfigDict(extra="forbid")

    branch_name: str
    head_sha: str
    git_diff: str
    git_staged_diff: str
    git_status: str
    untracked_files: list[str]


class SessionArtifactMeta(BaseModel):
    """session artifact のメタ情報。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workflow_id: str
    saved_at: str
    session_key: str
    provider: str
    lane: SessionLane
    backend: BackendName
    target_key: str
    target_kind: str | None = None
    repository_full_name: str | None = None
    target_number: int | None = None
    local_target_path: str | None = None
    branch_name: str
    head_sha: str
    allow_edits_notice_posted: bool = False
    provider_session_id: str | None = None
    target_context_state: TargetContextState | None = None


class SavedSessionArtifact(BaseModel):
    """保存済み session artifact の参照情報。"""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str
    artifact_path: str
    manifest_path: str


class RestoredSessionArtifact(BaseModel):
    """復元した session artifact の参照情報。"""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str
    artifact_path: str
    restored_dir: str
    provider_session_path: str | None
    meta: SessionArtifactMeta


def capture_git_snapshot(repo_root: Path) -> GitSnapshot:
    """保存対象の Git 状態を取得する。"""
    branch_name = _run_git_command(
        repo_root,
        "rev-parse",
        "--abbrev-ref",
        "HEAD",
    ).strip()
    if branch_name == "":
        raise SessionArtifactError("現在の Git branch 名を取得できません")

    head_sha = _run_git_command(repo_root, "rev-parse", "HEAD").strip()
    if head_sha == "":
        raise SessionArtifactError("現在の HEAD SHA を取得できません")

    return GitSnapshot(
        branch_name=branch_name,
        head_sha=head_sha,
        git_diff=_run_git_command(repo_root, "diff", "--no-ext-diff"),
        git_staged_diff=_run_git_command(
            repo_root,
            "diff",
            "--staged",
            "--no-ext-diff",
        ),
        git_status=_run_git_command(repo_root, "status", "--porcelain"),
        untracked_files=_list_untracked_files(repo_root),
    )


def build_session_artifact_name(session_key: SessionKey, workflow_id: str) -> str:
    """upload 用にも流用できる一意な artifact 名を返す。"""
    target_name = _sanitize_name(session_key.target_key)
    provider_name = _sanitize_name(session_key.provider)
    lane_name = _sanitize_name(session_key.lane)
    workflow_name = _sanitize_name(workflow_id)
    return (
        f"vv-ai-session__{target_name}__{provider_name}__{lane_name}"
        f"__{workflow_name}"
    )


def build_session_artifact_prefix(session_key: SessionKey) -> str:
    """workflow_id を除いた session artifact prefix を返す。"""
    target_name = _sanitize_name(session_key.target_key)
    provider_name = _sanitize_name(session_key.provider)
    lane_name = _sanitize_name(session_key.lane)
    return f"vv-ai-session__{target_name}__{provider_name}__{lane_name}__"


def build_session_artifact_target_prefix(target_key: str) -> str:
    """target ごとの session artifact prefix を返す。"""
    target_name = _sanitize_name(target_key)
    return f"vv-ai-session__{target_name}__"


def save_session_artifact(
    repo_root: Path,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
    resolved_session: ResolvedSession,
    state_ref: SessionStateRef,
    age_public_key: str,
    *,
    provider_session_path: Path | None = None,
    allow_edits_notice_posted: bool = False,
    saved_at: datetime | None = None,
) -> SavedSessionArtifact:
    """session artifact 本体と manifest を保存する。"""
    snapshot = capture_git_snapshot(repo_root)
    current = _normalize_datetime(saved_at)
    artifact_name = build_session_artifact_name(resolved_session.key, workflow_id)
    sessions_root = repo_root / ".vv-ai" / "artifacts" / workflow_id / "sessions"
    artifact_path = sessions_root / f"{artifact_name}.tar.age"
    temp_artifact_path = sessions_root / f".{artifact_name}.tmp"
    if artifact_path.exists():
        raise SessionArtifactError(f"`{artifact_path}` は既に存在します")
    if temp_artifact_path.exists():
        raise SessionArtifactError(f"`{temp_artifact_path}` が残っています")

    meta = _build_session_artifact_meta(
        workflow_id=workflow_id,
        resolved_command=resolved_command,
        resolved_provider=resolved_provider,
        resolved_session=resolved_session,
        state_ref=state_ref,
        snapshot=snapshot,
        allow_edits_notice_posted=allow_edits_notice_posted,
        saved_at=current,
    )

    try:
        temp_artifact_path.mkdir(parents=True, exist_ok=False)
        _write_text(temp_artifact_path / "meta.json", _dump_json(meta))
        _write_text(temp_artifact_path / "git-diff.patch", snapshot.git_diff)
        _write_text(temp_artifact_path / "git-staged.patch", snapshot.git_staged_diff)
        _write_text(temp_artifact_path / "git-status.txt", snapshot.git_status)
        _copy_untracked_files(repo_root, temp_artifact_path, snapshot.untracked_files)
        _copy_provider_session_dir(provider_session_path, temp_artifact_path)
        encrypt_directory(temp_artifact_path, artifact_path, age_public_key)
    except OSError as exc:
        _cleanup_directory(temp_artifact_path)
        raise SessionArtifactError(
            f"`{artifact_path}` の保存に失敗しました"
        ) from exc
    except shutil.Error as exc:
        _cleanup_directory(temp_artifact_path)
        raise SessionArtifactError(
            f"`{artifact_path}` の保存に失敗しました"
        ) from exc
    except SessionArtifactError:
        _cleanup_directory(temp_artifact_path)
        raise
    except ArtifactCryptoError as exc:
        _cleanup_directory(temp_artifact_path)
        raise SessionArtifactError(str(exc)) from exc
    finally:
        _cleanup_directory(temp_artifact_path)

    manifest_state_ref = state_ref.model_copy(
        update={"artifact_hint": str(artifact_path)}
    )
    try:
        manifest_path = save_session_manifest(
            repo_root,
            workflow_id,
            resolved_session.key,
            manifest_state_ref,
            saved_at=current,
        )
    except Exception as exc:
        _cleanup_file(artifact_path)
        raise SessionArtifactError("session manifest の保存に失敗しました") from exc

    return SavedSessionArtifact(
        artifact_name=artifact_name,
        artifact_path=str(artifact_path),
        manifest_path=str(manifest_path),
    )


def fork_session_artifact(
    source_artifact_path: Path,
    source_session_key: SessionKey,
    new_session_key: SessionKey,
    repo_root: Path,
    workflow_id: str,
) -> None:
    """session artifact を新しい session key に複製して manifest を作成する。"""
    old_manifest = load_latest_session_manifest(repo_root, source_session_key)
    if old_manifest is None:
        raise SessionArtifactError("元の session manifest が見つかりません")

    new_name = build_session_artifact_name(new_session_key, workflow_id)
    new_path = source_artifact_path.parent / f"{new_name}.tar.age"
    if new_path.exists():
        raise SessionArtifactError(f"`{new_path}` は既に存在します")

    try:
        shutil.copy2(source_artifact_path, new_path)
    except OSError as exc:
        raise SessionArtifactError(
            f"`{source_artifact_path}` の複製に失敗しました"
        ) from exc

    new_state_ref = old_manifest.state_ref.model_copy(
        update={"artifact_hint": str(new_path)}
    )
    try:
        save_session_manifest(repo_root, workflow_id, new_session_key, new_state_ref)
    except Exception as exc:
        _cleanup_file(new_path)
        raise SessionArtifactError("session manifest の保存に失敗しました") from exc


def decrypt_session_artifact(
    artifact_path: Path,
    destination_dir: Path,
    age_secret_key: str,
) -> Path:
    """暗号化済み session artifact を復号して返す。"""
    try:
        decrypt_directory(artifact_path, destination_dir, age_secret_key)
        load_session_artifact_meta(destination_dir)
    except ArtifactCryptoError as exc:
        raise SessionArtifactError(str(exc)) from exc
    except SessionArtifactError:
        _cleanup_directory(destination_dir)
        raise
    return destination_dir


def load_session_artifact_meta(artifact_dir: Path) -> SessionArtifactMeta:
    """展開済み session artifact から metadata を読み込む。"""
    meta_path = artifact_dir / "meta.json"
    if not meta_path.is_file():
        raise SessionArtifactError(f"`{meta_path}` が見つかりません")

    try:
        raw_data = json.loads(meta_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SessionArtifactError(f"`{meta_path}` の読み込みに失敗しました") from exc
    except json.JSONDecodeError as exc:
        raise SessionArtifactError(f"`{meta_path}` は JSON として不正です") from exc

    try:
        return SessionArtifactMeta.model_validate(raw_data)
    except ValidationError as exc:
        raise SessionArtifactError(f"`{meta_path}` の値が不正です") from exc


def restore_downloaded_session_artifact(
    repo_root: Path,
    workflow_id: str,
    artifact_name: str,
    downloaded_zip_path: Path,
    age_secret_key: str,
) -> RestoredSessionArtifact:
    """download 済み artifact zip を展開して session を復元する。"""
    restored_root = (
        repo_root
        / ".vv-ai"
        / "artifacts"
        / workflow_id
        / "restored-sessions"
        / artifact_name
    )
    temp_root = restored_root.parent / f".{artifact_name}.tmp"
    if restored_root.exists():
        raise SessionArtifactError(f"`{restored_root}` は既に存在します")
    if temp_root.exists():
        raise SessionArtifactError(f"`{temp_root}` が残っています")

    try:
        temp_root.mkdir(parents=True, exist_ok=False)
        extracted_zip_dir = temp_root / "zip"
        extracted_zip_dir.mkdir()
        _extract_zip_archive(downloaded_zip_path, extracted_zip_dir)
        encrypted_artifact_path = temp_root / f"{artifact_name}.tar.age"
        source_artifact_path = _find_single_encrypted_artifact(extracted_zip_dir)
        shutil.copy2(source_artifact_path, encrypted_artifact_path)
        restored_dir = decrypt_session_artifact(
            encrypted_artifact_path,
            temp_root / "session",
            age_secret_key,
        )
        meta = load_session_artifact_meta(restored_dir)
        provider_session_path = _resolve_provider_session_path(restored_dir)
        temp_root.replace(restored_root)
    except OSError as exc:
        _cleanup_directory(temp_root)
        raise SessionArtifactError(
            f"`{artifact_name}` の復元に失敗しました"
        ) from exc
    except shutil.Error as exc:
        _cleanup_directory(temp_root)
        raise SessionArtifactError(
            f"`{artifact_name}` の復元に失敗しました"
        ) from exc
    except SessionArtifactError:
        _cleanup_directory(temp_root)
        raise

    final_artifact_path = restored_root / f"{artifact_name}.tar.age"
    final_restored_dir = restored_root / "session"
    final_provider_session_path = _resolve_provider_session_path(final_restored_dir)
    return RestoredSessionArtifact(
        artifact_name=artifact_name,
        artifact_path=str(final_artifact_path),
        restored_dir=str(final_restored_dir),
        provider_session_path=(
            str(final_provider_session_path)
            if final_provider_session_path is not None
            else None
        ),
        meta=meta,
    )


def is_restored_session_artifact_resumable(
    restored_artifact: RestoredSessionArtifact,
) -> bool:
    """復元済み session artifact が継続可能かを返す。"""
    return (
        restored_artifact.meta.provider_session_id is not None
        and restored_artifact.provider_session_path is not None
    )


def cleanup_restored_session_artifact(
    restored_artifact: RestoredSessionArtifact,
) -> None:
    """採用しない復元済み session artifact を削除する。"""
    _cleanup_directory(Path(restored_artifact.restored_dir).parent)


def _build_session_artifact_meta(
    *,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
    resolved_session: ResolvedSession,
    state_ref: SessionStateRef,
    snapshot: GitSnapshot,
    allow_edits_notice_posted: bool,
    saved_at: datetime,
) -> SessionArtifactMeta:
    """resolved 情報から artifact metadata を組み立てる。"""
    target = resolved_command.target
    return SessionArtifactMeta(
        workflow_id=workflow_id,
        saved_at=_format_saved_at(saved_at),
        session_key=resolved_session.key.canonical_key,
        provider=resolved_provider.name,
        lane=resolved_session.lane,
        backend=resolved_session.key.backend,
        target_key=resolved_session.key.target_key,
        target_kind=target.kind if target is not None else None,
        repository_full_name=_resolve_repository_full_name(resolved_command),
        target_number=target.number if target is not None else None,
        local_target_path=target.path if target is not None else None,
        branch_name=snapshot.branch_name,
        head_sha=snapshot.head_sha,
        allow_edits_notice_posted=allow_edits_notice_posted,
        provider_session_id=state_ref.provider_session_id,
        target_context_state=state_ref.target_context_state,
    )


def _resolve_repository_full_name(resolved_command: ResolvedCommand) -> str | None:
    """artifact metadata に入れる repository 名を返す。"""
    if resolved_command.target is not None:
        return resolved_command.target.repository_full_name
    return resolved_command.repo or resolved_command.repository_full_name


def _copy_provider_session_dir(
    provider_session_path: Path | None,
    artifact_path: Path,
) -> None:
    """provider 固有の session directory を保存する。"""
    if provider_session_path is None:
        return
    if not provider_session_path.is_dir():
        raise SessionArtifactError(
            f"`{provider_session_path}` は session directory ではありません"
        )
    shutil.copytree(provider_session_path, artifact_path / "provider-session")


def _copy_untracked_files(
    repo_root: Path,
    artifact_path: Path,
    untracked_files: list[str],
) -> None:
    """untracked file を artifact 配下へ保存する。"""
    if not untracked_files:
        return

    untracked_root = artifact_path / "untracked"
    for relative_path in untracked_files:
        source_path = repo_root / relative_path
        if not source_path.exists():
            raise SessionArtifactError(
                f"untracked file が見つかりません: `{relative_path}`"
            )
        destination_path = untracked_root / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)


def _extract_zip_archive(source_path: Path, destination_dir: Path) -> None:
    """zip archive を directory へ展開する。"""
    if not source_path.is_file():
        raise SessionArtifactError(f"`{source_path}` が見つかりません")
    try:
        with zipfile.ZipFile(source_path) as archive:
            archive.extractall(destination_dir)
    except OSError as exc:
        raise SessionArtifactError(f"`{source_path}` の展開に失敗しました") from exc
    except zipfile.BadZipFile as exc:
        raise SessionArtifactError(f"`{source_path}` は zip として不正です") from exc


def _find_single_encrypted_artifact(extracted_zip_dir: Path) -> Path:
    """展開済み zip から唯一の session artifact を返す。"""
    candidates = sorted(
        path
        for path in extracted_zip_dir.rglob("*.tar.age")
        if path.is_file()
    )
    if not candidates:
        raise SessionArtifactError("zip 内に `*.tar.age` が見つかりません")
    if len(candidates) != 1:
        raise SessionArtifactError("zip 内の `*.tar.age` は 1 件だけである必要があります")
    return candidates[0]


def _resolve_provider_session_path(restored_dir: Path) -> Path | None:
    """復元済み provider session directory を返す。"""
    provider_session_path = restored_dir / "provider-session"
    if not provider_session_path.exists():
        return None
    if not provider_session_path.is_dir():
        raise SessionArtifactError(
            f"`{provider_session_path}` は session directory ではありません"
        )
    return provider_session_path


def _run_git_command(repo_root: Path, *args: str) -> str:
    """Git コマンドを実行して標準出力を返す。"""
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        raise SessionArtifactError(
            f"`{' '.join(command)}` の実行に失敗しました{detail}"
        )
    return result.stdout


def _list_untracked_files(repo_root: Path) -> list[str]:
    """Git 管理外の file 一覧を返す。"""
    output = _run_git_command(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    candidates = [item for item in output.split("\0") if item != ""]
    files: list[str] = []
    for candidate in candidates:
        path = repo_root / candidate
        if path.is_dir():
            continue
        if _is_artifact_path(path, repo_root):
            continue
        files.append(candidate)
    return files


def _is_artifact_path(path: Path, repo_root: Path) -> bool:
    """保存済み artifact 配下の path かどうかを返す。"""
    artifacts_root = repo_root / ".vv-ai" / "artifacts"
    try:
        path.relative_to(artifacts_root)
    except ValueError:
        return False
    return True


def _sanitize_name(value: str) -> str:
    """artifact 名に使える文字へ正規化する。"""
    normalized = _SAFE_NAME_PATTERN.sub("-", value).strip("-")
    return normalized or "unknown"


def _dump_json(meta: SessionArtifactMeta) -> str:
    """metadata を整形済み JSON へ変換する。"""
    return json.dumps(meta.model_dump(), ensure_ascii=False, indent=2) + "\n"


def _write_text(path: Path, content: str) -> None:
    """テキストファイルを書き込む。"""
    path.write_text(content, encoding="utf-8")


def _format_saved_at(value: datetime) -> str:
    """artifact metadata 用の UTC timestamp を返す。"""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_datetime(value: datetime | None) -> datetime:
    """UTC aware な datetime にそろえる。"""
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _cleanup_directory(path: Path) -> None:
    """途中生成した directory を削除する。"""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_file(path: Path) -> None:
    """途中生成した file を削除する。"""
    if path.exists():
        path.unlink()
