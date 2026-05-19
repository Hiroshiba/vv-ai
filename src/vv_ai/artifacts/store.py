"""session manifest の保存と検索。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from vv_ai.session import SavedSessionManifest, SessionKey, SessionStateRef

_FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class SessionStoreError(Exception):
    """session manifest の保存や読込に失敗したことを表す例外。"""


def save_session_manifest(
    repo_root: Path,
    workflow_id: str,
    session_key: SessionKey,
    state_ref: SessionStateRef,
    *,
    saved_at: datetime | None = None,
) -> Path:
    """session manifest を `.vv-ai/artifacts` 配下へ保存する。"""
    manifest_path = build_session_manifest_path(repo_root, workflow_id, session_key)
    manifest = SavedSessionManifest(
        workflow_id=workflow_id,
        saved_at=_format_saved_at(saved_at),
        session_key=session_key.canonical_key,
        provider=session_key.provider,
        lane=session_key.lane,
        backend=session_key.backend,
        target_key=session_key.target_key,
        state_ref=state_ref,
    )
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise SessionStoreError(f"`{manifest_path}` の書き込みに失敗しました") from exc
    return manifest_path


def list_session_manifests(
    repo_root: Path,
    session_key: SessionKey | None = None,
) -> list[SavedSessionManifest]:
    """保存済み session manifest を新しい順で返す。"""
    artifacts_root = _artifacts_root(repo_root)
    manifests: list[SavedSessionManifest] = []
    if not artifacts_root.is_dir():
        return manifests
    for candidate in artifacts_root.glob("*/sessions/*.json"):
        manifest = _load_manifest_file(candidate)
        if session_key is not None and manifest.session_key != session_key.canonical_key:
            continue
        manifests.append(manifest)
    manifests.sort(key=_manifest_sort_key, reverse=True)
    return manifests


def load_latest_session_manifest(
    repo_root: Path,
    session_key: SessionKey,
) -> SavedSessionManifest | None:
    """同じ session key の最新 manifest を返す。"""
    manifests = list_session_manifests(repo_root, session_key)
    if not manifests:
        return None
    return manifests[0]


def build_session_manifest_path(
    repo_root: Path,
    workflow_id: str,
    session_key: SessionKey,
) -> Path:
    """session key から manifest 保存先 path を組み立てる。"""
    artifacts_root = _artifacts_root(repo_root)
    filename = build_session_manifest_filename(session_key)
    return artifacts_root / workflow_id / "sessions" / filename


def build_session_manifest_filename(session_key: SessionKey) -> str:
    """session key に対応する安全な manifest filename を返す。"""
    normalized = _FILENAME_SAFE_PATTERN.sub("-", session_key.canonical_key).strip("-")
    digest = hashlib.sha1(session_key.canonical_key.encode("utf-8")).hexdigest()[:12]
    return f"{normalized}-{digest}.json"


def _load_manifest_file(candidate: Path) -> SavedSessionManifest:
    """manifest file を検証付きで読み込む。"""
    try:
        raw_data = json.loads(candidate.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SessionStoreError(f"`{candidate}` の読み込みに失敗しました") from exc
    except json.JSONDecodeError as exc:
        raise SessionStoreError(f"`{candidate}` は JSON として不正です") from exc

    try:
        return SavedSessionManifest.model_validate(raw_data)
    except ValidationError as exc:
        raise SessionStoreError(f"`{candidate}` の値が不正です") from exc


def _manifest_sort_key(manifest: SavedSessionManifest) -> tuple[datetime, int, int, str]:
    """manifest の新しさ比較に使うキーを返す。"""
    return (
        _parse_saved_at(manifest.saved_at),
        *_parse_workflow_id_order(manifest.workflow_id),
        manifest.workflow_id,
    )


def _artifacts_root(repo_root: Path) -> Path:
    """artifact ルートを返す。"""
    return repo_root / ".vv-ai" / "artifacts"


def _format_saved_at(value: datetime | None) -> str:
    """manifest 保存用の UTC timestamp を返す。"""
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    return current.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_saved_at(saved_at: str) -> datetime:
    """saved_at を比較可能な UTC datetime に直す。"""
    try:
        return datetime.strptime(saved_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise SessionStoreError(f"`saved_at` の形式が不正です: {saved_at}") from exc


def _parse_workflow_id_order(workflow_id: str) -> tuple[int, int]:
    """workflow_id から run 番号と attempt を取り出す。"""
    match = re.fullmatch(r"run-(\d+)(?:-attempt-(\d+))?", workflow_id)
    if match is None:
        return (0, 0)
    run_id = int(match.group(1))
    attempt = int(match.group(2) or "0")
    return (run_id, attempt)
