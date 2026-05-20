"""next AI 判断結果の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.next_decision import (
    NextDecisionError,
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
