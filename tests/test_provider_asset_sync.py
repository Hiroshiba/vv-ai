"""provider 用アセット同期の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.provider_asset_sync import (
    ProviderAssetSyncError,
    sync_claude_provider_assets,
    sync_codex_provider_assets,
    warn_provider_asset_overwrites,
)


def test_packaged_provider_assets_refer_to_root_assets() -> None:
    root = Path(__file__).resolve().parents[1]
    pairs = [
        (
            root / "src/vv_ai/.codex/skills/detailed-design/SKILL.md",
            root / ".codex/skills/detailed-design/SKILL.md",
        ),
        (
            root / "src/vv_ai/.claude/skills/detailed-design/SKILL.md",
            root / ".claude/skills/detailed-design/SKILL.md",
        ),
    ]

    for package_path, root_path in pairs:
        assert package_path.is_symlink()
        assert package_path.resolve() == root_path.resolve()


def test_codex_provider_assets_sync_detailed_design(tmp_path: Path) -> None:
    results = sync_codex_provider_assets(tmp_path)

    skill_path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    assert skill_path.is_file()
    assert "description: 基本設計に基づいて" in skill_path.read_text(encoding="utf-8")
    assert sum(result.copied_files for result in results) > 0


def test_claude_provider_assets_sync_detailed_design(tmp_path: Path) -> None:
    results = sync_claude_provider_assets(tmp_path)

    skill_path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    assert skill_path.is_file()
    assert "description: 基本設計に基づいて" in skill_path.read_text(encoding="utf-8")
    assert (tmp_path / "commands" / "team-task.md").is_file()
    assert sum(result.copied_files for result in results) > 0


def test_provider_assets_overwrite_different_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_path = tmp_path / "skills" / "detailed-design" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("古い内容", encoding="utf-8")

    results = sync_codex_provider_assets(tmp_path)
    warn_provider_asset_overwrites(results)
    captured = capsys.readouterr()

    assert "name: detailed-design" in skill_path.read_text(encoding="utf-8")
    assert sum(result.overwritten_files for result in results) == 1
    assert "provider=codex" in captured.err
    assert "overwritten=1" in captured.err


def test_provider_assets_same_file_does_not_warn(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sync_codex_provider_assets(tmp_path)
    results = sync_codex_provider_assets(tmp_path)
    warn_provider_asset_overwrites(results)
    captured = capsys.readouterr()

    assert sum(result.overwritten_files for result in results) == 0
    assert captured.err == ""


def test_provider_assets_destination_type_mismatch(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills"
    skill_path.write_text("ファイル", encoding="utf-8")

    with pytest.raises(ProviderAssetSyncError, match="ディレクトリではありません"):
        sync_codex_provider_assets(tmp_path)
