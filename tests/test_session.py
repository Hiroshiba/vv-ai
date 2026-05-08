"""session 解決ロジックの単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.provider import ProviderSpec, ResolvedProvider
from vv_ai.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.session import (
    SavedSessionManifest,
    SessionKey,
    SessionResolutionError,
    SessionStateRef,
    TargetContextState,
    _build_manifest_from_restored_artifact,
    _resolve_restore_state,
)
from vv_ai.session_artifact import RestoredSessionArtifact, SessionArtifactMeta


def _make_command() -> ResolvedCommand:
    """local backend でのテスト用 ResolvedCommand を返す。"""
    return ResolvedCommand.model_validate(
        {
            "event_name": "local",
            "command": "arch",
            "has_target": True,
            "target": ResolvedTarget(
                backend="local",
                kind="issue",
                canonical_id="issue:test",
                local_id="test",
                path=".vv-ai/issues/test",
            ),
        }
    )


def _make_provider() -> ResolvedProvider:
    """session 継続可能な ResolvedProvider を返す。"""
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


def _make_key() -> SessionKey:
    """local backend 用の SessionKey を返す。"""
    return SessionKey(
        backend="local",
        target_key="issue:test",
        provider="codex",
        lane="main",
        canonical_key="local/issue:test/codex/main",
    )


def _make_manifest(*, with_session_id: bool) -> SavedSessionManifest:
    """テスト用 SavedSessionManifest を返す。"""
    return SavedSessionManifest(
        workflow_id="wf-old",
        saved_at="2026-04-15T00:00:00Z",
        session_key="local/issue:test/codex/main",
        provider="codex",
        lane="main",
        backend="local",
        target_key="issue:test",
        state_ref=SessionStateRef(
            provider_session_id="old-session-id" if with_session_id else None,
        ),
    )


def _call(
    *,
    requested_mode,
    manifest,
):
    """`_resolve_restore_state` を local backend でシンプルに呼び出す。"""
    return _resolve_restore_state(
        repo_root=Path("/tmp"),
        workflow_id="wf-new",
        resolved_command=_make_command(),
        key=_make_key(),
        requested_mode=requested_mode,
        resolved_provider=_make_provider(),
        env={},
        load_latest_session_manifest=lambda repo, key: manifest,
        build_session_artifact_prefix=lambda key: "",
        restore_downloaded_session_artifact=lambda *a, **kw: None,
    )


class TestResolveRestoreState:
    def test_new_short_circuits(self) -> None:
        manifest, artifact, strategy = _call(
            requested_mode="new",
            manifest=_make_manifest(with_session_id=True),
        )
        assert manifest is None
        assert artifact is None
        assert strategy == "new"

    def test_inherit_with_manifest(self) -> None:
        saved = _make_manifest(with_session_id=True)
        manifest, artifact, strategy = _call(
            requested_mode="inherit",
            manifest=saved,
        )
        assert manifest is saved
        assert artifact is None
        assert strategy == "inherit"

    def test_inherit_missing_manifest_raises(self) -> None:
        with pytest.raises(SessionResolutionError, match="inherit"):
            _call(requested_mode="inherit", manifest=None)

    def test_compact_with_manifest(self) -> None:
        saved = _make_manifest(with_session_id=True)
        manifest, artifact, strategy = _call(
            requested_mode="compact",
            manifest=saved,
        )
        assert manifest is saved
        assert strategy == "compact"

    def test_compact_missing_manifest_raises(self) -> None:
        with pytest.raises(SessionResolutionError, match="compact"):
            _call(requested_mode="compact", manifest=None)

    def test_inherit_or_new_without_manifest_falls_back(self) -> None:
        manifest, artifact, strategy = _call(
            requested_mode="inherit_or_new",
            manifest=None,
        )
        assert manifest is None
        assert artifact is None
        assert strategy == "new"

    def test_inherit_or_new_with_manifest_inherits(self) -> None:
        saved = _make_manifest(with_session_id=True)
        manifest, artifact, strategy = _call(
            requested_mode="inherit_or_new",
            manifest=saved,
        )
        assert manifest is saved
        assert strategy == "inherit"

    def test_inherit_or_new_with_broken_manifest_raises(self) -> None:
        broken = _make_manifest(with_session_id=False)
        with pytest.raises(SessionResolutionError, match="provider_session_id"):
            _call(requested_mode="inherit_or_new", manifest=broken)


class TestResolveSessionDefault:
    def test_missing_session_mode_defaults_to_inherit_or_new(
        self, tmp_path: Path
    ) -> None:
        from vv_ai.session import resolve_session

        command = ResolvedCommand.model_validate(
            {
                "event_name": "local",
                "command": "arch",
                "has_target": True,
                "session_mode": None,
                "target": ResolvedTarget(
                    backend="local",
                    kind="issue",
                    canonical_id="issue:test",
                    local_id="test",
                    path=str(tmp_path / ".vv-ai/issues/test"),
                ),
            }
        )
        resolved = resolve_session(
            repo_root=tmp_path,
            workflow_id="wf-test",
            resolved_command=command,
            resolved_provider=_make_provider(),
            env={},
        )
        assert resolved.requested_mode == "inherit_or_new"
        assert resolved.restore_strategy == "new"
        assert resolved.restore_manifest is None


def test_build_manifest_from_restored_artifact_keeps_target_context_state() -> None:
    state = TargetContextState(
        title_hash="title",
        description_hash="description",
        comment_hashes={"1": "comment"},
    )
    artifact = RestoredSessionArtifact(
        artifact_name="artifact",
        artifact_path="/tmp/artifact.tar.age",
        restored_dir="/tmp/restored",
        provider_session_path=None,
        meta=SessionArtifactMeta(
            workflow_id="wf-old",
            saved_at="2026-04-15T00:00:00Z",
            session_key="github/org/repo#1/codex/main",
            provider="codex",
            lane="main",
            backend="github",
            target_key="org/repo#1",
            branch_name="main",
            head_sha="sha",
            provider_session_id="session-id",
            target_context_state=state,
        ),
    )

    manifest = _build_manifest_from_restored_artifact(artifact)

    assert manifest.state_ref.target_context_state == state
