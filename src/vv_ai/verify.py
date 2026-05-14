"""workflow が本体実行すべきかを高速に判定する verify サブコマンド。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from vv_ai.config import VVAIConfig
from vv_ai.input import (
    InputError,
    IssueCommentEvent,
    IssueLabeledEvent,
    PullRequestLabeledEvent,
    WorkflowDispatchEvent,
    build_raw_input_from_issue_labeled_event,
    build_raw_input_from_pull_request_labeled_event,
    parse_comment_invocation,
)

VerifyEventName = Literal[
    "issue_comment", "workflow_dispatch", "issues", "pull_request",
]
VerifyReason = Literal["not_vv_ai_prefix", "not_vv_ai_label", "unauthorized"]


class VerifyResult(BaseModel):
    """verify 判定の結果。"""

    should_run: bool
    actor: str | None
    event: VerifyEventName
    reason: VerifyReason | None = None


def run_verify(
    event: VerifyEventName, event_file: Path, config: VVAIConfig
) -> VerifyResult:
    """`vv-ai.yml` と event payload だけを見て本体実行可否を判定する。"""
    try:
        raw = event_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"`{event_file}` の読み込みに失敗しました") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"`{event_file}` を JSON として解釈できませんでした"
        ) from exc

    if not isinstance(payload, dict):
        raise InputError(f"`{event_file}` は JSON object である必要があります")

    if event == "issue_comment":
        return _verify_issue_comment(payload, config.allowed_users)
    if event == "workflow_dispatch":
        return _verify_workflow_dispatch(payload, config.allowed_users)
    if event == "issues":
        return _verify_issue_labeled(payload, config.allowed_users)
    if event == "pull_request":
        return _verify_pull_request_labeled(payload, config.allowed_users)
    raise AssertionError(f"未対応の event です: {event}")


def _verify_issue_comment(
    payload: dict[str, object], allowed_users: list[str]
) -> VerifyResult:
    try:
        parsed = IssueCommentEvent.model_validate(payload)
    except ValidationError as exc:
        raise InputError("issue_comment payload の値が不正です") from exc

    actor = parsed.sender.login
    try:
        parse_comment_invocation(parsed.comment.body)
    except InputError:
        return VerifyResult(
            should_run=False,
            actor=actor,
            event="issue_comment",
            reason="not_vv_ai_prefix",
        )

    if actor not in allowed_users:
        return VerifyResult(
            should_run=False,
            actor=actor,
            event="issue_comment",
            reason="unauthorized",
        )

    return VerifyResult(should_run=True, actor=actor, event="issue_comment")


def _verify_workflow_dispatch(
    payload: dict[str, object], allowed_users: list[str]
) -> VerifyResult:
    try:
        parsed = WorkflowDispatchEvent.model_validate(payload)
    except ValidationError as exc:
        raise InputError("workflow_dispatch payload の値が不正です") from exc

    actor = parsed.sender.login
    if actor not in allowed_users:
        return VerifyResult(
            should_run=False,
            actor=actor,
            event="workflow_dispatch",
            reason="unauthorized",
        )
    return VerifyResult(should_run=True, actor=actor, event="workflow_dispatch")


def _verify_issue_labeled(
    payload: dict[str, object], allowed_users: list[str]
) -> VerifyResult:
    try:
        parsed = IssueLabeledEvent.model_validate(payload)
    except ValidationError as exc:
        raise InputError("issues payload の値が不正です") from exc

    actor = parsed.sender.login
    try:
        build_raw_input_from_issue_labeled_event(parsed)
    except InputError:
        return VerifyResult(
            should_run=False,
            actor=actor,
            event="issues",
            reason="not_vv_ai_label",
        )

    if actor not in allowed_users:
        return VerifyResult(
            should_run=False,
            actor=actor,
            event="issues",
            reason="unauthorized",
        )

    return VerifyResult(should_run=True, actor=actor, event="issues")


def _verify_pull_request_labeled(
    payload: dict[str, object], allowed_users: list[str]
) -> VerifyResult:
    try:
        parsed = PullRequestLabeledEvent.model_validate(payload)
    except ValidationError as exc:
        raise InputError("pull_request payload の値が不正です") from exc

    actor = parsed.sender.login
    try:
        build_raw_input_from_pull_request_labeled_event(parsed)
    except InputError:
        return VerifyResult(
            should_run=False,
            actor=actor,
            event="pull_request",
            reason="not_vv_ai_label",
        )

    if actor not in allowed_users:
        return VerifyResult(
            should_run=False,
            actor=actor,
            event="pull_request",
            reason="unauthorized",
        )

    return VerifyResult(should_run=True, actor=actor, event="pull_request")
