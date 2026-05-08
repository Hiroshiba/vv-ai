"""provider 用アセット同期の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.provider_asset_bundle import PROVIDER_ASSET_TEXTS
from vv_ai.provider_asset_sync import (
    ProviderAssetSyncError,
    sync_claude_provider_assets,
    sync_codex_provider_assets,
    warn_provider_asset_overwrites,
)


def test_provider_asset_bundle_matches_root_files() -> None:
    """同梱アセットがリポジトリ直下の provider ディレクトリと一致する。"""
    repo_root = Path(__file__).parents[1]
    expected: dict[str, str] = {}

    for directory_name in (".codex", ".claude"):
        for path in sorted((repo_root / directory_name).rglob("*")):
            if path.is_file() is False:
                continue
            relative_path = path.relative_to(repo_root).as_posix()
            expected[relative_path] = path.read_text(encoding="utf-8")

    assert PROVIDER_ASSET_TEXTS == expected


def test_sync_codex_provider_assets_creates_assets(tmp_path: Path) -> None:
    """Codex 用アセットを Codex home へ作成する。"""
    results = sync_codex_provider_assets(tmp_path)

    skill_path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    assert skill_path.is_file()
    assert "name: detailed-design" in skill_path.read_text(encoding="utf-8")
    assert {result.directory_name for result in results} == {"agents", "skills"}


def test_sync_claude_provider_assets_creates_assets(tmp_path: Path) -> None:
    """Claude 用アセットを Claude home へ作成する。"""
    results = sync_claude_provider_assets(tmp_path)

    skill_path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    command_path = tmp_path / "commands" / "team-task.md"
    assert skill_path.is_file()
    assert command_path.is_file()
    assert "description: 基本設計に基づいて" in skill_path.read_text(
        encoding="utf-8"
    )
    assert {result.directory_name for result in results} == {
        "agents",
        "commands",
        "skills",
    }


def test_warns_only_when_overwriting_different_asset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """差分のある既存ファイルだけを上書き警告する。"""
    skill_path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("古いスキル\n", encoding="utf-8")

    results = sync_codex_provider_assets(tmp_path)
    warn_provider_asset_overwrites(results)

    stderr = capsys.readouterr().err
    assert "provider=codex" in stderr
    assert "directory=skills" in stderr
    assert "overwritten=1" in stderr
    assert "name: detailed-design" in skill_path.read_text(encoding="utf-8")


def test_does_not_warn_when_asset_is_same(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同一内容の既存ファイルは警告しない。"""
    sync_codex_provider_assets(tmp_path)
    results = sync_codex_provider_assets(tmp_path)
    warn_provider_asset_overwrites(results)

    assert capsys.readouterr().err == ""
    assert sum(result.overwritten_files for result in results) == 0


def test_sync_codex_provider_assets_rejects_file_destination(
    tmp_path: Path,
) -> None:
    """同期先ディレクトリにファイルがある場合は例外を投げる。"""
    (tmp_path / "skills").write_text("not directory\n", encoding="utf-8")

    with pytest.raises(ProviderAssetSyncError, match="ディレクトリではありません"):
        sync_codex_provider_assets(tmp_path)
