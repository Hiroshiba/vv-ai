"""vv-ai 導入ツールの単体テスト。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from typing import NoReturn

import pytest

from tools.setup_vv_ai import (
    SetupVVAIError,
    _confirm_step,
    _setup_vv_ai,
)


def test_setup_vv_ai_runs_all_steps_with_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_setup_vv_ai は yes 指定時に全導入作業を実行する。"""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    _setup_vv_ai("org/repo", True, True)

    assert calls == [
        [
            sys.executable,
            "-m",
            "tools.create_vv_ai_labels",
            "--repo",
            "org/repo",
            "--dry-run",
        ],
        [
            sys.executable,
            "-m",
            "tools.set_codex_auth_secret",
            "--repo",
            "org/repo",
            "--dry-run",
        ],
        [
            sys.executable,
            "-m",
            "tools.set_claude_settings_secret",
            "--repo",
            "org/repo",
            "--dry-run",
        ],
    ]


def test_setup_vv_ai_skips_no_answer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_setup_vv_ai は n 回答の導入作業をスキップする。"""
    answers: Iterator[str] = iter(["n", "y", "n"])
    calls: list[list[str]] = []

    def fake_input(prompt: str) -> str:
        return next(answers)

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(subprocess, "run", fake_run)

    _setup_vv_ai(None, False, False)

    output = capsys.readouterr().out
    assert calls == [[sys.executable, "-m", "tools.set_codex_auth_secret"]]
    assert "スキップしました: GitHub ラベルを作成または更新しますか？" in output
    assert "スキップしました: Claude 設定を GitHub Secret に設定しますか？" in output


def test_confirm_step_rejects_invalid_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_confirm_step は y と n 以外の回答を拒否する。"""
    monkeypatch.setattr("builtins.input", lambda prompt: "invalid")

    with pytest.raises(SetupVVAIError, match="y または n"):
        _confirm_step("実行しますか？", False)


def test_setup_vv_ai_rejects_failed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_setup_vv_ai は導入作業の失敗を拒否する。"""

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SetupVVAIError, match="失敗しました"):
        _setup_vv_ai(None, False, True)


def test_setup_vv_ai_rejects_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_setup_vv_ai は導入作業の起動失敗を拒否する。"""

    def fake_run(cmd: list[str]) -> NoReturn:
        raise OSError("起動できません")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SetupVVAIError, match="実行に失敗しました"):
        _setup_vv_ai(None, False, True)
