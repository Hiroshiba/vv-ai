"""設定読込・認可・provider 解決の前処理。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.config import VVAIConfig, load_vv_ai_config
from vv_ai.local_store import generate_local_workflow_id
from vv_ai.provider import ResolvedProvider, resolve_provider
from vv_ai.resolve import ResolvedCommand
from vv_ai.session import ResolvedSession


class PreflightError(Exception):
    """実行前解決に失敗したことを表す例外。"""


class AuthorizationError(PreflightError):
    """認可に失敗したことを表す例外。"""


class SilentSkip(BaseModel):
    """外部へ何も出さずに処理を打ち切る結果。"""

    model_config = ConfigDict(extra="forbid")

    reason: Literal["unauthorized_comment", "unauthorized_label"]


class ReadyExecution(BaseModel):
    """後続処理へ進める状態。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    command: ResolvedCommand
    config: VVAIConfig
    resolved_provider: ResolvedProvider
    resolved_session: ResolvedSession | None = None
    workflow_id: str

    @property
    def provider(self) -> str:
        """確認表示用の provider 名。"""
        return self.resolved_provider.name

    @property
    def provider_source(self) -> str:
        """確認表示用の provider 解決元。"""
        return self.resolved_provider.source


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

    return ReadyExecution(
        command=resolved_command,
        config=config,
        resolved_provider=resolve_provider(resolved_command, config, env),
        workflow_id=_resolve_workflow_id(resolved_command, env),
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
        if resolved_command.event_name in {"issues", "pull_request"}:
            return SilentSkip(reason="unauthorized_label")
        raise AuthorizationError("この workflow は許可されたユーザーのみ実行できます")

    return None


def _resolve_workflow_id(
    resolved_command: ResolvedCommand,
    env: Mapping[str, str],
) -> str:
    """全 event で使う workflow_id を解決する。"""
    if resolved_command.event_name != "local":
        run_id = _normalize_optional_env_value(env.get("GITHUB_RUN_ID"))
        run_attempt = _normalize_optional_env_value(env.get("GITHUB_RUN_ATTEMPT"))
        if run_id is not None:
            if run_attempt is None:
                return f"run-{run_id}"
            return f"run-{run_id}-attempt-{run_attempt}"
        return f"debug-{generate_local_workflow_id()}"
    return generate_local_workflow_id()


def _normalize_optional_env_value(value: str | None) -> str | None:
    """空文字の環境変数を未指定として扱う。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
