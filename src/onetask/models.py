"""Structured output のデータモデルと JSON スキーマ。"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ImplementerPlanResult(BaseModel):
    """implementer のプラン作成結果。"""

    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "error"]
    summary: str
    message: str


class ImplementerTaskResult(BaseModel):
    """implementer のタスク実行結果。"""

    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "error"]
    changes_made: bool
    summary: str
    message: str


class ImplementerTriageResult(BaseModel):
    """implementer の review-triage 後の結果。"""

    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "error"]
    changes_made: bool
    message: str


class ReviewerResult(BaseModel):
    """reviewer の review-diff 結果。"""

    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "error"]
    review_file_path: str
    message: str


class ImplementerFinalResult(BaseModel):
    """implementer の日誌作成・コミット結果。"""

    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "error"]
    message: str


class ClaudeResponse(BaseModel):
    """claude --output-format json の生レスポンス。"""

    model_config = ConfigDict(extra="allow")
    result: str
    session_id: str
    is_error: bool


def schema_json(model: type[BaseModel]) -> str:
    """Pydantic モデルから --json-schema 用の JSON 文字列を生成する。"""
    raw = model.model_json_schema()
    raw.pop("title", None)
    raw.pop("$defs", None)
    return json.dumps(raw, ensure_ascii=False)
