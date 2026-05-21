"""provider 実行の単体テスト。"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.workflow.preflight import ReadyExecution
from vv_ai.providers.claude import execute_claude as _execute_claude
from vv_ai.providers.codex import (
    _build_codex_provider_prompt,
    execute_codex as _execute_codex,
)
from vv_ai.providers.environment import build_codex_env as _build_codex_env
from vv_ai.providers.runner import ProviderExecutionError
from vv_ai.providers.selection import ResolvedProvider, get_provider_spec
from vv_ai.providers.sessions import (
    deploy_codex_session_dir as _deploy_codex_session_dir,
    resolve_codex_session_dir as _resolve_codex_session_dir,
)
from vv_ai.inputs.resolve import ResolvedCommand
from vv_ai.sessions.models import ResolvedSession, SessionKey


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


def test_execute_codex_deploys_after_restore_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """_execute_codex は restore 後に asset と作業用ディレクトリを準備する。"""
    events: list[str] = []
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    ready_execution = _make_ready_execution(
        "codex",
        _make_restored_session("codex", restored_dir),
    )

    def fake_deploy(source: str, destination: Path) -> None:
        events.append("restore")

    def fake_deploy_assets(env, codex_home: Path) -> None:
        events.append("deploy")

    def fake_prepare_work(repo_root: Path) -> Path:
        events.append("prepare_work")
        return repo_root / ".vv-ai" / "codex-work"

    def fake_sync_work(repo_root: Path, work_dir: Path) -> None:
        events.append("sync_work")

    def fake_run(
        command: Sequence[str],
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        events.append("run")
        assert ".vv-ai/codex-work" in command[-1]
        session_path = Path(env["CODEX_HOME"]) / "sessions" / "2026" / "05" / "15"
        session_path.mkdir(parents=True)
        (session_path / "rollout.jsonl").write_text("{}", encoding="utf-8")
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text("Codex 応答", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("vv_ai.providers.codex.deploy_codex_session_dir", fake_deploy)
    monkeypatch.setattr(
        "vv_ai.providers.codex._deploy_codex_assets_before_execution",
        fake_deploy_assets,
    )
    monkeypatch.setattr(
        "vv_ai.providers.codex._prepare_codex_work_dir",
        fake_prepare_work,
    )
    monkeypatch.setattr("vv_ai.providers.codex._sync_codex_work_dir", fake_sync_work)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = None
    try:
        result = _execute_codex(
            tmp_path,
            ready_execution,
            {
                "VV_OPENAI_API_KEY": "key",
                "VV_CODEX_HOME": str(tmp_path / "codex_home"),
            },
            0.1,
            "prompt",
            skip_api_key_check=False,
        )

        assert events == ["restore", "deploy", "prepare_work", "run", "sync_work"]
        assert result.response_text == "Codex 応答"
    finally:
        if result is not None and result.provider_session_path is not None:
            shutil.rmtree(result.provider_session_path, ignore_errors=True)


def test_execute_codex_does_not_sync_work_dir_when_subprocess_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Codex subprocess が失敗した場合は作業用ディレクトリを同期しない。"""
    events: list[str] = []
    ready_execution = _make_ready_execution("codex", None)

    def fake_deploy_assets(env, codex_home: Path) -> None:
        events.append("deploy")
        codex_home.mkdir(parents=True)

    def fake_prepare_work(repo_root: Path) -> Path:
        events.append("prepare_work")
        return repo_root / ".vv-ai" / "codex-work"

    def fake_sync_work(repo_root: Path, work_dir: Path) -> None:
        events.append("sync_work")

    def fake_run(
        command: Sequence[str],
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        events.append("run")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

    monkeypatch.setattr(
        "vv_ai.providers.codex._deploy_codex_assets_before_execution",
        fake_deploy_assets,
    )
    monkeypatch.setattr(
        "vv_ai.providers.codex._prepare_codex_work_dir",
        fake_prepare_work,
    )
    monkeypatch.setattr("vv_ai.providers.codex._sync_codex_work_dir", fake_sync_work)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderExecutionError, match="終了コード 1"):
        _execute_codex(
            tmp_path,
            ready_execution,
            {
                "VV_OPENAI_API_KEY": "key",
                "VV_CODEX_HOME": str(tmp_path / "codex_home"),
            },
            0.1,
            "prompt",
            skip_api_key_check=False,
        )

    assert events == ["deploy", "prepare_work", "run"]


