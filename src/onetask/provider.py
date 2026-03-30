"""provider 共通インターフェース。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from onetask.tmux import TmuxManager

_POLL_INTERVAL = 2.0

ProviderName = Literal["claude", "codex"]

T = TypeVar("T", bound=BaseModel)


class ProviderRunError(Exception):
    """provider CLI の実行に失敗した。"""


class RunResult(BaseModel):
    """provider 共通の実行結果。"""

    session_id: str
    is_error: bool
    raw_result: str


class ProviderRunner(Protocol):
    """provider 実行の共通プロトコル。"""

    def run(
        self,
        *,
        tmux: TmuxManager,
        window_name: str,
        prompt: str,
        json_schema: str,
        work_dir: Path,
        prefix: str,
        repo_root: Path,
        session_id: str | None,
        permission_mode: str,
        timeout: float,
        settings_file: Path | None,
    ) -> RunResult: ...

    def parse_structured_output(self, result: RunResult, model: type[T]) -> T: ...


def create_runner(provider: ProviderName) -> ProviderRunner:
    """provider 名から runner インスタンスを生成する。"""
    if provider == "claude":
        from onetask.claude import ClaudeRunner

        return ClaudeRunner()
    if provider == "codex":
        from onetask.codex import CodexRunner

        return CodexRunner()
    raise AssertionError(f"未対応の provider: {provider}")


def wait_for_exitcode(*, exitcode_file: Path, timeout: float) -> int:
    """exitcode ファイルが出現するまでポーリングし、終了コードを返す。"""
    deadline = time.monotonic() + timeout
    while not exitcode_file.exists():
        if time.monotonic() > deadline:
            raise ProviderRunError(f"タイムアウト ({timeout}秒)")
        time.sleep(_POLL_INTERVAL)
    return int(exitcode_file.read_text().strip())
