"""provider 実行コマンド生成の単体テスト。"""

from __future__ import annotations

from pathlib import Path

from vv_ai.config import VVAIConfig
from vv_ai.preflight import ReadyExecution
from vv_ai.provider import ProviderSpec, ResolvedProvider
from vv_ai.provider_execution import _build_codex_command
from vv_ai.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.session import ResolvedSession, SessionKey, SessionStateRef


def _make_command() -> ResolvedCommand:
    """テスト用の最小 ResolvedCommand を生成する。"""
    return ResolvedCommand.model_validate(
        {
            "event_name": "local",
            "command": "arch",
            "has_target": True,
            "target": ResolvedTarget(
                backend="github",
                kind="issue",
                canonical_id="org/repo#1",
                repository_full_name="org/repo",
                number=1,
            ),
        }
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


def _make_resolved_session(
    restore_strategy: str,
    provider_session_id: str | None,
) -> ResolvedSession:
    """テスト用の最小 ResolvedSession を生成する。"""
    return ResolvedSession.model_validate(
        {
            "requested_mode": restore_strategy,
            "lane": "main",
            "key": _make_session_key(),
            "restore_strategy": restore_strategy,
            "save_manifest_path": "/tmp/manifest.json",
            "state_ref": SessionStateRef(provider_session_id=provider_session_id),
        }
    )


def _make_ready_execution(
    resolved_session: ResolvedSession | None,
) -> ReadyExecution:
    """テスト用の最小 ReadyExecution を生成する。"""
    return ReadyExecution(
        command=_make_command(),
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=_make_provider(),
        resolved_session=resolved_session,
        workflow_id="test-run-1",
    )


def _assert_reasoning_high(command: list[str]) -> None:
    """Codex command に reasoning high 指定があることを確認する。"""
    option_index = command.index("model_reasoning_effort=high")
    assert command[option_index - 1] == "-c"


class TestBuildCodexCommand:
    """Codex command の生成テスト。"""

    def test_new_session_sets_reasoning_high(self) -> None:
        ready_execution = _make_ready_execution(resolved_session=None)

        command = _build_codex_command(
            ready_execution,
            Path("/tmp/result.txt"),
            "作業して",
        )

        assert command[:2] == ["codex", "exec"]
        _assert_reasoning_high(command)

    def test_resume_session_sets_reasoning_high(self) -> None:
        ready_execution = _make_ready_execution(
            resolved_session=_make_resolved_session("inherit", "session-1"),
        )

        command = _build_codex_command(
            ready_execution,
            Path("/tmp/result.txt"),
            "作業して",
        )

        assert command[:4] == ["codex", "exec", "resume", "session-1"]
        _assert_reasoning_high(command)
