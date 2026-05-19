"""metrics artifact の保存形式。"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError

from vv_ai.artifacts.crypto import (
    ArtifactCryptoError,
    decrypt_file_text,
    encrypt_file,
)
from vv_ai.provider import ResolvedProvider
from vv_ai.resolve import BackendName, ResolvedCommand
from vv_ai.session import ResolvedSession, SessionKey

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class MetricsArtifactError(Exception):
    """metrics artifact の保存に失敗したことを表す例外。"""


class MetricsSummary(BaseModel):
    """実行の概要。"""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    saved_at: str
    event_name: str
    command: str
    provider: str
    backend: BackendName
    target_key: str
    target_kind: str | None = None
    repository_full_name: str | None = None
    target_number: int | None = None
    local_target_path: str | None = None
    session_key: str
    dry_run: bool


class MetricsUsage(BaseModel):
    """トークンとコストの集計。"""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class MetricsBehavior(BaseModel):
    """実行のふるまい集計。"""

    model_config = ConfigDict(extra="forbid")

    total_turns: int | None = None
    failed_turns: int | None = None
    success_rate: float | None = None
    command_execution_count: int | None = None
    file_change_count: int | None = None
    mcp_tool_call_count: int | None = None
    web_search_count: int | None = None
    plan_update_count: int | None = None
    session_count: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    pr_count: int | None = None
    commit_count: int | None = None
    active_time_seconds: float | None = None
    code_edit_decisions: dict[str, int] | None = None


class ToolMetric(BaseModel):
    """ツール単位の集計。"""

    model_config = ConfigDict(extra="forbid")

    success_count: int | None = None
    failure_count: int | None = None
    duration_seconds: float | None = None


class StepMetric(BaseModel):
    """フェーズ単位の所要時間。"""

    model_config = ConfigDict(extra="forbid")

    duration_seconds: float | None = None


class CodexProviderMetrics(BaseModel):
    """Codex 固有の詳細 metrics。"""

    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    total_turns: int | None = None
    failed_turns: int | None = None
    success_rate: float | None = None
    command_execution_count: int | None = None
    file_change_count: int | None = None
    mcp_tool_call_count: int | None = None
    web_search_count: int | None = None
    plan_update_count: int | None = None


class ClaudeProviderMetrics(BaseModel):
    """Claude Code 固有の詳細 metrics。"""

    model_config = ConfigDict(extra="forbid")

    session_count: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    pr_count: int | None = None
    commit_count: int | None = None
    active_time_seconds: float | None = None
    code_edit_decisions: dict[str, int] | None = None


class ProviderSpecificMetrics(BaseModel):
    """provider ごとの詳細 metrics。"""

    model_config = ConfigDict(extra="forbid")

    codex: CodexProviderMetrics | None = None
    claude: ClaudeProviderMetrics | None = None


class MetricsArtifact(BaseModel):
    """保存する metrics artifact 全体。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    summary: MetricsSummary
    usage: MetricsUsage
    behavior: MetricsBehavior
    tools: dict[str, ToolMetric]
    steps: dict[str, StepMetric]
    provider_specific: ProviderSpecificMetrics


class SavedMetricsArtifact(BaseModel):
    """保存済み metrics artifact の参照情報。"""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str
    artifact_path: str


def build_metrics_artifact_name(session_key: SessionKey, workflow_id: str) -> str:
    """upload 用にも流用できる一意な artifact 名を返す。"""
    target_name = _sanitize_name(session_key.target_key)
    provider_name = _sanitize_name(session_key.provider)
    lane_name = _sanitize_name(session_key.lane)
    workflow_name = _sanitize_name(workflow_id)
    return (
        f"vv-ai-metrics__{target_name}__{provider_name}__{lane_name}"
        f"__{workflow_name}"
    )


