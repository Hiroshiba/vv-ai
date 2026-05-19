"""session key / lane / 復元方針のモデル。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vv_ai.inputs.models import SessionMode
from vv_ai.inputs.resolve import BackendName

SessionLane = Literal["main", "review"]
RestoreStrategy = Literal["inherit", "compact", "new"]


class SessionKey(BaseModel):
    """artifact 検索や保存に使う session の共通キー。"""

    model_config = ConfigDict(extra="forbid")

    backend: BackendName
    target_key: str
    provider: str
    lane: SessionLane
    canonical_key: str


class TargetContextState(BaseModel):
    """provider に渡した target context の version 群。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    title_hash: str | None = None
    description_hash: str | None = None
    comment_hashes: dict[str, str] = Field(default_factory=dict)


class SessionStateRef(BaseModel):
    """provider 固有 session の参照情報。"""

    model_config = ConfigDict(extra="forbid")

    provider_session_id: str | None = None
    summary_path: str | None = None
    artifact_hint: str | None = None
    target_context_state: TargetContextState | None = None


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

    requested_mode: SessionMode
    lane: SessionLane
    key: SessionKey
    restore_strategy: RestoreStrategy
    restore_manifest: SavedSessionManifest | None = None
    save_manifest_path: str
    state_ref: SessionStateRef | None = None
    restored_artifact_dir: str | None = None
    restored_provider_session_path: str | None = None
    allow_edits_notice_posted: bool = False
