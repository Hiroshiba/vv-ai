"""値制約を持つ共通型。"""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

type NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


def validate_non_empty_string(value: str) -> NonEmptyString:
    """空文字でない文字列を返す。"""
    normalized = value.strip()
    if normalized == "":
        raise ValueError("空文字にできません")
    return normalized
