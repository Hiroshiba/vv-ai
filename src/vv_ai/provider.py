"""provider 抽象と選択処理。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.config import ProviderName, VVAIConfig
from vv_ai.resolve import ResolvedCommand

ProviderSource = Literal["explicit", "config"]


class ProviderResolutionError(Exception):
    """provider 解決に失敗したことを表す例外。"""


class ProviderSpec(BaseModel):
    """provider ごとの固定仕様。"""

    model_config = ConfigDict(extra="forbid")

    name: ProviderName
    api_key_env: str
    api_key_file_env: str
    cli_command: str
    supports_session_resume: bool
    supports_compact: bool


class ResolvedProvider(BaseModel):
    """今回の実行で使う provider。"""

    model_config = ConfigDict(extra="forbid")

    spec: ProviderSpec
    source: ProviderSource

    @property
    def name(self) -> ProviderName:
        """選択済み provider 名。"""
        return self.spec.name

    @property
    def api_key_env(self) -> str:
        """必要な API key 用環境変数名。"""
        return self.spec.api_key_env


_PROVIDER_SPECS: dict[ProviderName, ProviderSpec] = {
    "codex": ProviderSpec(
        name="codex",
        api_key_env="VV_OPENAI_API_KEY",
        api_key_file_env="VV_OPENAI_API_KEY_FILE",
        cli_command="codex",
        supports_session_resume=True,
        supports_compact=True,
    ),
    "claude": ProviderSpec(
        name="claude",
        api_key_env="VV_ANTHROPIC_API_KEY",
        api_key_file_env="VV_ANTHROPIC_API_KEY_FILE",
        cli_command="claude",
        supports_session_resume=True,
        supports_compact=True,
    ),
}


def resolve_provider(
    resolved_command: ResolvedCommand,
    config: VVAIConfig,
    env: Mapping[str, str],
) -> ResolvedProvider:
    """利用する provider を確定する。"""
    if resolved_command.provider is not None:
        spec = get_provider_spec(resolved_command.provider)
        _ensure_provider_available(spec, env)
        return ResolvedProvider(spec=spec, source="explicit")

    for provider in _iter_provider_priority(config.provider_priority):
        spec = get_provider_spec(provider)
        if _has_provider_secret(spec, env):
            return ResolvedProvider(spec=spec, source="config")

    required_secrets = ", ".join(
        f"{get_provider_spec(p).api_key_file_env} / {get_provider_spec(p).api_key_env}"
        for p in _iter_provider_priority(config.provider_priority)
    )
    raise ProviderResolutionError(
        "利用可能な provider を選べませんでした。"
        f" 次のいずれかの環境変数を設定してください: {required_secrets}"
    )


def get_provider_spec(provider: ProviderName) -> ProviderSpec:
    """provider 名から固定仕様を返す。"""
    return _PROVIDER_SPECS[provider]


def _iter_provider_priority(
    provider_priority: list[ProviderName],
) -> tuple[ProviderName, ...]:
    """定義順を保ったまま重複を除く。"""
    return tuple(dict.fromkeys(provider_priority))


def _ensure_provider_available(
    spec: ProviderSpec,
    env: Mapping[str, str],
) -> None:
    """指定 provider の秘密値が存在することを確認する。"""
    if _has_provider_secret(spec, env):
        return

    raise ProviderResolutionError(
        f"`{spec.name}` を使うには環境変数 `{spec.api_key_file_env}` または"
        f" `{spec.api_key_env}` が必要です"
    )


def _has_provider_secret(
    spec: ProviderSpec,
    env: Mapping[str, str],
) -> bool:
    """provider 用の秘密値が利用可能かを確認する。"""
    file_path = env.get(spec.api_key_file_env, "").strip()
    if file_path:
        return Path(file_path).is_file()
    secret_value = env.get(spec.api_key_env)
    return secret_value is not None and secret_value.strip() != ""
