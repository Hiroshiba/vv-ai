"""`vv-ai.yml` の設定モデルと読み込み処理。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from yaml import YAMLError, safe_load

ProviderName = Literal["codex", "claude"]


class VVAIConfigError(Exception):
    """設定ファイルの読み込みに失敗したことを表す例外。"""


class VVAIConfigFileNotFoundError(VVAIConfigError):
    """設定ファイルが見つからない場合の例外。"""


class VVAIConfig(BaseModel):
    """`vv-ai.yml` の最小設定。"""

    model_config = ConfigDict(extra="forbid")

    allowed_users: list[str] = Field(min_length=1)
    provider_priority: list[ProviderName] = Field(
        default_factory=lambda: ["codex", "claude"],
        min_length=1,
    )

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: list[str]) -> list[str]:
        """空文字や前後空白だけのユーザー名を弾く。"""
        normalized = [user.strip() for user in value]
        if any(not user for user in normalized):
            raise ValueError("allowed_users に空文字は指定できません")
        return normalized


def load_vv_ai_config(repo_root: Path) -> VVAIConfig:
    """リポジトリルートから `vv-ai.yml` を読み込んで検証する。"""
    config_path = repo_root / "vv-ai.yml"
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
