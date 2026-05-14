"""provider asset 配置の単体テスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vv_ai.github import GitHubTree, GitHubTreeEntry
from vv_ai.provider_asset_deploy import (
    ProviderAssetFile,
    ProviderAssetDeployError,
    resolve_vv_ai_commit_id,
    deploy_claude_provider_assets,
    deploy_codex_provider_assets,
)


class _FakeDistribution:
    """direct_url.json を返すテスト用 distribution。"""

    def __init__(self, text: str | None) -> None:
        self._text = text

    def read_text(self, name: str) -> str | None:
        """指定ファイルの内容を返す。"""
        if name != "direct_url.json":
            raise AssertionError(f"未対応のファイルです: {name}")
        return self._text


class _FakeGitHubClient:
    """provider asset 取得用の GitHub client。"""

    def __init__(self, tree: GitHubTree, blobs: dict[str, bytes]) -> None:
        self._tree = tree
        self._blobs = blobs

    def get_repository_tree(self, repository_full_name: str, ref: str) -> GitHubTree:
        """固定の tree を返す。"""
        return self._tree

    def get_repository_blob(self, repository_full_name: str, sha: str) -> bytes:
        """sha に対応する blob を返す。"""
        return self._blobs[sha]


def _patch_commit_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """commit id 解決を固定する。"""
    monkeypatch.setattr(
        "vv_ai.provider_asset_deploy.resolve_vv_ai_commit_id",
        lambda: "a" * 40,
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    tree: GitHubTree,
    blobs: dict[str, bytes],
) -> None:
    """GitHub client を固定応答に差し替える。"""
    monkeypatch.setattr(
        "vv_ai.provider_asset_deploy.build_github_client_with_token",
        lambda token: _FakeGitHubClient(tree, blobs),
    )


def test_resolve_vv_ai_commit_id_reads_direct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """direct_url.json の vcs_info.commit_id を返す。"""
    monkeypatch.setattr(
        "vv_ai.provider_asset_deploy.metadata.distribution",
        lambda name: _FakeDistribution(
            json.dumps({"vcs_info": {"commit_id": "b" * 40}})
        ),
    )

    assert resolve_vv_ai_commit_id() == "b" * 40


def test_resolve_vv_ai_commit_id_rejects_missing_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit_id が無い direct_url.json は拒否する。"""
    monkeypatch.setattr(
        "vv_ai.provider_asset_deploy.metadata.distribution",
        lambda name: _FakeDistribution(json.dumps({"dir_info": {"editable": True}})),
    )

    with pytest.raises(ProviderAssetDeployError, match="commit id"):
        resolve_vv_ai_commit_id()


