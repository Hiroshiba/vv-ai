"""session / artifact 保存復元の単体テスト。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vv_ai.artifact_crypto import (
    ArtifactCryptoError,
    resolve_age_public_key,
    resolve_age_secret_key,
)
from vv_ai.metrics_artifact import build_metrics_artifact_name
from vv_ai.report_artifact import (
    ReportSections,
    build_report_artifact_name,
    render_report_markdown,
)
from vv_ai.resolve import BackendName
from vv_ai.session import (
    SavedSessionManifest,
    SessionKey,
    SessionLane,
    SessionStateRef,
    TargetContextState,
)
from vv_ai.session_artifact import (
    SessionArtifactError,
    SessionArtifactMeta,
    build_session_artifact_name,
    build_session_artifact_prefix,
    load_session_artifact_meta,
)
from vv_ai.session_store import (
    build_session_manifest_filename,
    list_session_manifests,
    load_latest_session_manifest,
    save_session_manifest,
)


def _make_session_key(
    backend: BackendName,
    target_key: str,
    provider: str,
    lane: SessionLane,
) -> SessionKey:
    """テスト用の最小 SessionKey を生成する。"""
    return SessionKey.model_validate(
        {
            "backend": backend,
            "target_key": target_key,
            "provider": provider,
            "lane": lane,
            "canonical_key": f"{backend}/{target_key}/{provider}/{lane}",
        }
    )


def _make_empty_state_ref() -> SessionStateRef:
    """全フィールド None の SessionStateRef を生成する。"""
    return SessionStateRef(
        provider_session_id=None,
        summary_path=None,
        artifact_hint=None,
    )


class TestBuildSessionArtifactName:
    def test_basic_name(self) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        result = build_session_artifact_name(key, "run-100")
        assert result == "vv-ai-session__org-repo-1__codex__main__run-100"

    def test_special_characters_sanitized(self) -> None:
        key = _make_session_key("local", "issue:login-403-7k2p9a", "claude", "review")
        result = build_session_artifact_name(key, "20260101-120000-abc")
        assert ":" not in result
        assert result.startswith("vv-ai-session__")


class TestBuildSessionArtifactPrefix:
    def test_prefix_ends_with_double_underscore(self) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        prefix = build_session_artifact_prefix(key)
        assert prefix.endswith("__")

    def test_prefix_matches_name_start(self) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        prefix = build_session_artifact_prefix(key)
        name = build_session_artifact_name(key, "run-100")
        assert name.startswith(prefix)


class TestBuildSessionManifestFilename:
    def test_filename_ends_with_json(self) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        filename = build_session_manifest_filename(key)
        assert filename.endswith(".json")

    def test_filename_contains_sha1_digest(self) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        filename = build_session_manifest_filename(key)
        expected_digest = hashlib.sha1(
            key.canonical_key.encode("utf-8")
        ).hexdigest()[:12]
        assert expected_digest in filename

    def test_unsafe_characters_replaced(self) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        filename = build_session_manifest_filename(key)
        name_without_ext = filename.removesuffix(".json")
        assert "/" not in name_without_ext
        assert "#" not in name_without_ext


class TestBuildArtifactNames:
    def test_different_prefixes_same_key(self) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        workflow_id = "run-100"
        session_name = build_session_artifact_name(key, workflow_id)
        report_name = build_report_artifact_name(key, workflow_id)
        metrics_name = build_metrics_artifact_name(key, workflow_id)
        assert session_name.startswith("vv-ai-session__")
        assert report_name.startswith("vv-ai-report__")
        assert metrics_name.startswith("vv-ai-metrics__")
        assert len({session_name, report_name, metrics_name}) == 3


class TestRenderReportMarkdown:
    def test_contains_all_section_headers(self) -> None:
        sections = ReportSections(
            summary="s",
            changes="c",
            decisions="d",
            validation="v",
            risks_open_questions="r",
            next_actions="n",
            notes="t",
        )
        md = render_report_markdown(sections)
        for header in [
            "# Report",
            "## Summary",
            "## Changes",
            "## Decisions",
            "## Validation",
            "## Risks / Open Questions",
            "## Next Actions",
            "## Notes",
        ]:
            assert header in md

    def test_section_content_included(self) -> None:
        sections = ReportSections(
            summary="summary text here",
            changes="changes text here",
            decisions="decisions text here",
            validation="validation text here",
            risks_open_questions="risks text here",
            next_actions="next actions text here",
            notes="notes text here",
        )
        md = render_report_markdown(sections)
        assert "summary text here" in md
        assert "notes text here" in md


class TestResolveAgeKey:
    def test_public_key_from_env_value(self) -> None:
        env = {"VV_AI_AGE_PUBLIC_KEY": "age1testkey123"}
        assert resolve_age_public_key(env) == "age1testkey123"

    def test_public_key_from_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "pub.key"
        key_file.write_text("age1fromfile456\n")
        env = {"VV_AI_AGE_PUBLIC_KEY_FILE": str(key_file)}
        assert resolve_age_public_key(env) == "age1fromfile456"

    def test_file_takes_priority_over_value(self, tmp_path: Path) -> None:
        key_file = tmp_path / "pub.key"
        key_file.write_text("age1file\n")
        env = {
            "VV_AI_AGE_PUBLIC_KEY_FILE": str(key_file),
            "VV_AI_AGE_PUBLIC_KEY": "age1value",
        }
        assert resolve_age_public_key(env) == "age1file"

    def test_missing_both_raises(self) -> None:
        with pytest.raises(ArtifactCryptoError, match="環境変数"):
            resolve_age_public_key({})

    def test_empty_value_raises(self) -> None:
        env = {"VV_AI_AGE_PUBLIC_KEY": "  "}
        with pytest.raises(ArtifactCryptoError, match="空"):
            resolve_age_public_key(env)

    def test_secret_key_from_env_value(self) -> None:
        env = {"VV_AI_AGE_SECRET_KEY": "AGE-SECRET-KEY-1TEST"}
        assert resolve_age_secret_key(env) == "AGE-SECRET-KEY-1TEST"


class TestLoadSessionArtifactMeta:
    def test_load_valid_meta(self, tmp_path: Path) -> None:
        meta_data = {
            "schema_version": 1,
            "workflow_id": "run-100",
            "saved_at": "2026-01-01T00:00:00Z",
            "session_key": "github/org/repo#1/codex/main",
            "provider": "codex",
            "lane": "main",
            "backend": "github",
            "target_key": "org/repo#1",
            "branch_name": "main",
            "head_sha": "abc123",
        }
        meta_path = tmp_path / "meta.json"
        meta_path.write_text(json.dumps(meta_data), encoding="utf-8")
        meta = load_session_artifact_meta(tmp_path)
        assert meta.workflow_id == "run-100"
        assert meta.provider == "codex"

    def test_missing_meta_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SessionArtifactError, match="見つかりません"):
            load_session_artifact_meta(tmp_path)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "meta.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(SessionArtifactError, match="JSON"):
            load_session_artifact_meta(tmp_path)

    def test_validation_error_raises(self, tmp_path: Path) -> None:
        (tmp_path / "meta.json").write_text(
            json.dumps({"workflow_id": "x"}), encoding="utf-8"
        )
        with pytest.raises(SessionArtifactError, match="不正"):
            load_session_artifact_meta(tmp_path)


class TestSaveAndListSessionManifest:
    def test_save_creates_json_file(self, tmp_path: Path) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        ref = _make_empty_state_ref()
        path = save_session_manifest(
            tmp_path,
            "run-100",
            key,
            ref,
            saved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert path.exists()
        assert path.suffix == ".json"
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = SavedSessionManifest.model_validate(data)
        assert manifest.workflow_id == "run-100"

    def test_save_keeps_target_context_state(self, tmp_path: Path) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        ref = SessionStateRef(
            target_context_state=TargetContextState(
                title_hash="title",
                description_hash="description",
                comment_hashes={"1": "comment"},
            )
        )
        path = save_session_manifest(
            tmp_path,
            "run-100",
            key,
            ref,
            saved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = SavedSessionManifest.model_validate(data)

        assert manifest.state_ref.target_context_state == ref.target_context_state

    def test_list_returns_saved_manifests(self, tmp_path: Path) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        ref = _make_empty_state_ref()
        save_session_manifest(
            tmp_path,
            "run-100",
            key,
            ref,
            saved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        results = list_session_manifests(tmp_path, key)
        assert len(results) == 1
        assert results[0].workflow_id == "run-100"

    def test_list_filters_by_session_key(self, tmp_path: Path) -> None:
        key_a = _make_session_key("github", "org/repo#1", "codex", "main")
        key_b = _make_session_key("github", "org/repo#2", "codex", "main")
        ref = _make_empty_state_ref()
        save_session_manifest(
            tmp_path, "run-1", key_a, ref, saved_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        save_session_manifest(
            tmp_path, "run-2", key_b, ref, saved_at=datetime(2026, 1, 2, tzinfo=UTC)
        )
        results = list_session_manifests(tmp_path, key_a)
        assert len(results) == 1
        assert results[0].session_key == key_a.canonical_key

    def test_list_sorted_newest_first(self, tmp_path: Path) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        ref = _make_empty_state_ref()
        save_session_manifest(
            tmp_path, "run-1", key, ref, saved_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        save_session_manifest(
            tmp_path, "run-2", key, ref, saved_at=datetime(2026, 6, 1, tzinfo=UTC)
        )
        results = list_session_manifests(tmp_path, key)
        assert len(results) == 2
        assert results[0].workflow_id == "run-2"
        assert results[1].workflow_id == "run-1"

    def test_load_latest_returns_newest(self, tmp_path: Path) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        ref = _make_empty_state_ref()
        save_session_manifest(
            tmp_path, "run-old", key, ref, saved_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        save_session_manifest(
            tmp_path, "run-new", key, ref, saved_at=datetime(2026, 6, 1, tzinfo=UTC)
        )
        latest = load_latest_session_manifest(tmp_path, key)
        assert latest is not None
        assert latest.workflow_id == "run-new"

    def test_load_latest_returns_none_when_empty(self, tmp_path: Path) -> None:
        key = _make_session_key("github", "org/repo#1", "codex", "main")
        assert load_latest_session_manifest(tmp_path, key) is None
