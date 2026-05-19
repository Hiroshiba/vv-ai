"""provider 用 asset を vv-ai 本体から取得して配置する。"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Literal

from vv_ai.backends.github.client import (
    GitHubClient,
    build_github_client,
    build_github_client_with_token,
)
from vv_ai.backends.github.models import GitHubClientError
from vv_ai.config import ProviderName

_VV_AI_REPOSITORY = "Hiroshiba/vv-ai"
_READONLY_TOKEN_ENV = "VV_GH_READONLY_TOKEN"
_FALLBACK_TOKEN_ENVS = ("GH_TOKEN", "GITHUB_TOKEN")

_PROVIDER_ROOTS: dict[ProviderName, str] = {
    "codex": ".codex",
    "claude": ".claude",
}
_PROVIDER_DIRECTORIES: dict[ProviderName, tuple[str, ...]] = {
    "codex": ("skills", "agents"),
    "claude": ("skills", "agents"),
}
_PROVIDER_ROOT_FILES: dict[ProviderName, tuple[str, ...]] = {
    "codex": ("AGENTS.md",),
    "claude": ("CLAUDE.md",),
}
type ProviderAssetWriteAction = Literal[
    "copied",
    "appended",
    "overwritten",
    "unchanged",
]


class ProviderAssetDeployError(Exception):
    """provider asset の配置に失敗したことを表す例外。"""


@dataclass(frozen=True)
class ProviderAssetFile:
    """配置対象の provider asset ファイル。"""

    source_path: str
    destination_relative_path: Path
    content: bytes


@dataclass(frozen=True)
class ProviderAssetDeployResult:
    """provider asset 配置結果を表す。"""

    provider: ProviderName
    destination_root: Path
    copied_files: int
    appended_files: int
    overwritten_files: int


def deploy_codex_provider_assets(
    env: Mapping[str, str],
    codex_home: Path,
) -> ProviderAssetDeployResult:
    """Codex 用 provider asset を CODEX_HOME へ配置する。"""
    files = _fetch_provider_asset_files("codex", env)
    return _deploy_provider_asset_files("codex", files, codex_home)


def deploy_claude_provider_assets(
    env: Mapping[str, str],
    claude_home: Path,
) -> ProviderAssetDeployResult:
    """Claude 用 provider asset を ~/.claude へ配置する。"""
    files = _fetch_provider_asset_files("claude", env)
    return _deploy_provider_asset_files("claude", files, claude_home)


def copy_codex_provider_assets_to_work_dir(
    repo_root: Path,
    work_root: Path,
) -> ProviderAssetDeployResult:
    """Codex 用 provider asset を作業用ディレクトリへコピーする。"""
    source_root = repo_root / _PROVIDER_ROOTS["codex"]
    _ensure_directory(source_root)
    _ensure_no_symlink_in_existing_path(repo_root, work_root)
    if work_root.is_symlink():
        raise ProviderAssetDeployError(f"`{work_root}` は symlink です")
    if work_root.exists():
        if work_root.is_dir() is False:
            raise ProviderAssetDeployError(
                f"`{work_root}` はディレクトリではありません"
            )
        shutil.rmtree(work_root)
    files = _collect_local_provider_asset_files("codex", source_root)
    return _copy_provider_asset_files("codex", files, work_root)


def sync_codex_provider_assets_from_work_dir(
    repo_root: Path,
    work_root: Path,
) -> ProviderAssetDeployResult:
    """Codex 用 provider asset を作業用ディレクトリから .codex へ同期する。"""
    destination_root = repo_root / _PROVIDER_ROOTS["codex"]
    _ensure_no_symlink_in_existing_path(repo_root, work_root)
    _ensure_directory(work_root)
    _ensure_directory(destination_root)
    files = _collect_local_provider_asset_files("codex", work_root)
    _replace_provider_asset_targets("codex", destination_root)
    return _copy_provider_asset_files("codex", files, destination_root)


def resolve_vv_ai_commit_id() -> str:
    """インストール済み vv-ai の commit id を返す。"""
    try:
        direct_url_text = metadata.distribution("vv-ai").read_text("direct_url.json")
    except metadata.PackageNotFoundError as exc:
        raise ProviderAssetDeployError("vv-ai の配布メタデータが見つかりません") from exc
    if direct_url_text is None:
        raise ProviderAssetDeployError("vv-ai の direct_url.json が見つかりません")
    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError as exc:
        raise ProviderAssetDeployError(
            "vv-ai の direct_url.json が JSON として不正です"
        ) from exc
    if not isinstance(direct_url, dict):
        raise ProviderAssetDeployError("vv-ai の direct_url.json の形式が不正です")
    vcs_info = direct_url.get("vcs_info")
    if not isinstance(vcs_info, dict):
        raise ProviderAssetDeployError("vv-ai の commit id を取得できません")
    commit_id = vcs_info.get("commit_id")
    if not isinstance(commit_id, str) or commit_id == "":
        raise ProviderAssetDeployError("vv-ai の commit id を取得できません")
    return commit_id


def _fetch_provider_asset_files(
    provider: ProviderName,
    env: Mapping[str, str],
) -> list[ProviderAssetFile]:
    """GitHub API から provider asset ファイル群を取得する。"""
    commit_id = resolve_vv_ai_commit_id()
    client = _build_provider_asset_github_client(env)
    try:
        tree = client.get_repository_tree(_VV_AI_REPOSITORY, commit_id)
        if tree.truncated:
            raise ProviderAssetDeployError(
                "vv-ai provider asset の tree 取得結果が不完全です"
            )
        files: list[ProviderAssetFile] = []
        for entry in tree.tree:
            if entry.type != "blob":
                continue
            relative_path = _build_provider_asset_relative_path(provider, entry.path)
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
        raise ProviderAssetDeployError("vv-ai provider asset の取得に失敗しました") from exc
    if len(files) == 0:
        raise ProviderAssetDeployError(f"{provider} 用 provider asset が見つかりません")
    return files


def _deploy_provider_asset_files(
    provider: ProviderName,
    files: list[ProviderAssetFile],
    destination_root: Path,
) -> ProviderAssetDeployResult:
    """provider asset ファイル群を provider home へ配置する。"""
    copied_files = 0
    appended_files = 0
    overwritten_files = 0
    for file in files:
        action = _write_provider_asset_file(provider, file, destination_root)
        if action == "copied":
            copied_files += 1
        elif action == "appended":
            appended_files += 1
        elif action == "overwritten":
            overwritten_files += 1
        elif action == "unchanged":
            pass
        else:
            raise AssertionError(f"未対応の provider asset 配置結果です: {action}")
    return ProviderAssetDeployResult(
        provider=provider,
        destination_root=destination_root,
        copied_files=copied_files,
        appended_files=appended_files,
        overwritten_files=overwritten_files,
    )


def _write_provider_asset_file(
    provider: ProviderName,
    file: ProviderAssetFile,
    destination_root: Path,
) -> ProviderAssetWriteAction:
    """provider asset ファイルを配置して処理結果を返す。"""
    destination = destination_root / file.destination_relative_path
    try:
        if destination.exists() and destination.is_dir():
            raise ProviderAssetDeployError(f"`{destination}` はディレクトリです")
        if destination.parent.exists() and not destination.parent.is_dir():
            raise ProviderAssetDeployError(
                f"`{destination.parent}` はディレクトリではありません"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(file.content)
            return "copied"
        current = destination.read_bytes()
        if _is_provider_root_instruction_file(provider, file):
            appended_content = _append_provider_asset_content(current, file.content)
            destination.write_bytes(appended_content)
            print(
                f"vv-ai provider asset を追記しました: provider={provider}, path={destination}",
                file=sys.stderr,
            )
            return "appended"
        if current == file.content:
            return "unchanged"
        destination.write_bytes(file.content)
    except OSError as exc:
        raise ProviderAssetDeployError(f"`{destination}` の配置に失敗しました") from exc
    print(
        f"vv-ai provider asset を上書きしました: provider={provider}, path={destination}",
        file=sys.stderr,
    )
    return "overwritten"


def _is_provider_root_instruction_file(
    provider: ProviderName,
    file: ProviderAssetFile,
) -> bool:
    """provider root の指示ファイルなら true を返す。"""
    return file.destination_relative_path in {
        Path(root_file_name) for root_file_name in _PROVIDER_ROOT_FILES[provider]
    }


def _append_provider_asset_content(current: bytes, content: bytes) -> bytes:
    """既存内容の末尾へ provider asset 内容を追加する。"""
    if len(current) == 0 or current.endswith(b"\n"):
        return current + content
    return current + b"\n" + content


def _build_provider_asset_github_client(env: Mapping[str, str]) -> GitHubClient:
    """provider asset 取得用 GitHub client を返す。"""
    token = _resolve_provider_asset_token(env)
    if token is None:
        return build_github_client()
    return build_github_client_with_token(token)


def _resolve_provider_asset_token(env: Mapping[str, str]) -> str | None:
    """provider asset 取得に使う GitHub token を返す。"""
    readonly_token = env.get(_READONLY_TOKEN_ENV, "").strip()
    if readonly_token != "":
        return readonly_token
    for token_env in _FALLBACK_TOKEN_ENVS:
        token = env.get(token_env, "").strip()
        if token != "":
            return token
    return None


def _build_provider_asset_relative_path(
    provider: ProviderName,
    source_path: str,
) -> Path | None:
    """provider asset なら provider home からの相対パスを返す。"""
    return _build_destination_relative_path(
        source_path,
        _PROVIDER_ROOTS[provider],
        _PROVIDER_DIRECTORIES[provider],
        _PROVIDER_ROOT_FILES[provider],
    )


def _build_destination_relative_path(
    source_path: str,
    root: str,
    directory_names: tuple[str, ...],
    root_file_names: tuple[str, ...],
) -> Path | None:
    """配置対象なら provider home からの相対パスを返す。"""
    prefix = f"{root}/"
    if source_path.startswith(prefix) is False:
        return None
    rest = source_path.removeprefix(prefix)
    if rest in root_file_names:
        return Path(rest)
    for directory_name in directory_names:
        if rest == directory_name or rest.startswith(f"{directory_name}/"):
            return Path(rest)
    return None


def _collect_local_provider_asset_files(
    provider: ProviderName,
    source_root: Path,
) -> list[ProviderAssetFile]:
    """ローカルの provider asset ファイル群を返す。"""
    _ensure_directory(source_root)
    files: list[ProviderAssetFile] = []
    for root_file_name in _PROVIDER_ROOT_FILES[provider]:
        path = source_root / root_file_name
        if path.is_symlink():
            raise ProviderAssetDeployError(f"`{path}` は symlink です")
        if path.exists() is False:
            continue
        _ensure_regular_file(path)
        files.append(_read_local_provider_asset_file(path, Path(root_file_name)))
    for directory_name in _PROVIDER_DIRECTORIES[provider]:
        directory = source_root / directory_name
        if directory.is_symlink():
            raise ProviderAssetDeployError(f"`{directory}` は symlink です")
        if directory.exists() is False:
            continue
        _ensure_directory(directory)
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ProviderAssetDeployError(f"`{path}` は symlink です")
            if path.is_dir():
                continue
            _ensure_regular_file(path)
            relative_path = path.relative_to(source_root)
            files.append(_read_local_provider_asset_file(path, relative_path))
    if len(files) == 0:
        raise ProviderAssetDeployError(f"{provider} 用 provider asset が見つかりません")
    return files


def _read_local_provider_asset_file(
    path: Path,
    relative_path: Path,
) -> ProviderAssetFile:
    """ローカルの provider asset ファイルを読み込む。"""
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ProviderAssetDeployError(f"`{path}` の読み込みに失敗しました") from exc
    return ProviderAssetFile(
        source_path=relative_path.as_posix(),
        destination_relative_path=relative_path,
        content=content,
    )


def _copy_provider_asset_files(
    provider: ProviderName,
    files: list[ProviderAssetFile],
    destination_root: Path,
) -> ProviderAssetDeployResult:
    """provider asset ファイル群を追記せずコピーする。"""
    copied_files = 0
    appended_files = 0
    overwritten_files = 0
    for file in files:
        action = _copy_provider_asset_file(file, destination_root)
        if action == "copied":
            copied_files += 1
        elif action == "appended":
            appended_files += 1
        elif action == "overwritten":
            overwritten_files += 1
        elif action == "unchanged":
            pass
        else:
            raise AssertionError(f"未対応の provider asset 配置結果です: {action}")
    return ProviderAssetDeployResult(
        provider=provider,
        destination_root=destination_root,
        copied_files=copied_files,
        appended_files=appended_files,
        overwritten_files=overwritten_files,
    )


def _copy_provider_asset_file(
    file: ProviderAssetFile,
    destination_root: Path,
) -> ProviderAssetWriteAction:
    """provider asset ファイルを追記せずコピーして処理結果を返す。"""
    destination = destination_root / file.destination_relative_path
    try:
        if destination.is_symlink():
            raise ProviderAssetDeployError(f"`{destination}` は symlink です")
        if destination.exists() and destination.is_dir():
            raise ProviderAssetDeployError(f"`{destination}` はディレクトリです")
        if destination.parent.exists() and destination.parent.is_symlink():
            raise ProviderAssetDeployError(f"`{destination.parent}` は symlink です")
        if destination.parent.exists() and destination.parent.is_dir() is False:
            raise ProviderAssetDeployError(
                f"`{destination.parent}` はディレクトリではありません"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() is False:
            destination.write_bytes(file.content)
            return "copied"
        current = destination.read_bytes()
        if current == file.content:
            return "unchanged"
        destination.write_bytes(file.content)
    except OSError as exc:
        raise ProviderAssetDeployError(f"`{destination}` の配置に失敗しました") from exc
    return "overwritten"


def _replace_provider_asset_targets(
    provider: ProviderName,
    destination_root: Path,
) -> None:
    """provider asset 対象だけを同期前に削除する。"""
    for root_file_name in _PROVIDER_ROOT_FILES[provider]:
        path = destination_root / root_file_name
        if path.exists() is False and path.is_symlink() is False:
            continue
        if path.is_symlink():
            raise ProviderAssetDeployError(f"`{path}` は symlink です")
        if path.is_dir():
            raise ProviderAssetDeployError(f"`{path}` はディレクトリです")
        if path.is_file() is False:
            raise ProviderAssetDeployError(f"`{path}` は通常ファイルではありません")
        path.unlink()
    for directory_name in _PROVIDER_DIRECTORIES[provider]:
        path = destination_root / directory_name
        if path.exists() is False and path.is_symlink() is False:
            continue
        if path.is_symlink():
            raise ProviderAssetDeployError(f"`{path}` は symlink です")
        if path.is_dir() is False:
            raise ProviderAssetDeployError(f"`{path}` はディレクトリではありません")
        shutil.rmtree(path)


def _ensure_directory(path: Path) -> None:
    """path が通常ディレクトリであることを検証する。"""
    if path.is_symlink():
        raise ProviderAssetDeployError(f"`{path}` は symlink です")
    if path.is_dir() is False:
        raise ProviderAssetDeployError(f"`{path}` はディレクトリではありません")


def _ensure_regular_file(path: Path) -> None:
    """path が通常ファイルであることを検証する。"""
    if path.is_symlink():
        raise ProviderAssetDeployError(f"`{path}` は symlink です")
    if path.is_file() is False:
        raise ProviderAssetDeployError(f"`{path}` は通常ファイルではありません")


def _ensure_no_symlink_in_existing_path(root: Path, path: Path) -> None:
    """root から path までの既存 path に symlink が無いことを検証する。"""
    try:
        relative_path = path.relative_to(root)
    except ValueError as exc:
        raise ProviderAssetDeployError(
            f"`{path}` は `{root}` 配下の path ではありません"
        ) from exc
    current = root
    for part in relative_path.parts:
        current = current / part
        if current.exists() is False and current.is_symlink() is False:
            continue
        if current.is_symlink():
            raise ProviderAssetDeployError(f"`{current}` は symlink です")
