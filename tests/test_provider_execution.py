"""provider CLI 実行コマンド生成の単体テスト。"""

from __future__ import annotations

from pathlib import Path

from vv_ai.config import VVAIConfig
from vv_ai.preflight import ReadyExecution
from vv_ai.provider import ProviderSpec, ResolvedProvider
from vv_ai.provider_execution import _build_claude_command, _build_codex_command
from vv_ai.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.session import ResolvedSession, SessionKey, SessionStateRef


def _make_command() -> ResolvedCommand:
    """テスト用の最小 ResolvedCommand を生成する。"""
    return ResolvedCommand(
        event_name="local",
        command="arch",
        has_target=True,
        target=ResolvedTarget(
            backend="github",
            kind="issue",
            canonical_id="org/repo#1",
            repository_full_name="org/repo",
            number=1,
        ),
    )


def _make_provider() -> ResolvedProvider:
    """テスト用の最小 ResolvedProvider を生成する。"""
    return ResolvedProvider(
        spec=ProviderSpec(
            name="codex",
            api_key_env="VV_OPENAI_API_KEY",
            api_key_file_env="VV_OPENAI_API_KEY_FILE",
            auth_home_env="VV_CODEX_HOME",
            cli_command="codex",
            supports_session_resume=True,
            supports_compact=True,
        ),
        source="explicit",
    )


def _make_session_key() -> SessionKey:
    """テスト用の最小 SessionKey を生成する。"""
    return SessionKey(
        backend="github",
        target_key="org/repo#1",
        provider="codex",
        lane="main",
        canonical_key="github/org/repo#1/codex/main",
    )


def _make_session(state_ref: SessionStateRef | None) -> ResolvedSession:
    """テスト用の ResolvedSession を生成する。"""
    restore_strategy = "inherit" if state_ref is not None else "new"
    return ResolvedSession(
        requested_mode="inherit" if state_ref is not None else "new",
        lane="main",
        key=_make_session_key(),
        restore_strategy=restore_strategy,
        save_manifest_path="/tmp/manifest.json",
        state_ref=state_ref,
    )


def _make_ready_execution(state_ref: SessionStateRef | None) -> ReadyExecution:
    """テスト用の ReadyExecution を生成する。"""
    return ReadyExecution(
        command=_make_command(),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        resolved_session=_make_session(state_ref),
        workflow_id="test-run-1",
    )


def test_codex_new_command_disables_web_search(tmp_path: Path) -> None:
    """Codex 新規 session の web search feature を引数で無効化する。"""
    command = _build_codex_command(
        _make_ready_execution(None),
        tmp_path / "output.txt",
        "prompt",
    )

    assert "--search" not in command
    assert command.count("--disable") == 2
    assert "web_search_request" in command
    assert "web_search_cached" in command


def test_codex_resume_command_disables_web_search(tmp_path: Path) -> None:
    """Codex resume session の web search feature を引数で無効化する。"""
    command = _build_codex_command(
        _make_ready_execution(SessionStateRef(provider_session_id="session-1")),
        tmp_path / "output.txt",
        "prompt",
    )

    assert command[:4] == ["codex", "exec", "resume", "session-1"]
    assert command.count("--disable") == 2
    assert "web_search_request" in command
    assert "web_search_cached" in command


def test_claude_command_disallows_web_search() -> None:
    """Claude Code の WebSearch tool を引数で禁止する。"""
    command = _build_claude_command(
        _make_ready_execution(None),
        None,
        None,
        "prompt",
    )

    assert "--disallowedTools" in command
    assert command[command.index("--disallowedTools") + 1] == "WebSearch"
    assert "WebFetch" not in command
