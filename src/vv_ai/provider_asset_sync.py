"""provider 用 asset を vv-ai 本体から取得して同期する。"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from vv_ai.config import ProviderName
from vv_ai.github import GitHubClientError, build_github_client_with_token

_VV_AI_REPOSITORY = "Hiroshiba/vv-ai"
_READONLY_TOKEN_ENV = "VV_GH_READONLY_TOKEN"

_PROVIDER_ROOTS: dict[ProviderName, str] = {
    "codex": ".codex",
    "claude": ".claude",
}
_PROVIDER_DIRECTORIES: dict[ProviderName, tuple[str, ...]] = {
    "codex": ("skills", "agents"),
    "claude": ("skills", "agents", "commands"),
}


class ProviderAssetSyncError(Exception):
    """provider asset の同期に失敗したことを表す例外。"""


@dataclass(frozen=True)
class ProviderAssetFile:
    """同期対象の provider asset ファイル。"""

    source_path: str
    destination_relative_path: Path
    content: bytes


@dataclass(frozen=True)
class ProviderAssetSyncResult:
    """provider asset 同期結果を表す。"""

    provider: ProviderName
    destination_root: Path
    copied_files: int
    overwritten_files: int


def sync_codex_provider_assets(
    env: Mapping[str, str],
    codex_home: Path,
) -> ProviderAssetSyncResult:
    """Codex 用 provider asset を CODEX_HOME へ同期する。"""
    files = _fetch_provider_asset_files("codex", env)
    return _sync_provider_asset_files("codex", files, codex_home)


def sync_claude_provider_assets(
    env: Mapping[str, str],
    claude_home: Path,
) -> ProviderAssetSyncResult:
    """Claude 用 provider asset を ~/.claude へ同期する。"""
    files = _fetch_provider_asset_files("claude", env)
    return _sync_provider_asset_files("claude", files, claude_home)


def resolve_vv_ai_commit_id() -> str:
    """インストール済み vv-ai の commit id を返す。"""
    try:
        direct_url_text = metadata.distribution("vv-ai").read_text("direct_url.json")
    except metadata.PackageNotFoundError as exc:
        raise ProviderAssetSyncError("vv-ai の配布メタデータが見つかりません") from exc
    if direct_url_text is None:
        raise ProviderAssetSyncError("vv-ai の direct_url.json が見つかりません")
    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError as exc:
        raise ProviderAssetSyncError(
            "vv-ai の direct_url.json が JSON として不正です"
        ) from exc
    if not isinstance(direct_url, dict):
        raise ProviderAssetSyncError("vv-ai の direct_url.json の形式が不正です")
    vcs_info = direct_url.get("vcs_info")
    if not isinstance(vcs_info, dict):
        raise ProviderAssetSyncError("vv-ai の commit id を取得できません")
    commit_id = vcs_info.get("commit_id")
    if not isinstance(commit_id, str) or commit_id == "":
        raise ProviderAssetSyncError("vv-ai の commit id を取得できません")
    return commit_id


def _fetch_provider_asset_files(
    provider: ProviderName,
    env: Mapping[str, str],
) -> list[ProviderAssetFile]:
    """GitHub API から provider asset ファイル群を取得する。"""
    commit_id = resolve_vv_ai_commit_id()
    token = _require_readonly_token(env)
    client = build_github_client_with_token(token)
    try:
        tree = client.get_repository_tree(_VV_AI_REPOSITORY, commit_id)
        if tree.truncated:
            raise ProviderAssetSyncError(
                "vv-ai provider asset の tree 取得結果が不完全です"
            )
        root = _PROVIDER_ROOTS[provider]
        directory_names = _PROVIDER_DIRECTORIES[provider]
        files: list[ProviderAssetFile] = []
        for entry in tree.tree:
            if entry.type != "blob":
                continue
            relative_path = _build_destination_relative_path(
                entry.path, root, directory_names
            )
            if relative_path is None:
                continue
            content = client.get_repository_blob(_VV_AI_REPOSITORY, entry.sha)
            files.append(
                ProviderAssetFile(
                    source_path=entry.path,
                    destination_relative_path=relative_path,
                    content=content,
                )
            )
    except GitHubClientError as exc:
        raise ProviderAssetSyncError("vv-ai provider asset の取得に失敗しました") from exc
    if len(files) == 0:
        raise ProviderAssetSyncError(f"{provider} 用 provider asset が見つかりません")
    return files


def _sync_provider_asset_files(
    provider: ProviderName,
    files: list[ProviderAssetFile],
    destination_root: Path,
) -> ProviderAssetSyncResult:
    """provider asset ファイル群を provider home へ同期する。"""
    copied_files = 0
    overwritten_files = 0
    for file in files:
        copied, overwritten = _write_provider_asset_file(
            provider, file, destination_root
        )
        if copied:
            copied_files += 1
        if overwritten:
            overwritten_files += 1
    return ProviderAssetSyncResult(
        provider=provider,
        destination_root=destination_root,
        copied_files=copied_files,
        overwritten_files=overwritten_files,
    )


def _write_provider_asset_file(
    provider: ProviderName,
    file: ProviderAssetFile,
    destination_root: Path,
) -> tuple[bool, bool]:
    """provider asset ファイルを同期し、コピーと上書きの有無を返す。"""
    destination = destination_root / file.destination_relative_path
    try:
        if destination.exists() and destination.is_dir():
            raise ProviderAssetSyncError(f"`{destination}` はディレクトリです")
        if destination.parent.exists() and not destination.parent.is_dir():
            raise ProviderAssetSyncError(
                f"`{destination.parent}` はディレクトリではありません"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(file.content)
            return True, False
        current = destination.read_bytes()
        if current == file.content:
            return False, False
        destination.write_bytes(file.content)
    except OSError as exc:
        raise ProviderAssetSyncError(f"`{destination}` の同期に失敗しました") from exc
    print(
        f"vv-ai provider asset を上書きしました: provider={provider}, path={destination}",
        file=sys.stderr,
    )
    return False, True


def _require_readonly_token(env: Mapping[str, str]) -> str:
    """read-only GitHub token を返す。"""
    token = env.get(_READONLY_TOKEN_ENV, "").strip()
    if token == "":
        raise ProviderAssetSyncError(f"`{_READONLY_TOKEN_ENV}` が設定されていません")
    return token


def _build_destination_relative_path(
    source_path: str,
    root: str,
    directory_names: tuple[str, ...],
) -> Path | None:
    """同期対象なら provider home からの相対パスを返す。"""
    prefix = f"{root}/"
    if not source_path.startswith(prefix):
        return None
    rest = source_path.removeprefix(prefix)
    for directory_name in directory_names:
        if rest == directory_name or rest.startswith(f"{directory_name}/"):
            return Path(rest)
    return None
