"""`next` の AI 判断結果を扱う。"""

from __future__ import annotations

from typing import Literal

NextDecisionCommand = Literal["breakdown", "implement"]


class NextDecisionError(Exception):
    """`next` の AI 判断結果が不正であることを表す例外。"""


def parse_next_decision_response(response_text: str | None) -> NextDecisionCommand:
    """AI 判断結果から次に実行する command を返す。"""
    if response_text is None:
        raise NextDecisionError("AI 判断の応答がありません")

    lines = [line.strip() for line in response_text.splitlines() if line.strip() != ""]
    if len(lines) != 1:
        raise NextDecisionError("AI 判断の応答は `COMMAND: <command>` の 1 行にしてください")

    line = lines[0]
    prefix = "COMMAND:"
    if not line.startswith(prefix):
        raise NextDecisionError("AI 判断の応答は `COMMAND:` で始めてください")

    command = line[len(prefix):].strip()
    if command == "breakdown" or command == "implement":
        return command
    raise NextDecisionError(
        "`next` の AI 判断結果は `breakdown` または `implement` にしてください"
    )
