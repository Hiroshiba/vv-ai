"""自動進行制御行 parser の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.auto_control import parse_auto_control_response


def test_issue_command_continue_removes_auto_status_and_resolves_next_label() -> None:
    result = parse_auto_control_response(
        "confirm",
        "AUTO_STATUS: continue\n\n## 要望確認\n本文",
    )

    assert result.response_text == "\n## 要望確認\n本文"
    assert result.decision.action == "continue"
    assert result.decision.next_label_name == "vv-ai:requirements"


@pytest.mark.parametrize(
    ("command", "label"),
    [
        ("requirements", "vv-ai:arch"),
        ("arch", "vv-ai:detail"),
        ("detail", "vv-ai:next"),
    ],
)
def test_issue_command_continue_resolves_fixed_next_label(
    command: str,
    label: str,
) -> None:
    result = parse_auto_control_response(command, "本文\nAUTO_STATUS: continue")

    assert result.response_text == "本文"
    assert result.decision.action == "continue"
    assert result.decision.next_label_name == label


def test_review_address_continue_removes_command_and_resolves_address_label() -> None:
    result = parse_auto_control_response(
        "review",
        "AUTO_STATUS: continue\nCOMMAND: address\nレビュー本文",
    )

    assert result.response_text == "レビュー本文"
    assert result.decision.action == "continue"
    assert result.decision.next_label_name == "vv-ai:address"


def test_address_continue_removes_command_and_resolves_review_label() -> None:
    result = parse_auto_control_response(
        "address",
        "AUTO_STATUS: continue\nCOMMAND: review\nCOMMIT_MESSAGE: fix\nBODY:\n本文",
    )

    assert result.response_text == "COMMIT_MESSAGE: fix\nBODY:\n本文"
    assert result.decision.action == "continue"
    assert result.decision.next_label_name == "vv-ai:review"


@pytest.mark.parametrize("command", ["review", "address"])
def test_pr_command_merge_wait(command: str) -> None:
    result = parse_auto_control_response(
        command,
        "AUTO_STATUS: continue\nCOMMAND: merge\n本文",
    )

    assert result.response_text == "本文"
    assert result.decision.action == "merge_wait"
    assert result.decision.next_label_name is None


def test_auto_status_escalate_stops() -> None:
    result = parse_auto_control_response("confirm", "AUTO_STATUS: escalate\n本文")

    assert result.response_text == "本文"
    assert result.decision.action == "stop"
    assert result.decision.next_label_name is None


@pytest.mark.parametrize(
    "response_text",
    [
        "本文",
        "AUTO_STATUS: unknown\n本文",
        "AUTO_STATUS: continue\nAUTO_STATUS: continue\n本文",
        "AUTO_STATUS: continue\nCOMMAND: address\n本文",
    ],
)
def test_invalid_issue_control_lines_stop(response_text: str) -> None:
    result = parse_auto_control_response("confirm", response_text)

    assert result.decision.action == "stop"
    assert result.decision.next_label_name is None


@pytest.mark.parametrize(
    "response_text",
    [
        "AUTO_STATUS: continue\n本文",
        "AUTO_STATUS: continue\nCOMMAND: review\n本文",
        "AUTO_STATUS: continue\nCOMMAND: address\nCOMMAND: merge\n本文",
    ],
)
def test_invalid_review_control_lines_stop(response_text: str) -> None:
    result = parse_auto_control_response("review", response_text)

    assert result.decision.action == "stop"
    assert result.decision.next_label_name is None


def test_none_response_stops() -> None:
    result = parse_auto_control_response("confirm", None)

    assert result.response_text is None
    assert result.decision.action == "stop"
