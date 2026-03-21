"""ローカル backend 用の保存基盤。"""

from __future__ import annotations

import json
import re
import secrets
import string
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

IssueStatus = Literal["open", "closed"]
PRStatus = Literal["open", "closed", "merged"]

_RANDOM_ALPHABET = string.ascii_lowercase + string.digits
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class LocalMetaError(Exception):
    """ローカル metadata の読み書きに失敗したことを表す例外。"""


class LocalIssueMeta(BaseModel):
    """local Issue の最小 metadata。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["issue"] = "issue"
    status: IssueStatus = "open"
    created_at: str
    updated_at: str
    backend: Literal["local"] = "local"


class LocalPRMeta(BaseModel):
    """local PR の最小 metadata。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["pr"] = "pr"
    status: PRStatus = "open"
    created_at: str
    updated_at: str
    backend: Literal["local"] = "local"
    head_branch: str
    base_branch: str


LocalMeta = LocalIssueMeta | LocalPRMeta


def ensure_local_workspace(repo_root: Path) -> Path:
    """`.vv-ai` の基本ディレクトリ群を作成する。"""
    workspace_root = repo_root / ".vv-ai"
    for directory in (
        workspace_root,
        workspace_root / "issues",
        workspace_root / "prs",
        workspace_root / "artifacts",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return workspace_root


def generate_local_workflow_id(now: datetime | None = None) -> str:
    """local 実行用の workflow_id を生成する。"""
    current = _normalize_datetime(now)
    return f"{current.strftime('%Y%m%d-%H%M%S')}-{_random_suffix(6)}"


def create_local_issue(
    repo_root: Path,
    slug_hint: str,
    body: str,
    *,
    created_at: datetime | None = None,
) -> Path:
    """local Issue 用のディレクトリ一式を作成する。"""
    timestamp = _normalize_datetime(created_at)
    workspace_root = ensure_local_workspace(repo_root)
    issue_id = _build_local_id(slug_hint)
    issue_dir = workspace_root / "issues" / issue_id
    try:
        issue_dir.mkdir(parents=False, exist_ok=False)
        (issue_dir / "comments").mkdir()
        (issue_dir / "issue.md").write_text(body, encoding="utf-8")
        meta = LocalIssueMeta(
            id=issue_id,
            created_at=_format_metadata_timestamp(timestamp),
            updated_at=_format_metadata_timestamp(timestamp),
        )
        _write_meta(issue_dir / "meta.json", meta)
    except OSError as exc:
        raise LocalMetaError(f"`{issue_dir}` の作成に失敗しました") from exc
    return issue_dir


def create_local_pr(
    repo_root: Path,
    slug_hint: str,
    body: str,
    *,
    head_branch: str,
    base_branch: str,
    created_at: datetime | None = None,
) -> Path:
    """local PR 用のディレクトリ一式を作成する。"""
    timestamp = _normalize_datetime(created_at)
    workspace_root = ensure_local_workspace(repo_root)
    pr_id = _build_local_id(slug_hint)
    pr_dir = workspace_root / "prs" / pr_id
    try:
        pr_dir.mkdir(parents=False, exist_ok=False)
        (pr_dir / "comments").mkdir()
        (pr_dir / "pr.md").write_text(body, encoding="utf-8")
        meta = LocalPRMeta(
            id=pr_id,
            created_at=_format_metadata_timestamp(timestamp),
            updated_at=_format_metadata_timestamp(timestamp),
            head_branch=head_branch,
            base_branch=base_branch,
        )
        _write_meta(pr_dir / "meta.json", meta)
    except OSError as exc:
        raise LocalMetaError(f"`{pr_dir}` の作成に失敗しました") from exc
    return pr_dir


def append_local_comment(
    target_dir: Path,
    body: str,
    *,
    slug_hint: str | None = None,
    created_at: datetime | None = None,
) -> Path:
    """local target 配下へ comment Markdown を保存する。"""
    comments_dir = target_dir / "comments"
    if not comments_dir.is_dir():
        raise LocalMetaError(f"`{comments_dir}` が見つかりません")

    current = _normalize_datetime(created_at)
    slug = _slugify(slug_hint or body or "comment", fallback="comment")
    comment_path = _build_comment_path(comments_dir, slug, current)
    try:
        comment_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise LocalMetaError(f"`{comment_path}` の書き込みに失敗しました") from exc
    return comment_path


def load_local_meta(target_dir: Path) -> LocalMeta:
    """local target の `meta.json` を検証付きで読み込む。"""
    meta_path = target_dir / "meta.json"
    try:
        raw_data = json.loads(meta_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LocalMetaError(f"`{meta_path}` の読み込みに失敗しました") from exc
    except json.JSONDecodeError as exc:
        raise LocalMetaError(f"`{meta_path}` は JSON として不正です") from exc

    if not isinstance(raw_data, dict):
        raise LocalMetaError(f"`{meta_path}` は JSON オブジェクトである必要があります")

    kind = raw_data.get("kind")
    try:
        if kind == "issue":
            return LocalIssueMeta.model_validate(raw_data)
        if kind == "pr":
            return LocalPRMeta.model_validate(raw_data)
    except ValidationError as exc:
        raise LocalMetaError(f"`{meta_path}` の値が不正です") from exc
    raise LocalMetaError(f"`{meta_path}` の `kind` が不正です")


def _write_meta(meta_path: Path, meta: LocalMeta) -> None:
    """metadata を整形して保存する。"""
    try:
        meta_path.write_text(
            json.dumps(meta.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise LocalMetaError(f"`{meta_path}` の書き込みに失敗しました") from exc


def _build_local_id(slug_hint: str) -> str:
    """人が読める slug と suffix から local ID を作る。"""
    slug = _slugify(slug_hint, fallback="item")
    return f"{slug}-{_random_suffix(6)}"


def _slugify(raw_text: str, *, fallback: str) -> str:
    """ASCII 小文字と数字だけの slug に正規化する。"""
    normalized = raw_text.strip().lower()
    normalized = _SLUG_PATTERN.sub("-", normalized)
    normalized = normalized.strip("-")
    return normalized or fallback


def _build_comment_path(
    comments_dir: Path,
    slug: str,
    timestamp: datetime,
) -> Path:
    """衝突しない comment path を組み立てる。"""
    current = timestamp
    while True:
        candidate = comments_dir / f"{current.strftime('%Y%m%d-%H%M%S')}-{slug}.md"
        if not candidate.exists():
            return candidate
        current += timedelta(seconds=1)


def _normalize_datetime(value: datetime | None) -> datetime:
    """UTC aware な datetime にそろえる。"""
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _format_metadata_timestamp(value: datetime) -> str:
    """metadata 保存用の UTC timestamp を返す。"""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _random_suffix(length: int) -> str:
    """英数字 suffix を生成する。"""
    return "".join(secrets.choice(_RANDOM_ALPHABET) for _ in range(length))
