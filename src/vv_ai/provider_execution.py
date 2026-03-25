"""provider CLI 実行ラッパー。"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.execution import ExecutionResult
from vv_ai.metrics_artifact import (
    ClaudeProviderMetrics,
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.report_artifact import ReportSections
from vv_ai.session import SessionStateRef

_API_KEY_HELPER_ENV = "VV_AI_API_KEY_HELPER_PATH"

_ALLOWED_ENV_KEYS = frozenset(
    [
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "XDG_RUNTIME_DIR",
        "TERM",
        "SHELL",
    ]
)

_DENY_READ_PATHS = [
    "/home/runner/.vv-secrets/**",
    "/proc/**",
    str(Path.home() / ".claude" / "**"),
]


class ProviderExecutionError(Exception):
    """provider 実行に失敗したことを表す例外。"""


class _ClaudeUsage(BaseModel):
    """Claude Code の usage フィールド。"""

    model_config = ConfigDict(extra="allow")

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None


class _ClaudeOutput(BaseModel):
    """Claude Code --output-format json の出力。"""

    model_config = ConfigDict(extra="allow")

    result: str
    session_id: str
    duration_ms: int
    num_turns: int
    is_error: bool
    total_cost_usd: float | None = None
    usage: _ClaudeUsage | None = None
    stop_reason: str | None = None


def execute_provider(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> ExecutionResult:
    """provider CLI を実行して ExecutionResult を返す。"""
    provider_name = ready_execution.resolved_provider.name
    if provider_name == "codex":
        raise ProviderExecutionError("codex の実行は未実装です")
    if provider_name == "claude":
        return _execute_claude(
            repo_root, ready_execution, env, preflight_duration_seconds
        )
    raise AssertionError(f"未対応の provider です: {provider_name}")


def _execute_claude(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> ExecutionResult:
    """Claude Code CLI を実行して ExecutionResult を返す。"""
    command = _build_claude_command(ready_execution, env)
    sanitized_env = _build_sanitized_env(env)

    execution_started_at = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=repo_root,
        env=sanitized_env,
        capture_output=True,
        text=True,
        check=False,
    )
    execution_duration_seconds = time.perf_counter() - execution_started_at

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise ProviderExecutionError(
            f"Claude Code が終了コード {proc.returncode} で失敗しました"
            + (f": {stderr}" if stderr else "")
        )

    claude_output = _parse_claude_json_output(proc.stdout)
    return _build_execution_result(
        ready_execution,
        claude_output,
        preflight_duration_seconds,
        execution_duration_seconds,
    )


def _build_claude_command(
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
) -> list[str]:
    """Claude Code CLI のコマンドリストを返す。"""
    settings_json = _build_claude_settings(env)
    session = ready_execution.resolved_session
    session_mode = session.mode if session is not None else "new"

    command: list[str] = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--bare",
        "--permission-mode",
        "accept",
        "--settings",
        settings_json,
    ]

    state_ref = session.state_ref if session is not None else None
    if (
        session_mode == "new"
        or state_ref is None
        or state_ref.provider_session_id is None
    ):
        command.extend(["--session-id", str(uuid.uuid4())])
    else:
        # inherit / compact: 前回 session を継続する
        # TODO: Claude Code に明示的な compaction API がないため、
        #       compact は現時点では inherit と同じ動作になる
        command.extend(["--resume", state_ref.provider_session_id])

    instruction = ready_execution.command.instruction
    prompt = instruction if instruction is not None else ready_execution.command.command
    command.append(prompt)
    return command


def _build_claude_settings(env: Mapping[str, str]) -> str:
    """Claude Code 用 settings JSON 文字列を返す。"""
    api_key_helper_path = env.get(_API_KEY_HELPER_ENV, "").strip()
    if not api_key_helper_path:
        raise ProviderExecutionError(
            f"Claude Code の認証に必要な環境変数 `{_API_KEY_HELPER_ENV}` が設定されていません"
        )

    settings = {
        "apiKeyHelper": {
            "command": ["cat", api_key_helper_path],
        },
        "allowUnsandboxedCommands": False,
        "sandbox": {
            "enabled": True,
            "filesystem": {
                "denyRead": _DENY_READ_PATHS,
            },
        },
    }
    return json.dumps(settings)


def _build_sanitized_env(env: Mapping[str, str]) -> dict[str, str]:
    """AI プロセスに渡す環境変数をホワイトリストで絞り込む。"""
    return {key: value for key, value in env.items() if key in _ALLOWED_ENV_KEYS}


def _parse_claude_json_output(stdout: str) -> _ClaudeOutput:
    """Claude Code の JSON 出力を解析する。"""
    stripped = stdout.strip()
    if not stripped:
        raise ProviderExecutionError("Claude Code の出力が空でした")

    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProviderExecutionError(
            f"Claude Code の出力が JSON として不正です: {exc}"
        ) from exc

    try:
        return _ClaudeOutput.model_validate(raw)
    except Exception as exc:
        raise ProviderExecutionError(
            f"Claude Code の出力形式が想定と異なります: {exc}"
        ) from exc


def _build_execution_result(
    ready_execution: ReadyExecution,
    claude_output: _ClaudeOutput,
    preflight_duration_seconds: float,
    execution_duration_seconds: float,
) -> ExecutionResult:
    """Claude Code 出力から ExecutionResult を組み立てる。"""
    usage_data = claude_output.usage
    usage = MetricsUsage(
        input_tokens=usage_data.input_tokens if usage_data is not None else None,
        cached_input_tokens=(
            usage_data.cache_read_input_tokens if usage_data is not None else None
        ),
        output_tokens=usage_data.output_tokens if usage_data is not None else None,
        cost_usd=claude_output.total_cost_usd,
    )

    active_time = claude_output.duration_ms / 1000.0
    behavior = MetricsBehavior(
        total_turns=claude_output.num_turns,
        active_time_seconds=active_time,
    )

    claude_metrics = ClaudeProviderMetrics(
        session_count=1,
        input_tokens=usage_data.input_tokens if usage_data is not None else None,
        cached_input_tokens=(
            usage_data.cache_read_input_tokens if usage_data is not None else None
        ),
        output_tokens=usage_data.output_tokens if usage_data is not None else None,
        cost_usd=claude_output.total_cost_usd,
        active_time_seconds=active_time,
    )

    command_name = ready_execution.command.command
    result_text = claude_output.result
    report_sections = ReportSections(
        summary=f"`{command_name}` を Claude Code で実行した。",
        changes=result_text[:500] if result_text else "出力なし。",
        decisions="Claude Code の判断に基づいて実行した。",
        validation=f"Claude Code が正常終了した。turns={claude_output.num_turns}",
        risks_open_questions="GitHub への反映は別途 workflow ステップで行う。",
        next_actions="実行結果をレビューし、必要に応じて追加対応を行う。",
        notes=f"session_id={claude_output.session_id}",
    )

    status = "failure" if claude_output.is_error else "success"

    return ExecutionResult(
        status=status,
        report_sections=report_sections,
        usage=usage,
        behavior=behavior,
        tools={},
        steps={
            "preflight": StepMetric(duration_seconds=preflight_duration_seconds),
            "execution": StepMetric(duration_seconds=execution_duration_seconds),
        },
        provider_specific=ProviderSpecificMetrics(claude=claude_metrics),
        state_ref=SessionStateRef(provider_session_id=claude_output.session_id),
        provider_session_path=None,
        allow_edits_notice_posted=False,
    )