def save_metrics_artifact(
    repo_root: Path,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
    resolved_session: ResolvedSession,
    age_public_key: str,
    *,
    usage: MetricsUsage | None = None,
    behavior: MetricsBehavior | None = None,
    tools: dict[str, ToolMetric] | None = None,
    steps: dict[str, StepMetric] | None = None,
    provider_specific: ProviderSpecificMetrics | None = None,
    saved_at: datetime | None = None,
) -> SavedMetricsArtifact:
    """metrics artifact を保存する。"""
    artifact_name = build_metrics_artifact_name(resolved_session.key, workflow_id)
    artifact_path = (
        repo_root
        / ".vv-ai"
        / "artifacts"
        / workflow_id
        / "metrics"
        / f"{artifact_name}.json.age"
    )
    if artifact_path.exists():
        raise MetricsArtifactError(f"`{artifact_path}` は既に存在します")

    artifact = MetricsArtifact(
        summary=_build_summary(
            workflow_id=workflow_id,
            resolved_command=resolved_command,
            resolved_provider=resolved_provider,
            resolved_session=resolved_session,
            saved_at=_normalize_datetime(saved_at),
        ),
        usage=usage or MetricsUsage(),
        behavior=behavior or MetricsBehavior(),
        tools=tools or {},
        steps=steps or {},
        provider_specific=provider_specific or ProviderSpecificMetrics(),
    )
    try:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vv-ai-metrics-") as temp_root:
            plaintext_path = Path(temp_root) / f"{artifact_name}.json"
            plaintext_path.write_text(
                json.dumps(artifact.model_dump(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            encrypt_file(plaintext_path, artifact_path, age_public_key)
    except OSError as exc:
        raise MetricsArtifactError(
            f"`{artifact_path}` の書き込みに失敗しました"
        ) from exc
    except ArtifactCryptoError as exc:
        raise MetricsArtifactError(str(exc)) from exc

    return SavedMetricsArtifact(
        artifact_name=artifact_name,
        artifact_path=str(artifact_path),
    )


def load_metrics_artifact(
    artifact_path: Path,
    age_secret_key: str,
) -> MetricsArtifact:
    """暗号化済み metrics artifact を復号して返す。"""
    try:
        plaintext = decrypt_file_text(artifact_path, age_secret_key)
        return MetricsArtifact.model_validate_json(plaintext)
    except ArtifactCryptoError as exc:
        raise MetricsArtifactError(str(exc)) from exc
    except ValidationError as exc:
        raise MetricsArtifactError(f"`{artifact_path}` の値が不正です") from exc


def _build_summary(
    *,
    workflow_id: str,
    resolved_command: ResolvedCommand,
    resolved_provider: ResolvedProvider,
    resolved_session: ResolvedSession,
    saved_at: datetime,
) -> MetricsSummary:
    """resolved 情報から summary を組み立てる。"""
    target = resolved_command.target
    return MetricsSummary(
        workflow_id=workflow_id,
        saved_at=_format_saved_at(saved_at),
        event_name=resolved_command.event_name,
        command=resolved_command.command,
        provider=resolved_provider.name,
        backend=resolved_session.key.backend,
        target_key=resolved_session.key.target_key,
        target_kind=target.kind if target is not None else None,
        repository_full_name=_resolve_repository_full_name(resolved_command),
        target_number=target.number if target is not None else None,
        local_target_path=target.path if target is not None else None,
        session_key=resolved_session.key.canonical_key,
        dry_run=resolved_command.dry_run,
    )


def _resolve_repository_full_name(resolved_command: ResolvedCommand) -> str | None:
    """artifact metadata に入れる repository 名を返す。"""
    if resolved_command.target is not None:
        return resolved_command.target.repository_full_name
    return resolved_command.repo or resolved_command.repository_full_name


def _sanitize_name(value: str) -> str:
    """artifact 名に使える文字へ正規化する。"""
    normalized = _SAFE_NAME_PATTERN.sub("-", value).strip("-")
    if normalized != "":
        return normalized
    return "item"


def _format_saved_at(value: datetime) -> str:
    """artifact metadata 用の UTC timestamp を返す。"""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_datetime(value: datetime | None) -> datetime:
    """保存時刻を UTC aware datetime に正規化する。"""
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)
