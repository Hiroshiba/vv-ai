"""provider asset 配置の単体テスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vv_ai.backends.github.models import GitHubTree, GitHubTreeEntry
from vv_ai.providers.assets import (
    ProviderAssetFile,
    ProviderAssetDeployError,
    resolve_vv_ai_commit_id,
    deploy_claude_provider_assets,
    deploy_codex_provider_assets,
)

_REPOSITORY_ROOT: Path = Path(__file__).resolve().parents[1]
_PROVIDER_ASSET_READONLY_POLICY: str = (
    "調査に必要な参照系 git コマンドは実行して構いません。"
)
_PROVIDER_ASSET_MUTATION_POLICY: str = (
    "作業ツリー、ステージング領域、ブランチ、リモートを変更する git コマンドは、"
    "明示指示がない限り実行しないでください。"
)
_GIT_COMMAND_EXAMPLES: tuple[str, ...] = (
    "git status",
    "git diff",
    "git log",
    "git blame",
    "git show",
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
        "vv_ai.providers.assets.resolve_vv_ai_commit_id",
        lambda: "a" * 40,
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    tree: GitHubTree,
    blobs: dict[str, bytes],
) -> None:
    """GitHub client を固定応答に差し替える。"""
    monkeypatch.setattr(
        "vv_ai.providers.assets.build_github_client_with_token",
        lambda token: _FakeGitHubClient(tree, blobs),
    )


def _read_repository_text(relative_path: str) -> str:
    """リポジトリ内ファイルの本文を返す。"""
    return (_REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def _assert_provider_asset_mentions_git_command_policy(text: str) -> None:
    """provider asset に git コマンド方針が含まれることを検証する。"""
    assert _PROVIDER_ASSET_READONLY_POLICY in text
    assert _PROVIDER_ASSET_MUTATION_POLICY in text
    assert "index" not in text
    for command_example in _GIT_COMMAND_EXAMPLES:
        assert command_example not in text


def test_codex_agents_md_mentions_git_command_policy() -> None:
    """Codex の provider asset に git コマンド方針を書く。"""
    text = _read_repository_text(".codex/AGENTS.md")

    _assert_provider_asset_mentions_git_command_policy(text)


def test_claude_claude_md_mentions_git_command_policy() -> None:
    """Claude の provider asset に git コマンド方針を書く。"""
    text = _read_repository_text(".claude/CLAUDE.md")

    _assert_provider_asset_mentions_git_command_policy(text)


def test_resolve_vv_ai_commit_id_reads_direct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """direct_url.json の vcs_info.commit_id を返す。"""
    monkeypatch.setattr(
        "vv_ai.providers.assets.metadata.distribution",
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
        "vv_ai.providers.assets.metadata.distribution",
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
        "vv_ai.providers.assets.build_github_client",
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
        "vv_ai.providers.assets.build_github_client_with_token",
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
    assert result.appended_files == 0
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
    assert result.appended_files == 0
    assert result.overwritten_files == 0


def test_deploy_codex_provider_assets_appends_agents_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既存の Codex AGENTS.md へ provider asset を追記する。"""
    (tmp_path / "AGENTS.md").write_bytes(b"hiho")
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

    err = capsys.readouterr().err
    assert (tmp_path / "AGENTS.md").read_bytes() == b"hiho\ncodex agents"
    assert result.copied_files == 0
    assert result.appended_files == 1
    assert result.overwritten_files == 0
    assert "追記しました" in err
    assert "上書きしました" not in err


def test_deploy_root_instruction_appends_after_existing_newline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既存末尾に改行があれば追加の改行を挟まず追記する。"""
    path = tmp_path / "AGENTS.md"
    path.write_bytes(b"hiho\n")
    file = ProviderAssetFile(
        source_path=".codex/AGENTS.md",
        destination_relative_path=Path("AGENTS.md"),
        content=b"codex agents",
    )

    from vv_ai.providers.assets import _deploy_provider_asset_files

    result = _deploy_provider_asset_files("codex", [file], tmp_path)

    assert path.read_bytes() == b"hiho\ncodex agents"
    assert result.copied_files == 0
    assert result.appended_files == 1
    assert result.overwritten_files == 0
    assert "追記しました" in capsys.readouterr().err


def test_deploy_root_instruction_appends_same_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """root 指示ファイルは同一内容でも追記する。"""
    path = tmp_path / "AGENTS.md"
    path.write_bytes(b"same")
    file = ProviderAssetFile(
        source_path=".codex/AGENTS.md",
        destination_relative_path=Path("AGENTS.md"),
        content=b"same",
    )

    from vv_ai.providers.assets import _deploy_provider_asset_files

    result = _deploy_provider_asset_files("codex", [file], tmp_path)

    assert path.read_bytes() == b"same\nsame"
    assert result.copied_files == 0
    assert result.appended_files == 1
    assert result.overwritten_files == 0
    assert "追記しました" in capsys.readouterr().err


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
    assert result.appended_files == 0
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
    assert result.appended_files == 0
    assert result.overwritten_files == 0


def test_deploy_claude_provider_assets_appends_claude_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既存の Claude CLAUDE.md へ provider asset を追記する。"""
    (tmp_path / "CLAUDE.md").write_bytes(b"hiho")
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

    err = capsys.readouterr().err
    assert (tmp_path / "CLAUDE.md").read_bytes() == b"hiho\nclaude instructions"
    assert result.copied_files == 0
    assert result.appended_files == 1
    assert result.overwritten_files == 0
    assert "追記しました" in err
    assert "上書きしました" not in err


def test_deploy_claude_provider_assets_ignores_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude commands は provider asset として配置しない。"""
    _patch_commit_id(monkeypatch)
    tree = GitHubTree(
        truncated=False,
        tree=[
            GitHubTreeEntry(
                path=".claude/skills/detailed-design/SKILL.md",
                type="blob",
                sha="skill",
            ),
            GitHubTreeEntry(
                path=".claude/commands/team-task.md",
                type="blob",
                sha="command",
            ),
        ],
    )
    _patch_client(
        monkeypatch,
        tree,
        {
            "skill": b"claude skill",
            "command": b"claude command",
        },
    )

    result = deploy_claude_provider_assets(
        {"VV_GH_READONLY_TOKEN": "token"},
        tmp_path,
    )

    assert (tmp_path / "skills" / "detailed-design" / "SKILL.md").is_file()
    assert not (tmp_path / "commands" / "team-task.md").exists()
    assert result.copied_files == 1
    assert result.appended_files == 0
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

    from vv_ai.providers.assets import _deploy_provider_asset_files

    result = _deploy_provider_asset_files("codex", [file], tmp_path)

    assert path.read_bytes() == b"new"
    assert result.copied_files == 0
    assert result.appended_files == 0
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

    from vv_ai.providers.assets import _deploy_provider_asset_files

    result = _deploy_provider_asset_files("codex", [file], tmp_path)

    assert result.copied_files == 0
    assert result.appended_files == 0
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

    from vv_ai.providers.assets import _deploy_provider_asset_files

    with pytest.raises(ProviderAssetDeployError, match="ディレクトリ"):
        _deploy_provider_asset_files("codex", [file], tmp_path)
