"""target 解決と backend 判定の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.inputs.resolve import ResolvedCommand, ResolvedControlLabel
from vv_ai.targets.resolve import (
    TargetResolutionError,
    resolve_control_label_target,
    resolve_target,
)


def _make_command(**overrides: object) -> ResolvedCommand:
    """テスト用の最小 ResolvedCommand を生成する。"""
    defaults: dict[str, object] = {
        "event_name": "local",
        "command": "arch",
        "has_target": True,
    }
    defaults.update(overrides)
    return ResolvedCommand.model_validate(defaults)


def _make_control_label(**overrides: object) -> ResolvedControlLabel:
    """テスト用の最小 ResolvedControlLabel を生成する。"""
    defaults: dict[str, object] = {
        "event_name": "issues",
        "control_label_name": "vv-ai:auto",
        "label_action": "labeled",
        "target_type": "issue",
        "target_number": 42,
        "has_target": True,
        "repository_full_name": "org/repo",
        "actor": "Hiroshiba",
        "trigger_label_name": "vv-ai:auto",
        "trigger_event_created_at": "2026-05-18T04:00:00Z",
    }
    defaults.update(overrides)
    return ResolvedControlLabel.model_validate(defaults)


class TestGitHubTargetFromUrl:
    def test_issue_url(self) -> None:
        cmd = _make_command(target_url="https://github.com/org/repo/issues/42")
        result = resolve_target(Path("/dummy"), cmd)
        assert result.target is not None
        target = result.target
        assert target.backend == "github"
        assert target.kind == "issue"
        assert target.canonical_id == "org/repo#42"
        assert target.repository_full_name == "org/repo"
        assert target.number == 42
        assert target.url == "https://github.com/org/repo/issues/42"

    def test_pr_url(self) -> None:
        cmd = _make_command(target_url="https://github.com/org/repo/pull/10")
        result = resolve_target(Path("/dummy"), cmd)
        assert result.target is not None
        target = result.target
        assert target.backend == "github"
        assert target.kind == "pr"
        assert target.canonical_id == "org/repo#10"
        assert target.url == "https://github.com/org/repo/pull/10"

    def test_sync_rejects_issue_url(self) -> None:
        cmd = _make_command(
            command="sync",
            target_url="https://github.com/org/repo/issues/42",
        )
        with pytest.raises(TargetResolutionError, match="PR 専用"):
            resolve_target(Path("/dummy"), cmd)

    def test_invalid_path_format(self) -> None:
        cmd = _make_command(target_url="https://github.com/org/repo")
        with pytest.raises(TargetResolutionError, match="形式"):
            resolve_target(Path("/dummy"), cmd)

    def test_invalid_kind(self) -> None:
        cmd = _make_command(target_url="https://github.com/org/repo/wiki/42")
        with pytest.raises(TargetResolutionError, match="Issue または PR"):
            resolve_target(Path("/dummy"), cmd)

    def test_non_numeric_number(self) -> None:
        cmd = _make_command(target_url="https://github.com/org/repo/issues/abc")
        with pytest.raises(TargetResolutionError, match="番号が不正"):
            resolve_target(Path("/dummy"), cmd)

    def test_zero_number(self) -> None:
        cmd = _make_command(target_url="https://github.com/org/repo/issues/0")
        with pytest.raises(TargetResolutionError, match="1 以上"):
            resolve_target(Path("/dummy"), cmd)


class TestLocalTargetFromPath:
    def test_issue_directory(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".vv-ai" / "issues" / "login-403-7k2p9a"
        issue_dir.mkdir(parents=True)
        cmd = _make_command(target_url=str(issue_dir))
        result = resolve_target(tmp_path, cmd)
        assert result.target is not None
        target = result.target
        assert target.backend == "local"
        assert target.kind == "issue"
        assert target.canonical_id == "issue:login-403-7k2p9a"
        assert target.local_id == "login-403-7k2p9a"

    def test_issue_md_file(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".vv-ai" / "issues" / "bug-fix-abc123"
        issue_dir.mkdir(parents=True)
        issue_file = issue_dir / "issue.md"
        issue_file.write_text("# Bug")
        cmd = _make_command(target_url=str(issue_file))
        result = resolve_target(tmp_path, cmd)
        assert result.target is not None
        assert result.target.kind == "issue"
        assert result.target.local_id == "bug-fix-abc123"

    def test_pr_directory(self, tmp_path: Path) -> None:
        pr_dir = tmp_path / ".vv-ai" / "prs" / "refactor-9m3x"
        pr_dir.mkdir(parents=True)
        cmd = _make_command(target_url=str(pr_dir))
        result = resolve_target(tmp_path, cmd)
        assert result.target is not None
        assert result.target.kind == "pr"
        assert result.target.canonical_id == "pr:refactor-9m3x"

    def test_sync_rejects_issue_directory(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".vv-ai" / "issues" / "login-403-7k2p9a"
        issue_dir.mkdir(parents=True)
        cmd = _make_command(command="sync", target_url=str(issue_dir))
        with pytest.raises(TargetResolutionError, match="PR 専用"):
            resolve_target(tmp_path, cmd)

    def test_pr_md_file(self, tmp_path: Path) -> None:
        pr_dir = tmp_path / ".vv-ai" / "prs" / "feature-xyz"
        pr_dir.mkdir(parents=True)
        pr_file = pr_dir / "pr.md"
        pr_file.write_text("# PR")
        cmd = _make_command(target_url=str(pr_file))
        result = resolve_target(tmp_path, cmd)
        assert result.target is not None
        assert result.target.kind == "pr"

    def test_relative_path(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".vv-ai" / "issues" / "my-issue"
        issue_dir.mkdir(parents=True)
        cmd = _make_command(target_url=".vv-ai/issues/my-issue")
        result = resolve_target(tmp_path, cmd)
        assert result.target is not None
        assert result.target.backend == "local"

    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        cmd = _make_command(target_url=str(tmp_path / ".vv-ai" / "issues" / "missing"))
        with pytest.raises(TargetResolutionError, match="見つかりません"):
            resolve_target(tmp_path, cmd)

    def test_path_outside_vv_ai_raises(self, tmp_path: Path) -> None:
        outside_dir = tmp_path / "other"
        outside_dir.mkdir()
        cmd = _make_command(target_url=str(outside_dir))
        with pytest.raises(TargetResolutionError, match=".vv-ai"):
            resolve_target(tmp_path, cmd)

    def test_invalid_structure_raises(self, tmp_path: Path) -> None:
        deep_dir = tmp_path / ".vv-ai" / "issues" / "id" / "nested" / "deep"
        deep_dir.mkdir(parents=True)
        cmd = _make_command(target_url=str(deep_dir))
        with pytest.raises(TargetResolutionError):
            resolve_target(tmp_path, cmd)


class TestResolveTargetFromFields:
    def test_github_target_from_fields(self) -> None:
        cmd = _make_command(
            repository_full_name="org/repo",
            target_type="issue",
            target_number=5,
        )
        result = resolve_target(Path("/dummy"), cmd)
        assert result.target is not None
        assert result.target.backend == "github"
        assert result.target.canonical_id == "org/repo#5"

    def test_no_target_returns_none(self) -> None:
        cmd = _make_command(
            command="issue",
            has_target=False,
        )
        result = resolve_target(Path("/dummy"), cmd)
        assert result.target is None

    def test_target_url_takes_priority(self) -> None:
        cmd = _make_command(
            target_url="https://github.com/url/repo/issues/99",
            repository_full_name="field/repo",
            target_type="pr",
            target_number=1,
        )
        result = resolve_target(Path("/dummy"), cmd)
        assert result.target is not None
        assert result.target.canonical_id == "url/repo#99"
        assert result.target.kind == "issue"


class TestResolveControlLabelTarget:
    def test_github_target_from_fields(self) -> None:
        control_label = _make_control_label()

        result = resolve_control_label_target(control_label)

        assert result.target is not None
        assert result.target.backend == "github"
        assert result.target.kind == "issue"
        assert result.target.canonical_id == "org/repo#42"
        assert result.target.repository_full_name == "org/repo"
        assert result.target.number == 42
        assert result.target.url == "https://github.com/org/repo/issues/42"
