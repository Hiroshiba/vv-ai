"""provider_execution の単体テスト。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from collections.abc import Sequence

from vv_ai.config import VVAIConfig
from vv_ai.preflight import ReadyExecution
from vv_ai.provider import ResolvedProvider, get_provider_spec
from vv_ai.provider_execution import (
    _build_codex_env,
    _execute_claude,
    _execute_codex,
)
from vv_ai.resolve import ResolvedCommand
from vv_ai.session import ResolvedSession, SessionKey


def _make_ready_execution(provider: str, session: ResolvedSession | None) -> ReadyExecution:
    """テスト用 ReadyExecution を生成する。"""
    return ReadyExecution(
        command=ResolvedCommand(
            event_name="local",
            command="reply",
            instruction="テスト",
            has_target=False,
            provider=provider,
            skip_api_key_check=True,
        ),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=ResolvedProvider(
            spec=get_provider_spec(provider),
            source="explicit",
        ),
        resolved_session=session,
        workflow_id="test-workflow",
    )


def _make_restored_session(provider: str, restored_path: Path) -> ResolvedSession:
    """復元済み provider session を持つ ResolvedSession を生成する。"""
    return ResolvedSession(
        requested_mode="new",
        lane="main",
        key=SessionKey(
            backend="local",
            target_key="issue:test",
            provider=provider,
            lane="main",
            canonical_key=f"local/issue:test/{provider}/main",
        ),
        restore_strategy="new",
        save_manifest_path="/tmp/session.json",
        restored_provider_session_path=str(restored_path),
    )


def test_build_codex_env_sets_codex_home_with_api_key_file(tmp_path: Path) -> None:
    """API key file 認証でも VV_CODEX_HOME を CODEX_HOME に反映する。"""
    key_file = tmp_path / "openai_key"
    key_file.write_text("key", encoding="utf-8")
    codex_home = tmp_path / "codex_home"

    env = _build_codex_env(
        {
            "VV_OPENAI_API_KEY_FILE": str(key_file),
            "VV_CODEX_HOME": str(codex_home),
        },
        skip_api_key_check=False,
    )

    assert env["OPENAI_API_KEY"] == "key"
    assert env["CODEX_HOME"] == str(codex_home)


def test_build_codex_env_does_not_set_codex_home_when_missing(tmp_path: Path) -> None:
    """VV_CODEX_HOME が無い場合は CODEX_HOME を追加しない。"""
    key_file = tmp_path / "openai_key"
    key_file.write_text("key", encoding="utf-8")

    env = _build_codex_env(
        {"VV_OPENAI_API_KEY_FILE": str(key_file)},
        skip_api_key_check=False,
    )

    assert "CODEX_HOME" not in env


def test_execute_codex_syncs_after_restore_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """_execute_codex は session restore 後、subprocess 前に asset 同期する。"""
    events: list[str] = []
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    ready_execution = _make_ready_execution(
        "codex",
        _make_restored_session("codex", restored_dir),
    )

    def fake_deploy(source: str, destination: Path) -> None:
        events.append("restore")

    def fake_sync(env, codex_home: Path) -> None:
        events.append("sync")

    def fake_run(
        command: Sequence[str],
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        events.append("run")
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text("Codex 応答", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("vv_ai.provider_execution._deploy_provider_session_dir", fake_deploy)
    monkeypatch.setattr("vv_ai.provider_execution._sync_codex_assets_before_execution", fake_sync)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _execute_codex(
        tmp_path,
        ready_execution,
        {"VV_OPENAI_API_KEY": "key", "VV_CODEX_HOME": str(tmp_path / "codex_home")},
        0.1,
        "prompt",
        skip_api_key_check=False,
    )

    assert events == ["restore", "sync", "run"]
    assert result.response_text == "Codex 応答"


def test_execute_claude_syncs_after_restore_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """_execute_claude は session restore 後、subprocess 前に asset 同期する。"""
    events: list[str] = []
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    ready_execution = _make_ready_execution(
        "claude",
        _make_restored_session("claude", restored_dir),
    )

    def fake_deploy(source: str, destination: Path) -> None:
        events.append("restore")

    def fake_sync(env, claude_home: Path) -> None:
        events.append("sync")

    def fake_run(
        command: Sequence[str],
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        events.append("run")
        payload = {
            "result": "Claude 応答",
            "session_id": "session-1",
            "duration_ms": 100,
            "num_turns": 1,
            "is_error": False,
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr("vv_ai.provider_execution._deploy_provider_session_dir", fake_deploy)
    monkeypatch.setattr("vv_ai.provider_execution._sync_claude_assets_before_execution", fake_sync)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _execute_claude(
        tmp_path,
        ready_execution,
        {},
        0.1,
        "prompt",
        skip_api_key_check=True,
    )

    assert events == ["restore", "sync", "run"]
    assert result.response_text == "Claude 応答"
