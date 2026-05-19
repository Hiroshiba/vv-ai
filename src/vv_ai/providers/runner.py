"""provider 実行の振り分け。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from vv_ai.executions.result import ExecutionResult
from vv_ai.preflight import ReadyExecution


class ProviderExecutionError(Exception):
    """provider 実行に失敗したことを表す例外。"""


def execute_provider(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
    provider_prompt: str,
) -> ExecutionResult:
    """provider CLI を実行して ExecutionResult を返す。"""
    provider_name = ready_execution.resolved_provider.name
    skip = ready_execution.command.skip_api_key_check
    if provider_name == "codex":
        from vv_ai.providers.codex import execute_codex

        return execute_codex(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            provider_prompt,
            skip,
        )
    if provider_name == "claude":
        from vv_ai.providers.claude import execute_claude

        return execute_claude(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            provider_prompt,
            skip,
        )
    raise AssertionError(f"未対応の provider です: {provider_name}")
