"""`next` の AI 判断結果を扱う。"""

from __future__ import annotations

from typing import Literal

NextDecisionCommand = Literal["breakdown", "implement"]
_HISTORY_MARKER_PREFIX = "<!-- vv-ai-next-decision:"
_HISTORY_MARKER_SUFFIX = "-->"


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


def format_next_decision_history_comment(command: NextDecisionCommand) -> str:
    """AI 判断済み `next` の履歴保存コメントを返す。"""
    return (
        f"`next` は `{command}` を選択しました。\n\n"
        f"{_HISTORY_MARKER_PREFIX} command={command} {_HISTORY_MARKER_SUFFIX}"
    )


def parse_next_decision_history_marker(body: str) -> NextDecisionCommand | None:
    """履歴保存コメントから AI 判断済み command を返す。"""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith(_HISTORY_MARKER_PREFIX):
            continue
        if not stripped.endswith(_HISTORY_MARKER_SUFFIX):
            continue
        content = stripped[
            len(_HISTORY_MARKER_PREFIX):-len(_HISTORY_MARKER_SUFFIX)
        ].strip()
        if content == "command=breakdown":
            return "breakdown"
        if content == "command=implement":
            return "implement"
        raise NextDecisionError(
            "`next` の履歴保存コメントは `breakdown` または `implement` にしてください"
        )
    return None
