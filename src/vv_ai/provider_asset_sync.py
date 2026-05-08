"""provider 用アセットを実行環境へ同期する。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path


class ProviderAssetSyncError(Exception):
    """provider 用アセットの同期に失敗したことを表す例外。"""


@dataclass(frozen=True)
class ProviderAssetSyncResult:
    """provider 用アセットの同期結果。"""

    provider_name: str
    directory_name: str
    destination: Path
    copied_files: int
    overwritten_files: int


def sync_codex_provider_assets(codex_home: Path) -> list[ProviderAssetSyncResult]:
    """Codex 用アセットを CODEX_HOME へ同期する。"""
    return _sync_provider_directories("codex", codex_home, ("agents", "skills"))


def sync_claude_provider_assets(claude_home: Path) -> list[ProviderAssetSyncResult]:
    """Claude 用アセットを ~/.claude へ同期する。"""
    return _sync_provider_directories(
        "claude", claude_home, ("agents", "commands", "skills")
    )


def warn_provider_asset_overwrites(results: list[ProviderAssetSyncResult]) -> None:
    """上書きが発生した provider 用アセットを警告として出力する。"""
    for result in results:
        if result.overwritten_files > 0:
            print(
                "警告: vv-ai provider asset を上書きしました: "
                f"provider={result.provider_name}, "
                f"directory={result.directory_name}, "
                f"destination={result.destination}, "
                f"overwritten={result.overwritten_files}",
                file=sys.stderr,
            )


def _sync_provider_directories(
    provider_name: str,
    destination_root: Path,
    directory_names: tuple[str, ...],
) -> list[ProviderAssetSyncResult]:
    source_root = _resolve_packaged_provider_root(provider_name)
    results: list[ProviderAssetSyncResult] = []
    for directory_name in directory_names:
        source = source_root / directory_name
        if not source.is_dir():
            raise ProviderAssetSyncError(
                f"provider 用アセット `{provider_name}/{directory_name}` が見つかりません"
            )
        destination = destination_root / directory_name
        copied_files, overwritten_files = _sync_tree(source, destination)
        results.append(
            ProviderAssetSyncResult(
                provider_name=provider_name,
                directory_name=directory_name,
                destination=destination,
                copied_files=copied_files,
                overwritten_files=overwritten_files,
            )
        )
    return results


def _resolve_packaged_provider_root(provider_name: str) -> Traversable:
    root = files("vv_ai") / f".{provider_name}"
    if not root.is_dir():
        raise ProviderAssetSyncError(
            f"provider 用アセット `{provider_name}` が見つかりません"
        )
    return root


def _sync_tree(source: Traversable, destination: Path) -> tuple[int, int]:
    if destination.exists() and not destination.is_dir():
        raise ProviderAssetSyncError(
            f"同期先 `{destination}` はディレクトリではありません"
        )
    destination.mkdir(parents=True, exist_ok=True)

    copied_files = 0
    overwritten_files = 0
    for child in source.iterdir():
        child_destination = destination / child.name
        if child.is_dir():
            child_copied_files, child_overwritten_files = _sync_tree(
                child, child_destination
            )
        elif child.is_file():
            child_copied_files, child_overwritten_files = _sync_file(
                child, child_destination
            )
        else:
            raise ProviderAssetSyncError(
                f"provider 用アセット `{child.name}` は同期できない種類です"
            )
        copied_files += child_copied_files
        overwritten_files += child_overwritten_files
    return copied_files, overwritten_files


def _sync_file(source: Traversable, destination: Path) -> tuple[int, int]:
    if destination.exists() and destination.is_dir():
        raise ProviderAssetSyncError(
            f"同期先 `{destination}` はファイルではありません"
        )

    content = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not destination.exists():
        destination.write_bytes(content)
        return 1, 0

    current_content = destination.read_bytes()
    if current_content == content:
        return 0, 0

    destination.write_bytes(content)
    return 0, 1
