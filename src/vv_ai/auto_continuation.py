"""自動継続の計画保存と後段適用を提供する。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from vv_ai.artifacts.session import build_session_artifact_target_prefix
from vv_ai.backends.github.client import GitHubClient
from vv_ai.backends.github.models import (
    GitHubArtifact,
    GitHubIssueLabeledEvent,
    GitHubTargetDetails,
)
from vv_ai.inputs.models import TargetType
from vv_ai.inputs.resolve import ResolvedTarget
from vv_ai.workflow.preflight import ReadyExecution

AUTO_CONTINUATION_LIMIT = 10
AUTO_LABEL_NAME = "vv-ai:auto"
NEXT_LABEL_NAME = "vv-ai:next"

ApplyStatus = Literal[
    "no_plan",
    "auto_removed",
    "target_closed",
    "limit_reached",
    "continued",
]


class AutoContinuationError(Exception):
    """自動継続の計画保存または適用に失敗したことを表す例外。"""


class AutoContinuationPlan(BaseModel):
    """artifact upload 後に適用する自動継続計画。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    repository_full_name: str
    target_type: TargetType
    target_number: int
    source_label_name: str
    next_label_name: str
    session_artifact_target_prefix: str
    workflow_id: str


class AutoContinuationApplyResult(BaseModel):
    """自動継続計画の適用結果。"""

    model_config = ConfigDict(extra="forbid")

    status: ApplyStatus


def build_auto_continuation_plan(
    ready_execution: ReadyExecution,
) -> AutoContinuationPlan:
    """実行状態から自動継続計画を組み立てる。"""
    command = ready_execution.command
    target = command.target
    session = ready_execution.resolved_session
    if target is None:
        raise AutoContinuationError("自動継続計画に必要な target がありません")
    if target.repository_full_name is None:
        raise AutoContinuationError("自動継続計画に必要な repository がありません")
    if target.number is None:
        raise AutoContinuationError("自動継続計画に必要な target 番号がありません")
    if command.trigger_label_name is None:
        raise AutoContinuationError("自動継続計画に必要な起動元 label がありません")
    if session is None:
        raise AutoContinuationError("自動継続計画に必要な session がありません")

    return AutoContinuationPlan(
        repository_full_name=target.repository_full_name,
        target_type=target.kind,
        target_number=target.number,
        source_label_name=command.trigger_label_name,
        next_label_name=NEXT_LABEL_NAME,
        session_artifact_target_prefix=build_session_artifact_target_prefix(
            session.key.target_key
        ),
        workflow_id=ready_execution.workflow_id,
    )


def save_auto_continuation_plan(
    repo_root: Path,
    workflow_id: str,
    plan: AutoContinuationPlan,
) -> Path:
    """自動継続計画を artifact 配下へ保存する。"""
    plan_path = _build_plan_path(repo_root, workflow_id)
    if plan.workflow_id != workflow_id:
        raise AutoContinuationError("自動継続計画の workflow_id が一致しません")
    if plan_path.exists():
        raise AutoContinuationError(f"`{plan_path}` は既に存在します")
    try:
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise AutoContinuationError(f"`{plan_path}` の保存に失敗しました") from exc
    return plan_path


def load_auto_continuation_plan(
    repo_root: Path,
    workflow_id: str,
) -> AutoContinuationPlan | None:
    """保存済み自動継続計画を読み込む。"""
    plan_path = _build_plan_path(repo_root, workflow_id)
    if not plan_path.exists():
        return None
    try:
        raw_data = json.loads(plan_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AutoContinuationError(f"`{plan_path}` の読み込みに失敗しました") from exc
    except json.JSONDecodeError as exc:
        raise AutoContinuationError(f"`{plan_path}` は JSON として不正です") from exc
    try:
        return AutoContinuationPlan.model_validate(raw_data)
    except ValidationError as exc:
        raise AutoContinuationError(f"`{plan_path}` の値が不正です") from exc


def apply_auto_continuation_plan(
    repo_root: Path,
    workflow_id: str,
    github_client: GitHubClient,
) -> AutoContinuationApplyResult:
    """保存済み自動継続計画を GitHub target へ適用する。"""
    plan = load_auto_continuation_plan(repo_root, workflow_id)
    if plan is None:
        return AutoContinuationApplyResult(status="no_plan")

    label_names = github_client.list_issue_label_names(
        plan.repository_full_name,
        plan.target_number,
    )
    if AUTO_LABEL_NAME not in label_names:
        _remove_source_label_if_present(plan, github_client, label_names)
        return AutoContinuationApplyResult(status="auto_removed")

    target = _build_target(plan)
    target_details = github_client.get_target_details(target)
    if _is_target_closed(target_details):
        _remove_source_label_if_present(plan, github_client, label_names)
        return AutoContinuationApplyResult(status="target_closed")

    _remove_source_label_if_present(plan, github_client, label_names)

    if _is_limit_reached(plan, github_client):
        github_client.remove_issue_label(
            plan.repository_full_name,
            plan.target_number,
            AUTO_LABEL_NAME,
        )
        return AutoContinuationApplyResult(status="limit_reached")

    github_client.add_issue_label(
        plan.repository_full_name,
        plan.target_number,
        plan.next_label_name,
    )
    return AutoContinuationApplyResult(status="continued")


def _build_plan_path(repo_root: Path, workflow_id: str) -> Path:
    return repo_root / ".vv-ai" / "artifacts" / workflow_id / "auto-continuation" / "plan.json"


def _build_target(plan: AutoContinuationPlan) -> ResolvedTarget:
    return ResolvedTarget(
        backend="github",
        kind=plan.target_type,
        canonical_id=f"github:{plan.repository_full_name}#{plan.target_number}",
        repository_full_name=plan.repository_full_name,
        number=plan.target_number,
    )


def _is_target_closed(target_details: GitHubTargetDetails) -> bool:
    return target_details.state != "OPEN"


def _remove_source_label_if_present(
    plan: AutoContinuationPlan,
    github_client: GitHubClient,
    label_names: list[str],
) -> None:
    if plan.source_label_name not in label_names:
        return
    github_client.remove_issue_label(
        plan.repository_full_name,
        plan.target_number,
        plan.source_label_name,
    )


def _is_limit_reached(
    plan: AutoContinuationPlan,
    github_client: GitHubClient,
) -> bool:
    auto_started_at = _find_latest_auto_label_created_at(
        github_client.list_issue_labeled_events(
            plan.repository_full_name,
            plan.target_number,
        )
    )
    artifacts = github_client.list_repository_artifacts_by_prefix(
        plan.repository_full_name,
        plan.session_artifact_target_prefix,
    )
    return _count_artifacts_since(artifacts, auto_started_at) >= AUTO_CONTINUATION_LIMIT


def _find_latest_auto_label_created_at(
    events: list[GitHubIssueLabeledEvent],
) -> datetime:
    candidates = [
        _parse_github_datetime(event.created_at)
        for event in events
        if event.label_name == AUTO_LABEL_NAME
    ]
    if len(candidates) == 0:
        raise AutoContinuationError("最新の自動継続 label 付与時刻が見つかりません")
    return max(candidates)


def _count_artifacts_since(
    artifacts: list[GitHubArtifact],
    threshold: datetime,
) -> int:
    return sum(
        1 for artifact in artifacts if _parse_github_datetime(artifact.created_at) >= threshold
    )


def _parse_github_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AutoContinuationError(f"GitHub timestamp が不正です: {value}") from exc
