"""`vv-ai.yml` の設定モデルと読み込み処理。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
)
from yaml import YAMLError, safe_load

from vv_ai.value_types import NonEmptyString

ProviderName = Literal["codex", "claude"]

_MERGE_ARG_FLAGS_WITHOUT_VALUE: set[str] = {
    "--admin",
    "--auto",
    "--delete-branch",
    "--merge",
    "--rebase",
    "--squash",
}
_MERGE_ARG_FLAGS_WITH_VALUE: set[str] = {
    "--author-email",
    "-A",
    "--body",
    "-b",
    "--match-head-commit",
    "--subject",
    "-t",
}
_MERGE_ARG_FLAGS_WITH_EQUAL_VALUE: set[str] = {
    "--author-email",
    "--body",
    "--match-head-commit",
    "--subject",
}
_FORBIDDEN_MERGE_ARG_FLAGS: set[str] = {
    "--repo",
    "-R",
    "--disable-auto",
    "--help",
    "--body-file",
    "-F",
}


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
    merge_args: list[NonEmptyString] = Field(default_factory=list)

    @field_validator("merge_args")
    @classmethod
    def validate_merge_args(cls, value: list[str]) -> list[str]:
        """`gh pr merge` に渡す追加引数を検証する。"""
        index = 0
        while index < len(value):
            token = value[index]
            _validate_merge_arg_token(token)
            if token in _MERGE_ARG_FLAGS_WITHOUT_VALUE:
                index += 1
                continue
            if token in _MERGE_ARG_FLAGS_WITH_VALUE:
                value_index = index + 1
                if value_index >= len(value):
                    raise ValueError(f"`merge_args` の `{token}` には値が必要です")
                arg_value = value[value_index]
                _validate_merge_arg_value(token, arg_value)
                index += 2
                continue
            if _is_allowed_equal_value_merge_arg(token):
                index += 1
                continue
            raise ValueError(f"`merge_args` に未対応の引数があります: {token}")
        return value


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


def _validate_merge_arg_token(token: str) -> None:
    """`merge_args` の引数 token を検証する。"""
    if token in _FORBIDDEN_MERGE_ARG_FLAGS:
        raise ValueError(f"`merge_args` では `{token}` を指定できません")
    for flag in _FORBIDDEN_MERGE_ARG_FLAGS:
        if token.startswith(f"{flag}="):
            raise ValueError(f"`merge_args` では `{flag}` を指定できません")
    if not token.startswith("-"):
        raise ValueError(f"`merge_args` に位置引数は指定できません: {token}")


def _validate_merge_arg_value(option_name: str, value: str) -> None:
    """`merge_args` の値 token を検証する。"""
    if value in _FORBIDDEN_MERGE_ARG_FLAGS:
        raise ValueError(f"`merge_args` の `{option_name}` に不正な値があります")
    if value.startswith("--repo="):
        raise ValueError(f"`merge_args` の `{option_name}` に不正な値があります")


def _is_allowed_equal_value_merge_arg(token: str) -> bool:
    """`--key=value` 形式の `merge_args` が許可対象か返す。"""
    key, separator, raw_value = token.partition("=")
    if separator != "=":
        return False
    if key not in _MERGE_ARG_FLAGS_WITH_EQUAL_VALUE:
        return False
    if raw_value == "":
        raise ValueError(f"`merge_args` の `{key}` には値が必要です")
    return True
