"""実行結果に対応する artifact 保存オーケストレーション。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.artifacts.crypto import ArtifactCryptoError, resolve_age_public_key
from vv_ai.artifacts.metrics import (
    MetricsArtifactError,
    SavedMetricsArtifact,
    save_metrics_artifact,
)
from vv_ai.artifacts.report import (
    ReportArtifactError,
    SavedReportArtifact,
    save_report_artifact,
)
from vv_ai.artifacts.session import (
    SavedSessionArtifact,
    SessionArtifactError,
    save_session_artifact,
)
from vv_ai.executions.result import ExecutionResult
from vv_ai.workflow.preflight import ReadyExecution


class ExecutionArtifactError(Exception):
    """artifact 保存に失敗したことを表す例外。"""


class SavedExecutionArtifacts(BaseModel):
    """保存済み 3 種 artifact の参照情報。"""

    model_config = ConfigDict(extra="forbid")

    session: SavedSessionArtifact
    metrics: SavedMetricsArtifact
    report: SavedReportArtifact


def save_execution_artifacts(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    execution_result: ExecutionResult,
) -> SavedExecutionArtifacts:
    """実行結果に対応する 3 種 artifact を保存する。"""
    if ready_execution.resolved_session is None:
        raise ExecutionArtifactError(
            "artifact 保存に必要な session が解決されていません"
        )

    try:
        age_public_key = resolve_age_public_key(env)
        session_artifact = save_session_artifact(
            repo_root,
            ready_execution.workflow_id,
            ready_execution.command,
            ready_execution.resolved_provider,
            ready_execution.resolved_session,
            execution_result.state_ref,
            age_public_key,
            provider_session_path=execution_result.provider_session_path,
            allow_edits_notice_posted=execution_result.allow_edits_notice_posted,
        )
        metrics_artifact = save_metrics_artifact(
            repo_root,
            ready_execution.workflow_id,
            ready_execution.command,
            ready_execution.resolved_provider,
            ready_execution.resolved_session,
            age_public_key,
            usage=execution_result.usage,
            behavior=execution_result.behavior,
            tools=execution_result.tools,
            steps=execution_result.steps,
            provider_specific=execution_result.provider_specific,
        )
        report_artifact = save_report_artifact(
            repo_root,
            ready_execution.workflow_id,
            ready_execution.resolved_session,
            execution_result.report_sections,
            age_public_key,
        )
    except (
        ArtifactCryptoError,
        MetricsArtifactError,
        ReportArtifactError,
        SessionArtifactError,
    ) as exc:
        raise ExecutionArtifactError(str(exc)) from exc

    return SavedExecutionArtifacts(
        session=session_artifact,
        metrics=metrics_artifact,
        report=report_artifact,
    )
