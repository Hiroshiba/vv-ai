"""Claude provider 実行処理。"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.artifacts.metrics import (
    ClaudeProviderMetrics,
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
)
from vv_ai.artifacts.report import ReportSections
from vv_ai.executions.result import ExecutionResult
from vv_ai.preflight import ReadyExecution
from vv_ai.providers.assets import ProviderAssetDeployError, deploy_claude_provider_assets
from vv_ai.providers.environment import (
    build_sanitized_env,
    extract_mcp_domains,
    get_claude_extra_settings_json,
    resolve_claude_api_key_file_path,
)
from vv_ai.providers.runner import ProviderExecutionError
from vv_ai.providers.sessions import (
    deploy_provider_session_dir,
    resolve_claude_session_dir,
)
from vv_ai.session import SessionStateRef

_CLAUDE_WEB_SEARCH_DISALLOWED_TOOL = "WebSearch"

_DENY_READ_PATHS = [
    "/home/runner/.vv-secrets/**",
    "/proc/**",
    str(Path.home() / ".claude" / "**"),
    str(Path.home() / ".claude.json"),
]


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


def execute_claude(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
    provider_prompt: str,
    skip_api_key_check: bool,
) -> ExecutionResult:
    """Claude Code CLI を実行して ExecutionResult を返す。"""
    if skip_api_key_check:
        api_key_file_path = None
        is_temporary = False
    else:
        api_key_file_path, is_temporary = resolve_claude_api_key_file_path(env)
    try:
        command = _build_claude_command(
            ready_execution,
            api_key_file_path,
            get_claude_extra_settings_json(env),
            provider_prompt,
        )
        sanitized_env = build_sanitized_env(env)

        session = ready_execution.resolved_session
        if session is not None and session.restored_provider_session_path is not None:
            sanitized = str(repo_root).replace("/", "-")
            project_dir = Path.home() / ".claude" / "projects" / sanitized
            deploy_provider_session_dir(
                session.restored_provider_session_path, project_dir
            )
        _deploy_claude_assets_before_execution(env, Path.home() / ".claude")

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
            detail = proc.stderr.strip() or proc.stdout.strip()[:500]
            raise ProviderExecutionError(
                f"Claude Code が終了コード {proc.returncode} で失敗しました"
                + (f": {detail}" if detail else "")
            )

        claude_output = _parse_claude_json_output(proc.stdout)
        provider_session_path = resolve_claude_session_dir(
            repo_root, claude_output.session_id
        )
        return _build_execution_result(
            ready_execution,
            claude_output,
            preflight_duration_seconds,
            execution_duration_seconds,
            provider_session_path,
        )
    finally:
        if is_temporary and api_key_file_path is not None:
            Path(api_key_file_path).unlink(missing_ok=True)


def _build_claude_command(
    ready_execution: ReadyExecution,
    api_key_file_path: str | None,
    extra_settings_json: str | None,
    provider_prompt: str,
) -> list[str]:
    """Claude Code CLI のコマンドリストを返す。"""
    settings_json = _build_claude_settings(api_key_file_path, extra_settings_json)
    session = ready_execution.resolved_session
    restore_strategy = session.restore_strategy if session is not None else "new"

    command: list[str] = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--bare",
        "--permission-mode",
        "acceptEdits",
        "--disallowedTools",
        _CLAUDE_WEB_SEARCH_DISALLOWED_TOOL,
        "--settings",
        settings_json,
    ]

    state_ref = session.state_ref if session is not None else None
    if (
        restore_strategy == "new"
        or state_ref is None
        or state_ref.provider_session_id is None
    ):
        command.extend(["--session-id", str(uuid.uuid4())])
    else:
        command.extend(["--resume", state_ref.provider_session_id])

    command.append(provider_prompt)
    return command


def _build_claude_settings(
    api_key_file_path: str | None,
    extra_settings_json: str | None,
) -> str:
    """Claude Code 用 settings JSON 文字列を返す。"""
    settings: dict = json.loads(extra_settings_json) if extra_settings_json else {}

    extra_allowed_domains: list[str] = (
        settings.get("sandbox", {}).get("network", {}).get("allowedDomains", [])
    )
    mcp_domains = extract_mcp_domains()

    settings["allowUnsandboxedCommands"] = False
    settings["permissions"] = {
        "deny": [f"Read({path})" for path in _DENY_READ_PATHS],
    }
    settings["sandbox"] = {
        "enabled": True,
        "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True,
        "filesystem": {
            "denyRead": _DENY_READ_PATHS,
        },
        "network": {
            "allowedDomains": ["api.github.com"]
            + extra_allowed_domains
            + mcp_domains,
        },
    }
    if api_key_file_path is not None:
        settings["apiKeyHelper"] = f"cat {api_key_file_path}"
    return json.dumps(settings)


def _deploy_claude_assets_before_execution(
    env: Mapping[str, str],
    claude_home: Path,
) -> None:
    """Claude 実行前に provider asset を配置する。"""
    try:
        deploy_claude_provider_assets(env, claude_home)
    except ProviderAssetDeployError as exc:
        raise ProviderExecutionError(
            f"Claude provider asset の配置に失敗しました: {exc}"
        ) from exc


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
    provider_session_path: Path | None,
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
        provider_session_path=provider_session_path,
        allow_edits_notice_posted=False,
        response_text=claude_output.result,
    )
