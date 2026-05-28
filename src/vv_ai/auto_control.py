"""自動進行用の制御行を解析する。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.inputs.models import CommandName

AutoContinuationAction = Literal["continue", "stop", "merge_wait"]

_AUTO_STATUS_PREFIX = "AUTO_STATUS:"
_COMMAND_PREFIX = "COMMAND:"

_ISSUE_NEXT_LABELS: dict[CommandName, str] = {
    "confirm": "vv-ai:requirements",
    "requirements": "vv-ai:arch",
    "arch": "vv-ai:detail",
    "detail": "vv-ai:next",
}


class AutoContinuationDecision(BaseModel):
    """自動進行の次アクションを表す。"""

    model_config = ConfigDict(extra="forbid")

    action: AutoContinuationAction
    next_label_name: str | None = None


class AutoControlParseResult(BaseModel):
    """制御行を除去した応答本文と自動進行判断を表す。"""

    model_config = ConfigDict(extra="forbid")

    response_text: str | None
    decision: AutoContinuationDecision


def parse_auto_control_response(
    command_name: str,
    response_text: str | None,
) -> AutoControlParseResult:
    """AI 応答から自動進行用の制御行を取り出す。"""
    if response_text is None:
        return AutoControlParseResult(
            response_text=None,
            decision=_stop_decision(),
        )

    control_lines, body_lines = _split_control_lines(response_text)
    decision = _resolve_decision(command_name, control_lines)
    return AutoControlParseResult(
        response_text="\n".join(body_lines),
        decision=decision,
    )


def _split_control_lines(response_text: str) -> tuple[dict[str, list[str]], list[str]]:
    control_lines: dict[str, list[str]] = {
        "auto_status": [],
        "command": [],
    }
    body_lines: list[str] = []
    for line in response_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(_AUTO_STATUS_PREFIX):
            control_lines["auto_status"].append(
                stripped[len(_AUTO_STATUS_PREFIX) :].strip()
            )
        elif stripped.startswith(_COMMAND_PREFIX):
            control_lines["command"].append(stripped[len(_COMMAND_PREFIX) :].strip())
        else:
            body_lines.append(line)
    return control_lines, body_lines


def _resolve_decision(
    command_name: str,
    control_lines: dict[str, list[str]],
) -> AutoContinuationDecision:
    statuses = control_lines["auto_status"]
    commands = control_lines["command"]
    if len(statuses) != 1:
        return _stop_decision()
    if len(commands) > 1:
        return _stop_decision()

    auto_status = statuses[0]
    if auto_status == "escalate":
        return _stop_decision()
    if auto_status != "continue":
        return _stop_decision()

    if command_name in _ISSUE_NEXT_LABELS:
        if len(commands) != 0:
            return _stop_decision()
        return AutoContinuationDecision(
            action="continue",
            next_label_name=_ISSUE_NEXT_LABELS[command_name],
        )
    if command_name == "review":
        return _resolve_pr_decision(commands, "address")
    if command_name == "address":
        return _resolve_pr_decision(commands, "review")
    return _stop_decision()


def _resolve_pr_decision(
    commands: list[str],
    continue_command: str,
) -> AutoContinuationDecision:
    if len(commands) != 1:
        return _stop_decision()
    command = commands[0]
    if command == "merge":
        return AutoContinuationDecision(action="merge_wait")
    if command == continue_command:
        return AutoContinuationDecision(
            action="continue",
            next_label_name=f"vv-ai:{continue_command}",
        )
    return _stop_decision()


def _stop_decision() -> AutoContinuationDecision:
    return AutoContinuationDecision(action="stop")
