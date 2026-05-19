"""next AI 判断結果の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.next_decision import (
    NextDecisionError,
    format_next_decision_history_comment,
    parse_next_decision_history_marker,
    parse_next_decision_response,
)


def test_breakdownを受け付ける() -> None:
    result = parse_next_decision_response("COMMAND: breakdown")

    assert result == "breakdown"


def test_implementを受け付ける() -> None:
    result = parse_next_decision_response("COMMAND: implement")

    assert result == "implement"


def test_応答なしは失敗する() -> None:
    with pytest.raises(NextDecisionError):
        parse_next_decision_response(None)


def test_余計な行は失敗する() -> None:
    with pytest.raises(NextDecisionError):
        parse_next_decision_response("COMMAND: implement\n理由: 簡単なため")


def test_未知commandは失敗する() -> None:
    with pytest.raises(NextDecisionError):
        parse_next_decision_response("COMMAND: confirm")


def test_履歴保存コメントからbreakdownを読む() -> None:
    body = format_next_decision_history_comment("breakdown")

    result = parse_next_decision_history_marker(body)

    assert result == "breakdown"


def test_履歴保存コメントからimplementを読む() -> None:
    body = format_next_decision_history_comment("implement")

    result = parse_next_decision_history_marker(body)

    assert result == "implement"


def test_履歴保存コメントがない場合はnoneを返す() -> None:
    result = parse_next_decision_history_marker("通常コメント")

    assert result is None