def test_execute_codex_does_not_sync_work_dir_when_session_resolve_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Codex session 保存に失敗した場合は作業用ディレクトリを同期しない。"""
    events: list[str] = []
    ready_execution = _make_ready_execution("codex", None)

    def fake_deploy_assets(env, codex_home: Path) -> None:
        events.append("deploy")
        codex_home.mkdir(parents=True)

    def fake_prepare_work(repo_root: Path) -> Path:
        events.append("prepare_work")
        return repo_root / ".vv-ai" / "codex-work"

    def fake_sync_work(repo_root: Path, work_dir: Path) -> None:
        events.append("sync_work")

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

    monkeypatch.setattr(
        "vv_ai.providers.codex._deploy_codex_assets_before_execution",
        fake_deploy_assets,
    )
    monkeypatch.setattr(
        "vv_ai.providers.codex._prepare_codex_work_dir",
        fake_prepare_work,
    )
    monkeypatch.setattr("vv_ai.providers.codex._sync_codex_work_dir", fake_sync_work)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderExecutionError, match="Codex session directory"):
        _execute_codex(
            tmp_path,
            ready_execution,
            {
                "VV_OPENAI_API_KEY": "key",
                "VV_CODEX_HOME": str(tmp_path / "codex_home"),
            },
            0.1,
            "prompt",
            skip_api_key_check=False,
        )

    assert events == ["deploy", "prepare_work", "run"]


def test_build_codex_provider_prompt_mentions_work_dir() -> None:
    """Codex provider prompt は作業用ディレクトリの編集指示を含む。"""
    prompt = _build_codex_provider_prompt(
        "元の指示",
        Path(".vv-ai/codex-work"),
    )

    assert "元の指示" in prompt
    assert ".codex/" in prompt
    assert ".vv-ai/codex-work/" in prompt
    assert "AGENTS.md" in prompt
    assert "skills/" in prompt
    assert "agents/" in prompt
    assert "直接編集しない" in prompt
    assert "作業用 mirror" in prompt
    assert "git に追加しなくて大丈夫です" in prompt
    assert "未追跡" not in prompt
    assert ".gitignore" not in prompt


def test_resolve_codex_session_dir_copies_only_sessions(tmp_path: Path) -> None:
    """Codex session 収集は CODEX_HOME/sessions だけを保存する。"""
    codex_home = tmp_path / "codex_home"
    session_file = codex_home / "sessions" / "2026" / "05" / "15" / "rollout.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("{}", encoding="utf-8")
    (codex_home / "AGENTS.md").write_text("agents", encoding="utf-8")
    (codex_home / "skills" / "skill").mkdir(parents=True)
    (codex_home / "agents").mkdir()
    (codex_home / "plugins").mkdir()
    (codex_home / "cache").mkdir()
    (codex_home / ".tmp").mkdir()
    (codex_home / "tmp").mkdir()
    (codex_home / "shell_snapshots").mkdir()
    for filename in [
        "auth.json",
        "config.toml",
        "logs_2.sqlite",
        "state_5.sqlite",
        "models_cache.json",
    ]:
        (codex_home / filename).write_text(filename, encoding="utf-8")

    result = _resolve_codex_session_dir({"CODEX_HOME": str(codex_home)})
    try:
        assert sorted(path.name for path in result.iterdir()) == ["sessions"]
        assert (
            result / "sessions" / "2026" / "05" / "15" / "rollout.jsonl"
        ).read_text(encoding="utf-8") == "{}"
    finally:
        shutil.rmtree(result, ignore_errors=True)


def test_resolve_codex_session_dir_raises_when_sessions_missing(tmp_path: Path) -> None:
    """Codex sessions が無い場合は保存不能として失敗する。"""
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    with pytest.raises(ProviderExecutionError, match="Codex session directory"):
        _resolve_codex_session_dir({"CODEX_HOME": str(codex_home)})


def test_deploy_codex_session_dir_replaces_only_sessions(tmp_path: Path) -> None:
    """Codex session 復元は sessions だけを置き換える。"""
    source = tmp_path / "source"
    (source / "sessions").mkdir(parents=True)
    (source / "sessions" / "new.jsonl").write_text("new", encoding="utf-8")
    (source / "AGENTS.md").write_text("old agents", encoding="utf-8")
    codex_home = tmp_path / "codex_home"
    (codex_home / "sessions").mkdir(parents=True)
    (codex_home / "sessions" / "old.jsonl").write_text("old", encoding="utf-8")
    (codex_home / "AGENTS.md").write_text("agents", encoding="utf-8")
    (codex_home / "skills" / "skill").mkdir(parents=True)
    (codex_home / "skills" / "skill" / "SKILL.md").write_text(
        "skill", encoding="utf-8"
    )

    _deploy_codex_session_dir(str(source), codex_home)

    assert not (codex_home / "sessions" / "old.jsonl").exists()
    assert (
        codex_home / "sessions" / "new.jsonl"
    ).read_text(encoding="utf-8") == "new"
    assert (codex_home / "AGENTS.md").read_text(encoding="utf-8") == "agents"
    assert (
        codex_home / "skills" / "skill" / "SKILL.md"
    ).read_text(encoding="utf-8") == "skill"


def test_deploy_codex_session_dir_raises_when_source_sessions_missing(tmp_path: Path) -> None:
    """復元元に Codex sessions が無い場合は失敗する。"""
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ProviderExecutionError, match="Codex session directory"):
        _deploy_codex_session_dir(str(source), tmp_path / "codex_home")


def test_execute_claude_deploys_after_restore_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """_execute_claude は session restore 後、subprocess 前に asset 配置する。"""
    events: list[str] = []
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    ready_execution = _make_ready_execution(
        "claude",
        _make_restored_session("claude", restored_dir),
    )

    def fake_deploy(source: str, destination: Path) -> None:
        events.append("restore")

    def fake_deploy_assets(env, claude_home: Path) -> None:
        events.append("deploy")

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

    monkeypatch.setattr(
        "vv_ai.providers.claude.deploy_provider_session_dir", fake_deploy
    )
    monkeypatch.setattr(
        "vv_ai.providers.claude._deploy_claude_assets_before_execution",
        fake_deploy_assets,
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _execute_claude(
        tmp_path,
        ready_execution,
        {},
        0.1,
        "prompt",
        skip_api_key_check=True,
    )

    assert events == ["restore", "deploy", "run"]
    assert result.response_text == "Claude 応答"
