"""設定読込・認可・provider 解決の前処理。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.config import VVAIConfig, load_vv_ai_config
from vv_ai.backends.local.store import generate_local_workflow_id
from vv_ai.providers.selection import ResolvedProvider, resolve_provider
from vv_ai.inputs.resolve import ResolvedCommand, ResolvedControlLabel, ResolvedInput
from vv_ai.sessions.models import ResolvedSession


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


class ReadyControlExecution(BaseModel):
    """制御ラベル処理へ進める状態。"""

    model_config = ConfigDict(extra="forbid")

    control: ResolvedControlLabel
    config: VVAIConfig
    workflow_id: str


def run_preflight(
    repo_root: Path,
    resolved_input: ResolvedInput,
    env: Mapping[str, str],
) -> ReadyExecution | ReadyControlExecution | SilentSkip:
    """設定読込・認可・provider 解決を行う。"""
    config = load_vv_ai_config(repo_root)

    authorization_result = _authorize_actor(resolved_input, config)
    if authorization_result is not None:
        return authorization_result

    if isinstance(resolved_input, ResolvedControlLabel):
        return ReadyControlExecution(
            control=resolved_input,
            config=config,
            workflow_id=_resolve_workflow_id(resolved_input, env),
        )

    return ReadyExecution(
        command=resolved_input,
        config=config,
        resolved_provider=resolve_provider(resolved_input, config, env),
        workflow_id=_resolve_workflow_id(resolved_input, env),
    )


def _authorize_actor(
    resolved_input: ResolvedInput,
    config: VVAIConfig,
) -> SilentSkip | None:
    """event に応じた認可を行う。"""
    if resolved_input.event_name == "local":
        return None

    actor = resolved_input.actor
    if actor is None:
        raise AuthorizationError("認可に必要な actor が見つかりません")

    if actor in config.allowed_users:
        return None

    if isinstance(resolved_input, ResolvedCommand) and _is_internal_bot_label(
        resolved_input,
        config,
    ):
        return None

    if resolved_input.event_name == "issue_comment":
        return SilentSkip(reason="unauthorized_comment")
    if resolved_input.event_name in {"issues", "pull_request"}:
        return SilentSkip(reason="unauthorized_label")
    raise AuthorizationError("この workflow は許可されたユーザーのみ実行できます")


def _is_internal_bot_label(
    resolved_command: ResolvedCommand,
    config: VVAIConfig,
) -> bool:
    """内部 bot による command label 起動かを返す。"""
    if resolved_command.event_name not in {"issues", "pull_request"}:
        return False
    if resolved_command.trigger_label_name is None:
        return False
    actor_id = resolved_command.actor_id
    if actor_id is None:
        return False
    return actor_id in config.internal_bot_ids


def _resolve_workflow_id(
    resolved_command: ResolvedInput,
    env: Mapping[str, str],
) -> str:
    """全 event で使う workflow_id を解決する。"""
    if resolved_command.event_name != "local":
        run_id = _resolve_optional_env_value(
            env.get("GITHUB_RUN_ID"),
            "GITHUB_RUN_ID",
        )
        run_attempt = _resolve_optional_env_value(
            env.get("GITHUB_RUN_ATTEMPT"),
            "GITHUB_RUN_ATTEMPT",
        )
        if run_id is not None:
            if run_attempt is None:
                return f"run-{run_id}"
            return f"run-{run_id}-attempt-{run_attempt}"
        return f"debug-{generate_local_workflow_id()}"
    return generate_local_workflow_id()


def _resolve_optional_env_value(value: str | None, env_name: str) -> str | None:
    """任意の環境変数値を解決する。"""
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        raise PreflightError(f"環境変数 `{env_name}` が空です")
    return stripped
