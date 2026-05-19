"""実行結果モデル。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.artifacts.metrics import (
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
    ToolMetric,
)
from vv_ai.artifacts.report import ReportSections
from vv_ai.session import SessionStateRef

ExecutionStatus = Literal["success", "failure", "cancelled"]


class ExecutionResult(BaseModel):
    """実行結果と保存用データ。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status: ExecutionStatus
    report_sections: ReportSections
    usage: MetricsUsage
    behavior: MetricsBehavior
    tools: dict[str, ToolMetric]
    steps: dict[str, StepMetric]
    provider_specific: ProviderSpecificMetrics
    state_ref: SessionStateRef
    provider_session_path: Path | None
    allow_edits_notice_posted: bool
    response_text: str | None
