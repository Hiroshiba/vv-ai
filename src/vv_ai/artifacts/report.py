"""report artifact の保存形式。"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from vv_ai.artifacts.crypto import (
    ArtifactCryptoError,
    decrypt_file_text,
    encrypt_file,
)
from vv_ai.sessions.models import ResolvedSession, SessionKey

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class ReportArtifactError(Exception):
    """report artifact の保存に失敗したことを表す例外。"""


class ReportSections(BaseModel):
    """report の各節。"""

    model_config = ConfigDict(extra="forbid")

    summary: str
    changes: str
    decisions: str
    validation: str
    risks_open_questions: str
    next_actions: str
    notes: str

    @field_validator(
        "summary",
        "changes",
        "decisions",
        "validation",
        "risks_open_questions",
        "next_actions",
        "notes",
    )
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        """空文字ではない report 本文を返す。"""
        normalized = value.strip()
        if normalized == "":
            raise ValueError("report の各節は空文字にできません")
        return normalized


class SavedReportArtifact(BaseModel):
    """保存済み report artifact の参照情報。"""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str
    artifact_path: str
    report_path: str


def build_report_artifact_name(session_key: SessionKey, workflow_id: str) -> str:
    """upload 用にも流用できる一意な artifact 名を返す。"""
    target_name = _sanitize_name(session_key.target_key)
    provider_name = _sanitize_name(session_key.provider)
    lane_name = _sanitize_name(session_key.lane)
    workflow_name = _sanitize_name(workflow_id)
    return (
        f"vv-ai-report__{target_name}__{provider_name}__{lane_name}"
        f"__{workflow_name}"
    )


def render_report_markdown(report_sections: ReportSections) -> str:
    """report sections から Markdown を組み立てる。"""
    blocks = [
        "# Report",
        "## Summary",
        report_sections.summary,
        "## Changes",
        report_sections.changes,
        "## Decisions",
        report_sections.decisions,
        "## Validation",
        report_sections.validation,
        "## Risks / Open Questions",
        report_sections.risks_open_questions,
        "## Next Actions",
        report_sections.next_actions,
        "## Notes",
        report_sections.notes,
    ]
    return "\n\n".join(blocks) + "\n"


def save_report_artifact(
    repo_root: Path,
    workflow_id: str,
    resolved_session: ResolvedSession,
    report_sections: ReportSections,
    age_public_key: str,
) -> SavedReportArtifact:
    """report artifact を保存する。"""
    artifact_name = build_report_artifact_name(resolved_session.key, workflow_id)
    reports_root = repo_root / ".vv-ai" / "artifacts" / workflow_id / "reports"
    artifact_path = reports_root / f"{artifact_name}.md.age"

    if artifact_path.exists():
        raise ReportArtifactError(f"`{artifact_path}` は既に存在します")

    try:
        reports_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vv-ai-report-") as temp_root:
            plaintext_path = Path(temp_root) / "report.md"
            plaintext_path.write_text(
                render_report_markdown(report_sections),
                encoding="utf-8",
            )
            encrypt_file(plaintext_path, artifact_path, age_public_key)
    except OSError as exc:
        raise ReportArtifactError(
            f"`{artifact_path}` の保存に失敗しました"
        ) from exc
    except ArtifactCryptoError as exc:
        raise ReportArtifactError(str(exc)) from exc

    return SavedReportArtifact(
        artifact_name=artifact_name,
        artifact_path=str(artifact_path),
        report_path=str(artifact_path),
    )


def _sanitize_name(value: str) -> str:
    """artifact 名に使える文字へ正規化する。"""
    normalized = _SAFE_NAME_PATTERN.sub("-", value).strip("-")
    return normalized or "unknown"


def load_report_artifact(
    artifact_path: Path,
    age_secret_key: str,
) -> str:
    """暗号化済み report artifact を復号して返す。"""
    try:
        return decrypt_file_text(artifact_path, age_secret_key)
    except ArtifactCryptoError as exc:
        raise ReportArtifactError(str(exc)) from exc
