"""tmux セッション/ウィンドウの作成・管理。"""
from __future__ import annotations

import subprocess


class TmuxError(Exception):
    """tmux 操作に失敗した。"""


def _run_tmux(*args: str) -> subprocess.CompletedProcess[str]:
    """tmux コマンドを実行する。"""
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TmuxError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result


class TmuxManager:
    """tmux セッションとウィンドウを管理する。"""

    def __init__(self, session_name: str) -> None:
        self._session = session_name

    @property
    def session_name(self) -> str:
        """セッション名。"""
        return self._session

    def create_session(self) -> None:
        """tmux セッションを新規作成する。最初のウィンドウ名は router。"""
        _run_tmux(
            "new-session",
            "-d",
            "-s",
            self._session,
            "-n",
            "router",
        )

    def create_window(self, window_name: str) -> None:
        """セッションにウィンドウを追加する。"""
        _run_tmux(
            "new-window",
            "-t",
            self._session,
            "-n",
            window_name,
        )

    def send_keys(self, window_name: str, command: str) -> None:
        """指定ウィンドウにキーストロークを送る。"""
        target = f"{self._session}:{window_name}"
        _run_tmux("send-keys", "-t", target, command, "Enter")

    def session_exists(self) -> bool:
        """セッションが存在するか確認する。"""
        result = subprocess.run(
            ["tmux", "has-session", "-t", self._session],
            capture_output=True,
        )
        return result.returncode == 0

    def kill_session(self) -> None:
        """セッション全体を破棄する。"""
        if self.session_exists():
            _run_tmux("kill-session", "-t", self._session)
