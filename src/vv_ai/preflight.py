"""設定読込・認可・provider 解決の前処理。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.config import ProviderName, VVAIConfig, load_vv_ai_config
from vv_ai.resolve import ResolvedCommand

ProviderSource = Literal["explicit", "config"]

_PROVIDER_SECRET_NAMES: dict[ProviderName, str] = {
    "codex": "VV_OPENAI_API_KEY",
    "claude": "VV_ANTHROPIC_API_KEY",
}


class PreflightError(Exception):
    """実行前解決に失敗したことを表す例外。"""


class AuthorizationError(PreflightError):
    """認可に失敗したことを表す例外。"""


class ProviderResolutionError(PreflightError):
    """provider 解決に失敗したことを表す例外。"""


class SilentSkip(BaseModel):
    """外部へ何も出さずに処理を打ち切る結果。"""

    model_config = ConfigDict(extra="forbid")

    reason: Literal["unauthorized_comment"]


class ReadyExecution(BaseModel):
    """後続処理へ進める状態。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    command: ResolvedCommand
    config: VVAIConfig
    provider: ProviderName
    provider_source: ProviderSource


def run_preflight(
    repo_root: Path,
    resolved_command: ResolvedCommand,
    env: Mapping[str, str],
) -> ReadyExecution | SilentSkip:
    """設定読込・認可・provider 解決を行う。"""
    config = load_vv_ai_config(repo_root)

    authorization_result = _authorize_actor(resolved_command, config)
    if authorization_result is not None:
        return authorization_result

    provider, provider_source = _resolve_provider(resolved_command, config, env)
    return ReadyExecution(
        command=resolved_command,
        config=config,
        provider=provider,
        provider_source=provider_source,
    )


def _authorize_actor(
    resolved_command: ResolvedCommand,
    config: VVAIConfig,
) -> SilentSkip | None:
    """event に応じた認可を行う。"""
    if resolved_command.event_name == "local":
        return None

    actor = resolved_command.actor
    if actor is None:
        raise AuthorizationError("認可に必要な actor が見つかりません")

    if actor not in config.allowed_users:
        if resolved_command.event_name == "issue_comment":
            return SilentSkip(reason="unauthorized_comment")
        raise AuthorizationError("この workflow は許可されたユーザーのみ実行できます")

    if resolved_command.event_name == "workflow_dispatch" and actor != "Hiroshiba":
        raise AuthorizationError(
            "`workflow_dispatch` は Hiroshiba のみ実行できます"
        )

    return None


def _resolve_provider(
    resolved_command: ResolvedCommand,
    config: VVAIConfig,
    env: Mapping[str, str],
) -> tuple[ProviderName, ProviderSource]:
    """利用する provider を確定する。"""
    if resolved_command.provider is not None:
        _ensure_provider_available(resolved_command.provider, env)
        return resolved_command.provider, "explicit"

    for provider in _iter_provider_priority(config.provider_priority):
        if _has_provider_secret(provider, env):
            return provider, "config"

    required_secrets = ", ".join(
        _PROVIDER_SECRET_NAMES[provider]
        for provider in _iter_provider_priority(config.provider_priority)
    )
    raise ProviderResolutionError(
        "利用可能な provider を選べませんでした。"
        f" 次のいずれかの環境変数を設定してください: {required_secrets}"
    )


def _iter_provider_priority(
    provider_priority: list[ProviderName],
) -> tuple[ProviderName, ...]:
    """定義順を保ったまま重複を除く。"""
    return tuple(dict.fromkeys(provider_priority))


def _ensure_provider_available(
    provider: ProviderName,
    env: Mapping[str, str],
) -> None:
    """指定 provider の秘密値が存在することを確認する。"""
    if _has_provider_secret(provider, env):
        return

    secret_name = _PROVIDER_SECRET_NAMES[provider]
    raise ProviderResolutionError(
        f"`{provider}` を使うには環境変数 `{secret_name}` が必要です"
    )


def _has_provider_secret(
    provider: ProviderName,
    env: Mapping[str, str],
) -> bool:
    """provider 用の秘密値が空でないかを確認する。"""
    secret_name = _PROVIDER_SECRET_NAMES[provider]
    secret_value = env.get(secret_name)
    return secret_value is not None and secret_value.strip() != ""
