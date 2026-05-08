"""provider 用アセットを実行環境へ同期する。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from vv_ai.provider_asset_bundle import PROVIDER_ASSET_TEXTS

_CODEX_ASSET_DIRECTORIES = ("agents", "skills")
_CLAUDE_ASSET_DIRECTORIES = ("agents", "commands", "skills")


class ProviderAssetSyncError(Exception):
    """provider 用アセットの同期失敗を表す例外。"""


@dataclass(frozen=True)
class ProviderAssetSyncResult:
    """provider 用アセットの同期結果。"""

    provider_name: str
    directory_name: str
    destination: Path
    copied_files: int
    overwritten_files: int


def sync_codex_provider_assets(codex_home: Path) -> list[ProviderAssetSyncResult]:
    """Codex 用アセットを Codex home へ同期する。"""
    return _sync_provider_assets(
        "codex", ".codex", codex_home, _CODEX_ASSET_DIRECTORIES
    )


def sync_claude_provider_assets(claude_home: Path) -> list[ProviderAssetSyncResult]:
    """Claude 用アセットを Claude home へ同期する。"""
    return _sync_provider_assets(
        "claude", ".claude", claude_home, _CLAUDE_ASSET_DIRECTORIES
    )


def warn_provider_asset_overwrites(results: list[ProviderAssetSyncResult]) -> None:
    """上書きが発生した provider 用アセットを標準エラーへ警告する。"""
    for result in results:
        if result.overwritten_files == 0:
            continue
        print(
            "警告: vv-ai provider asset を上書きしました: "
            f"provider={result.provider_name}, "
            f"directory={result.directory_name}, "
            f"destination={result.destination}, "
            f"overwritten={result.overwritten_files}",
            file=sys.stderr,
        )


def _sync_provider_assets(
    provider_name: str,
    source_root: str,
    provider_home: Path,
    directory_names: tuple[str, ...],
) -> list[ProviderAssetSyncResult]:
    """provider 用アセットを provider home へ同期する。"""
    _ensure_directory(provider_home)

    results: list[ProviderAssetSyncResult] = []
    for directory_name in directory_names:
        copied_files = 0
        overwritten_files = 0
        destination_root = provider_home / directory_name
        _ensure_directory(destination_root)

        for relative_path, content in _iter_asset_texts(source_root, directory_name):
            destination = destination_root / relative_path
            copied, overwritten = _sync_asset_text(destination, content)
            copied_files += copied
            overwritten_files += overwritten

        results.append(
            ProviderAssetSyncResult(
                provider_name=provider_name,
                directory_name=directory_name,
                destination=destination_root,
                copied_files=copied_files,
                overwritten_files=overwritten_files,
            )
        )

    return results


def _iter_asset_texts(
    source_root: str, directory_name: str
) -> list[tuple[Path, str]]:
    """同梱アセットから対象ディレクトリ配下のテキストを返す。"""
    prefix = f"{source_root}/{directory_name}/"
    items: list[tuple[Path, str]] = []
    for key, content in sorted(PROVIDER_ASSET_TEXTS.items()):
        if key.startswith(prefix) is False:
            continue
        relative_path = Path(key.removeprefix(prefix))
        if ".." in relative_path.parts:
            raise ProviderAssetSyncError(
                f"provider 用アセットのパスが不正です: {key}"
            )
        items.append((relative_path, content))
    if len(items) == 0:
        raise ProviderAssetSyncError(
            f"provider 用アセットが見つかりません: {source_root}/{directory_name}"
        )
    return items


def _sync_asset_text(destination: Path, content: str) -> tuple[int, int]:
    """テキストアセットを同期して作成数と上書き数を返す。"""
    _ensure_directory(destination.parent)
    if destination.exists() is False:
        destination.write_text(content, encoding="utf-8")
        return 1, 0

    if destination.is_file() is False:
        raise ProviderAssetSyncError(
            f"provider 用アセットの同期先がファイルではありません: {destination}"
        )

    current = destination.read_text(encoding="utf-8")
    if current == content:
        return 0, 0

    destination.write_text(content, encoding="utf-8")
    return 0, 1


def _ensure_directory(path: Path) -> None:
    """ディレクトリを作成し、ファイルがある場合は例外を投げる。"""
    if path.exists() is True and path.is_dir() is False:
        raise ProviderAssetSyncError(
            f"provider 用アセットの同期先がディレクトリではありません: {path}"
        )
    path.mkdir(parents=True, exist_ok=True)
