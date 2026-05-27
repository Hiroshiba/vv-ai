"""`vv-ai.yml` の設定モデルと読み込み処理。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError
from yaml import YAMLError, safe_load

from vv_ai.value_types import NonEmptyString

ProviderName = Literal["codex", "claude"]


class VVAIConfigError(Exception):
    """設定ファイルの読み込みに失敗したことを表す例外。"""


class VVAIConfigFileNotFoundError(VVAIConfigError):
    """設定ファイルが見つからない場合の例外。"""


class VVAIConfig(BaseModel):
    """`vv-ai.yml` の最小設定。"""

    model_config = ConfigDict(extra="forbid")

    allowed_users: list[NonEmptyString] = Field(min_length=1)
    internal_bot_ids: list[PositiveInt] = Field(
        default_factory=lambda: [274163862],
    )
    pull_request_target_branch: NonEmptyString | None = None
    provider_priority: list[ProviderName] = Field(
        default_factory=lambda: ["codex", "claude"],
        min_length=1,
    )


def load_vv_ai_config(repo_root: Path) -> VVAIConfig:
    """リポジトリルートから `vv-ai.yml` を読み込んで検証する。"""
    return load_vv_ai_config_file(repo_root / "vv-ai.yml")


def load_vv_ai_config_file(config_path: Path) -> VVAIConfig:
    """指定 path の `vv-ai.yml` を読み込んで検証する。"""
    if not config_path.is_file():
        raise VVAIConfigFileNotFoundError(
            f"`{config_path}` に設定ファイル `vv-ai.yml` が見つかりません"
        )

    try:
        raw_data = safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VVAIConfigError(f"`{config_path}` の読み込みに失敗しました") from exc
    except YAMLError as exc:
        raise VVAIConfigError(
            f"`{config_path}` を YAML として解釈できませんでした"
        ) from exc

    if raw_data is None:
        raise VVAIConfigError(f"`{config_path}` は空です")
    if not isinstance(raw_data, dict):
        raise VVAIConfigError(
            f"`{config_path}` は YAML のマッピング形式である必要があります"
        )

    try:
        return VVAIConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise VVAIConfigError(f"`{config_path}` の設定値が不正です") from exc


def find_repo_root(start_path: Path) -> Path:
    """現在位置から `vv-ai.yml` または `.git` を基準にリポジトリルートを探す。"""
    current = start_path.resolve()
    if current.is_file():
        current = current.parent

    git_root: Path | None = None
    for candidate in (current, *current.parents):
        if (candidate / "vv-ai.yml").is_file():
            return candidate
        if git_root is None and (candidate / ".git").exists():
            git_root = candidate

    if git_root is not None:
        return git_root
    return current
