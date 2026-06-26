"""vv-ai 用 age 鍵生成ツールの単体テスト。"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

import pytest

from tools import setup_vv_ai
from tools.generate_vv_ai_age_key import (
    GenerateVVAIKeyError,
    _generate_age_key,
    main,
)

_REPOSITORY_ROOT: Path = Path(__file__).resolve().parents[1]


def test_project_scripts_include_generate_vv_ai_age_key() -> None:
    """generate-vv-ai-age-key を公開コマンドに登録する。"""
    pyproject = tomllib.loads(
        (_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["scripts"]["generate-vv-ai-age-key"] == (
        "tools.generate_vv_ai_age_key:main"
    )


def test_project_scripts_include_setup_vv_ai() -> None:
    """setup-vv-ai を公開コマンドに登録する。"""
    pyproject = tomllib.loads(
        (_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["scripts"]["setup-vv-ai"] == "tools.setup_vv_ai:main"


def test_setup_vv_ai_main_exits_1_until_implemented(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup-vv-ai は導入作業の実装まで未実装エラーを出す。"""
    monkeypatch.setattr(sys, "argv", ["setup-vv-ai"])

    with pytest.raises(SystemExit) as e:
        setup_vv_ai.main()

    captured = capsys.readouterr()
    assert e.value.code == 1
    assert captured.out == ""
    assert captured.err == (
        "エラー: setup-vv-ai は未実装です。Issue #271 で実装します\n"
    )


def test_generate_age_key_outputs_age_keygen_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_generate_age_key は age-keygen の標準出力をそのまま返す。"""
    expected_output = (
        "Public key: age1example\n"
        "AGE-SECRET-KEY-1EXAMPLE\n"
    )

    def fake_run(
        cmd: list[str],
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cmd == ["age-keygen"]
        assert text is True
        assert capture_output is True
        return subprocess.CompletedProcess(cmd, 0, stdout=expected_output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _generate_age_key() == expected_output


def test_generate_age_key_rejects_missing_age_keygen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_generate_age_key は age-keygen 不在を専用エラーにする。"""

    def fake_run(
        cmd: list[str],
        text: bool,
        capture_output: bool,
    ) -> NoReturn:
        raise FileNotFoundError("age-keygen")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GenerateVVAIKeyError, match="見つかりません"):
        _generate_age_key()


def test_generate_age_key_rejects_failed_age_keygen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_generate_age_key は age-keygen 失敗を専用エラーにする。"""

    def fake_run(
        cmd: list[str],
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="失敗しました\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GenerateVVAIKeyError, match="失敗しました"):
        _generate_age_key()


def test_main_outputs_generated_age_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main は生成した age 鍵を標準出力へ出す。"""
    monkeypatch.setattr(sys, "argv", ["generate-vv-ai-age-key"])
    monkeypatch.setattr(
        "tools.generate_vv_ai_age_key._generate_age_key",
        lambda: "Public key: age1example\nAGE-SECRET-KEY-1EXAMPLE\n",
    )

    main()

    captured = capsys.readouterr()
    assert captured.out == "Public key: age1example\nAGE-SECRET-KEY-1EXAMPLE\n"
    assert captured.err == ""


def test_main_exits_1_on_generate_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main は生成エラーを標準エラーへ出して終了コード 1 にする。"""
    monkeypatch.setattr(sys, "argv", ["generate-vv-ai-age-key"])

    def fake_generate_age_key() -> NoReturn:
        raise GenerateVVAIKeyError("age-keygen が見つかりません")

    monkeypatch.setattr(
        "tools.generate_vv_ai_age_key._generate_age_key",
        fake_generate_age_key,
    )

    with pytest.raises(SystemExit) as e:
        main()

    captured = capsys.readouterr()
    assert e.value.code == 1
    assert captured.out == ""
    assert captured.err == "エラー: age-keygen が見つかりません\n"