def test_missing_token_uses_default_github_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """token が無い場合は gh の既存認証を使う。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path=".codex/skills/detailed-design/SKILL.md",
                type="blob",
                sha="skill",
            )
        ],
    )
    monkeypatch.setattr(
        "vv_ai.provider_asset_deploy.build_github_client",
        lambda: _FakeGitHubClient(tree, {"skill": b"codex skill"}),
    )

    result = deploy_codex_provider_assets({}, tmp_path)

    assert result.copied_files == 1
    assert (tmp_path / "skills" / "detailed-design" / "SKILL.md").is_file()


def test_fallback_gh_token_is_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VV_GH_READONLY_TOKEN が無い場合は GH_TOKEN を使う。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path=".codex/skills/detailed-design/SKILL.md",
                type="blob",
                sha="skill",
            )
        ],
    )
    used_tokens: list[str] = []

    def fake_build_client(token: str) -> _FakeGitHubClient:
        used_tokens.append(token)
        return _FakeGitHubClient(tree, {"skill": b"codex skill"})

    monkeypatch.setattr(
        "vv_ai.provider_asset_deploy.build_github_client_with_token",
        fake_build_client,
    )

    deploy_codex_provider_assets({"GH_TOKEN": "gh-token"}, tmp_path)

    assert used_tokens == ["gh-token"]


def test_deploy_codex_provider_assets_writes_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex asset を CODEX_HOME へ配置する。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path=".codex/skills/detailed-design/SKILL.md",
                type="blob",
                sha="skill",
            )
        ],
    )
    _patch_client(monkeypatch, tree, {"skill": b"codex skill"})

    result = deploy_codex_provider_assets(
        {"VV_GH_READONLY_TOKEN": "token"},
        tmp_path,
    )

    assert (tmp_path / "skills" / "detailed-design" / "SKILL.md").read_bytes() == b"codex skill"
    assert result.copied_files == 1
    assert result.overwritten_files == 0


def test_deploy_codex_provider_assets_writes_agents_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex の AGENTS.md を CODEX_HOME へ配置する。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path=".codex/AGENTS.md",
                type="blob",
                sha="agents",
            )
        ],
    )
    _patch_client(monkeypatch, tree, {"agents": b"codex agents"})

    result = deploy_codex_provider_assets(
        {"VV_GH_READONLY_TOKEN": "token"},
        tmp_path,
    )

    assert (tmp_path / "AGENTS.md").read_bytes() == b"codex agents"
    assert result.copied_files == 1
    assert result.overwritten_files == 0


def test_deploy_claude_provider_assets_writes_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude asset を ~/.claude へ配置する。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path=".claude/skills/detailed-design/SKILL.md",
                type="blob",
                sha="skill",
            )
        ],
    )
    _patch_client(monkeypatch, tree, {"skill": b"claude skill"})

    result = deploy_claude_provider_assets(
        {"VV_GH_READONLY_TOKEN": "token"},
        tmp_path,
    )

    assert (tmp_path / "skills" / "detailed-design" / "SKILL.md").read_bytes() == b"claude skill"
    assert result.copied_files == 1
    assert result.overwritten_files == 0


def test_deploy_claude_provider_assets_writes_claude_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude の CLAUDE.md を ~/.claude へ配置する。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path=".claude/CLAUDE.md",
                type="blob",
                sha="claude",
            )
        ],
    )
    _patch_client(monkeypatch, tree, {"claude": b"claude instructions"})

    result = deploy_claude_provider_assets(
        {"VV_GH_READONLY_TOKEN": "token"},
        tmp_path,
    )

    assert (tmp_path / "CLAUDE.md").read_bytes() == b"claude instructions"
    assert result.copied_files == 1
    assert result.overwritten_files == 0


def test_deploy_overwrites_changed_file_with_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """内容が違う既存ファイルは上書きし warning を出す。"""
    path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")
    file = ProviderAssetFile(
        source_path=".codex/skills/detailed-design/SKILL.md",
        destination_relative_path=Path("skills/detailed-design/SKILL.md"),
        content=b"new",
    )

    from vv_ai.provider_asset_deploy import _deploy_provider_asset_files

    result = _deploy_provider_asset_files("codex", [file], tmp_path)

    assert path.read_bytes() == b"new"
    assert result.copied_files == 0
    assert result.overwritten_files == 1
    assert "上書きしました" in capsys.readouterr().err


def test_deploy_same_file_without_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """内容が同じ既存ファイルでは warning を出さない。"""
    path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"same")
    file = ProviderAssetFile(
        source_path=".codex/skills/detailed-design/SKILL.md",
        destination_relative_path=Path("skills/detailed-design/SKILL.md"),
        content=b"same",
    )

    from vv_ai.provider_asset_deploy import _deploy_provider_asset_files

    result = _deploy_provider_asset_files("codex", [file], tmp_path)

    assert result.copied_files == 0
    assert result.overwritten_files == 0
    assert capsys.readouterr().err == ""


def test_truncated_tree_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tree が truncated の場合は配置しない。"""
    _patch_commit_id(monkeypatch)
    _patch_client(monkeypatch, GitHubTree(truncated=True, tree=[]), {})

    with pytest.raises(ProviderAssetDeployError, match="不完全"):
        deploy_codex_provider_assets({"VV_GH_READONLY_TOKEN": "token"}, tmp_path)


def test_missing_provider_assets_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider asset が無い場合は拒否する。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path="README.md",
                type="blob",
                sha="readme",
            )
        ],
    )
    _patch_client(monkeypatch, tree, {"readme": b"readme"})

    with pytest.raises(ProviderAssetDeployError, match="asset"):
        deploy_codex_provider_assets({"VV_GH_READONLY_TOKEN": "token"}, tmp_path)


def test_destination_type_mismatch_raises(tmp_path: Path) -> None:
    """配置先にディレクトリがある場合は配置しない。"""
    path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    path.mkdir(parents=True)
    file = ProviderAssetFile(
        source_path=".codex/skills/detailed-design/SKILL.md",
        destination_relative_path=Path("skills/detailed-design/SKILL.md"),
        content=b"content",
    )

    from vv_ai.provider_asset_deploy import _deploy_provider_asset_files

    with pytest.raises(ProviderAssetDeployError, match="ディレクトリ"):
        _deploy_provider_asset_files("codex", [file], tmp_path)
