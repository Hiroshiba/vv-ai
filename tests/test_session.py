"""session 解決ロジックの単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.backends.github.models import GitHubArtifact
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
from vv_ai.artifacts.session import (
    RestoredSessionArtifact,
    SessionArtifactError,
    SessionArtifactMeta,
    is_restored_session_artifact_resumable,
)


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


def _make_github_command() -> ResolvedCommand:
    """github backend でのテスト用 ResolvedCommand を返す。"""
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
                url="https://github.com/org/repo/issues/1",
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


def _make_github_key() -> SessionKey:
    """github backend 用の SessionKey を返す。"""
    return SessionKey(
        backend="github",
        target_key="org/repo#1",
        provider="codex",
        lane="main",
        canonical_key="github/org/repo#1/codex/main",
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


def _make_artifact(artifact_id: int, name: str) -> GitHubArtifact:
    """テスト用 GitHubArtifact を返す。"""
    return GitHubArtifact(
        id=artifact_id,
        name=name,
        created_at="2026-04-15T00:00:00Z",
        archive_download_url=f"https://example.test/{artifact_id}",
    )


def _make_restored_artifact(
    artifact_name: str,
    provider_session_id: str | None,
    provider_session_path: str | None,
) -> RestoredSessionArtifact:
    """テスト用 RestoredSessionArtifact を返す。"""
    return RestoredSessionArtifact(
        artifact_name=artifact_name,
        artifact_path=f"/tmp/{artifact_name}.tar.age",
        restored_dir=f"/tmp/{artifact_name}/session",
        provider_session_path=provider_session_path,
        meta=SessionArtifactMeta(
            workflow_id=artifact_name,
            saved_at="2026-04-15T00:00:00Z",
            session_key="github/org/repo#1/codex/main",
            provider="codex",
            lane="main",
            backend="github",
            target_key="org/repo#1",
            branch_name="main",
            head_sha="sha",
            provider_session_id=provider_session_id,
        ),
    )


class FakeGitHubClient:
    """remote artifact 復元テスト用の GitHub client。"""

    def __init__(self, artifacts: list[GitHubArtifact]) -> None:
        self.artifacts = artifacts
        self.downloaded_artifact_ids: list[int] = []
        self.list_called = False

    def list_repository_artifacts_by_prefix(
        self,
        repository_full_name: str,
        prefix: str,
    ) -> list[GitHubArtifact]:
        """事前に渡された artifact 一覧を返す。"""
        self.list_called = True
        return self.artifacts

    def download_repository_artifact(
        self,
        repository_full_name: str,
        artifact_id: int,
        destination_path: Path,
    ) -> None:
        """download 済み zip の代替 file を作る。"""
        self.downloaded_artifact_ids.append(artifact_id)
        destination_path.write_bytes(b"zip")


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
        build_github_client_func=lambda: FakeGitHubClient([]),
        is_restored_session_artifact_resumable=is_restored_session_artifact_resumable,
        cleanup_restored_session_artifact=lambda artifact: None,
    )


def _call_github(
    *,
    requested_mode,
    artifacts: list[GitHubArtifact],
    restored_artifacts: dict[str, RestoredSessionArtifact],
):
    """`_resolve_restore_state` を github backend で呼び出す。"""
    client = FakeGitHubClient(artifacts)
    cleaned_artifact_names: list[str] = []

    def restore_downloaded_session_artifact(
        repo_root: Path,
        workflow_id: str,
        artifact_name: str,
        downloaded_zip_path: Path,
        age_secret_key: str,
    ) -> RestoredSessionArtifact:
        return restored_artifacts[artifact_name]

    def cleanup_restored_session_artifact(
        artifact: RestoredSessionArtifact,
    ) -> None:
        cleaned_artifact_names.append(artifact.artifact_name)

    result = _resolve_restore_state(
        repo_root=Path("/tmp"),
        workflow_id="wf-new",
        resolved_command=_make_github_command(),
        key=_make_github_key(),
        requested_mode=requested_mode,
        resolved_provider=_make_provider(),
        env={"VV_AI_AGE_SECRET_KEY": "AGE-SECRET-KEY-1TEST"},
        load_latest_session_manifest=lambda repo, key: None,
        build_session_artifact_prefix=lambda key: "vv-ai-session__target__",
        restore_downloaded_session_artifact=restore_downloaded_session_artifact,
        build_github_client_func=lambda: client,
        is_restored_session_artifact_resumable=is_restored_session_artifact_resumable,
        cleanup_restored_session_artifact=cleanup_restored_session_artifact,
    )
    return result, client, cleaned_artifact_names


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

    def test_inherit_skips_artifact_without_provider_session_id(self) -> None:
        artifacts = [
            _make_artifact(2, "new-broken"),
            _make_artifact(1, "old-resumable"),
        ]
        restored_artifacts = {
            "new-broken": _make_restored_artifact(
                "new-broken",
                None,
                "/tmp/new-broken/session/provider-session",
            ),
            "old-resumable": _make_restored_artifact(
                "old-resumable",
                "old-session-id",
                "/tmp/old-resumable/session/provider-session",
            ),
        }

        (manifest, artifact, strategy), client, cleaned = _call_github(
            requested_mode="inherit",
            artifacts=artifacts,
            restored_artifacts=restored_artifacts,
        )

        assert manifest is not None
        assert artifact is not None
        assert manifest.workflow_id == "old-resumable"
        assert artifact.artifact_name == "old-resumable"
        assert strategy == "inherit"
        assert client.downloaded_artifact_ids == [2, 1]
        assert cleaned == ["new-broken"]

    def test_inherit_skips_artifact_without_provider_session_path(self) -> None:
        artifacts = [
            _make_artifact(2, "new-broken"),
            _make_artifact(1, "old-resumable"),
        ]
        restored_artifacts = {
            "new-broken": _make_restored_artifact(
                "new-broken",
                "new-session-id",
                None,
            ),
            "old-resumable": _make_restored_artifact(
                "old-resumable",
                "old-session-id",
                "/tmp/old-resumable/session/provider-session",
            ),
        }

        (manifest, artifact, strategy), client, cleaned = _call_github(
            requested_mode="inherit",
            artifacts=artifacts,
            restored_artifacts=restored_artifacts,
        )

        assert manifest is not None
        assert artifact is not None
        assert manifest.workflow_id == "old-resumable"
        assert artifact.artifact_name == "old-resumable"
        assert strategy == "inherit"
        assert client.downloaded_artifact_ids == [2, 1]
        assert cleaned == ["new-broken"]

    def test_compact_uses_resumable_artifact(self) -> None:
        artifacts = [_make_artifact(1, "resumable")]
        restored_artifacts = {
            "resumable": _make_restored_artifact(
                "resumable",
                "session-id",
                "/tmp/resumable/session/provider-session",
            ),
        }

        (manifest, artifact, strategy), client, cleaned = _call_github(
            requested_mode="compact",
            artifacts=artifacts,
            restored_artifacts=restored_artifacts,
        )

        assert manifest is not None
        assert artifact is not None
        assert manifest.workflow_id == "resumable"
        assert artifact.artifact_name == "resumable"
        assert strategy == "compact"
        assert client.downloaded_artifact_ids == [1]
        assert cleaned == []

    def test_inherit_or_new_starts_new_when_all_artifacts_are_broken(self) -> None:
        artifacts = [
            _make_artifact(2, "new-broken"),
            _make_artifact(1, "old-broken"),
        ]
        restored_artifacts = {
            "new-broken": _make_restored_artifact("new-broken", None, None),
            "old-broken": _make_restored_artifact("old-broken", None, None),
        }

        (manifest, artifact, strategy), client, cleaned = _call_github(
            requested_mode="inherit_or_new",
            artifacts=artifacts,
            restored_artifacts=restored_artifacts,
        )

        assert manifest is None
        assert artifact is None
        assert strategy == "new"
        assert client.downloaded_artifact_ids == [2, 1]
        assert cleaned == ["new-broken", "old-broken"]

    @pytest.mark.parametrize("requested_mode", ["inherit", "compact"])
    def test_strict_mode_raises_when_all_artifacts_are_broken(
        self,
        requested_mode,
    ) -> None:
        artifacts = [
            _make_artifact(2, "new-broken"),
            _make_artifact(1, "old-broken"),
        ]
        restored_artifacts = {
            "new-broken": _make_restored_artifact("new-broken", None, None),
            "old-broken": _make_restored_artifact("old-broken", None, None),
        }

        with pytest.raises(SessionResolutionError, match="session artifact"):
            _call_github(
                requested_mode=requested_mode,
                artifacts=artifacts,
                restored_artifacts=restored_artifacts,
            )

    def test_restore_error_propagates(self) -> None:
        client = FakeGitHubClient([_make_artifact(1, "broken")])

        def restore_downloaded_session_artifact(
            repo_root: Path,
            workflow_id: str,
            artifact_name: str,
            downloaded_zip_path: Path,
            age_secret_key: str,
        ) -> RestoredSessionArtifact:
            raise SessionArtifactError("artifact が壊れています")

        with pytest.raises(SessionArtifactError, match="壊れています"):
            _resolve_restore_state(
                repo_root=Path("/tmp"),
                workflow_id="wf-new",
                resolved_command=_make_github_command(),
                key=_make_github_key(),
                requested_mode="inherit_or_new",
                resolved_provider=_make_provider(),
                env={"VV_AI_AGE_SECRET_KEY": "AGE-SECRET-KEY-1TEST"},
                load_latest_session_manifest=lambda repo, key: None,
                build_session_artifact_prefix=lambda key: "vv-ai-session__target__",
                restore_downloaded_session_artifact=restore_downloaded_session_artifact,
                build_github_client_func=lambda: client,
                is_restored_session_artifact_resumable=(
                    is_restored_session_artifact_resumable
                ),
                cleanup_restored_session_artifact=lambda artifact: None,
            )

    def test_new_does_not_call_github_artifact_list(self) -> None:
        client = FakeGitHubClient([_make_artifact(1, "unused")])

        manifest, artifact, strategy = _resolve_restore_state(
            repo_root=Path("/tmp"),
            workflow_id="wf-new",
            resolved_command=_make_github_command(),
            key=_make_github_key(),
            requested_mode="new",
            resolved_provider=_make_provider(),
            env={},
            load_latest_session_manifest=lambda repo, key: None,
            build_session_artifact_prefix=lambda key: "vv-ai-session__target__",
            restore_downloaded_session_artifact=lambda *args: _make_restored_artifact(
                "unused",
                "unused-session-id",
                "/tmp/unused/session/provider-session",
            ),
            build_github_client_func=lambda: client,
            is_restored_session_artifact_resumable=(
                is_restored_session_artifact_resumable
            ),
            cleanup_restored_session_artifact=lambda artifact: None,
        )

        assert manifest is None
        assert artifact is None
        assert strategy == "new"
        assert client.list_called is False


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

    def test_address_uses_main_lane(self, tmp_path: Path) -> None:
        from vv_ai.session import resolve_session

        command = ResolvedCommand.model_validate(
            {
                "event_name": "local",
                "command": "address",
                "has_target": True,
                "session_mode": "new",
                "target": ResolvedTarget(
                    backend="local",
                    kind="pr",
                    canonical_id="pr:test",
                    local_id="test",
                    path=str(tmp_path / ".vv-ai/prs/test"),
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
        assert resolved.lane == "main"
        assert resolved.key.canonical_key == "local/pr:test/codex/main"


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
