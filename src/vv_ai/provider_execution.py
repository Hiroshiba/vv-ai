"""provider CLI 実行ラッパー。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.execution import ExecutionResult
from vv_ai.metrics_artifact import (
    ClaudeProviderMetrics,
    CodexProviderMetrics,
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.report_artifact import ReportSections
from vv_ai.session import SessionStateRef

_CODEX_OPENAI_API_KEY_ENV = "VV_OPENAI_API_KEY"
_CODEX_OPENAI_API_KEY_FILE_ENV = "VV_OPENAI_API_KEY_FILE"
_ANTHROPIC_API_KEY_ENV = "VV_ANTHROPIC_API_KEY"
_ANTHROPIC_API_KEY_FILE_ENV = "VV_ANTHROPIC_API_KEY_FILE"

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

_CODEX_SHELL_ENV_ALLOWLIST = json.dumps(
    ["PATH", "HOME", "USER", "LANG", "TERM", "SHELL", "TMPDIR"]
)

_DENY_READ_PATHS = [
    "/home/runner/.vv-secrets/**",
    "/proc/**",
    str(Path.home() / ".claude" / "**"),
]


class ProviderExecutionError(Exception):
    """provider 実行に失敗したことを表す例外。"""


class _CodexOutput(BaseModel):
    """Codex CLI 実行結果のサマリ。"""

    model_config = ConfigDict(extra="allow")

    result: str
    thread_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_turns: int | None = None


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
    provider_prompt: str,
) -> ExecutionResult:
    """provider CLI を実行して ExecutionResult を返す。"""
    provider_name = ready_execution.resolved_provider.name
    skip = ready_execution.command.skip_api_key_check
    if provider_name == "codex":
        return _execute_codex(
            repo_root, ready_execution, env, preflight_duration_seconds, provider_prompt, skip
        )
    if provider_name == "claude":
        return _execute_claude(
            repo_root, ready_execution, env, preflight_duration_seconds, provider_prompt, skip
        )
    raise AssertionError(f"未対応の provider です: {provider_name}")


def _execute_codex(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
    provider_prompt: str,
    skip_api_key_check: bool,
) -> ExecutionResult:
    """Codex CLI を実行して ExecutionResult を返す。"""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        output_file = Path(f.name)

    try:
        command = _build_codex_command(ready_execution, output_file, provider_prompt)
        codex_env = _build_codex_env(env, skip_api_key_check)

        execution_started_at = time.perf_counter()
        proc = subprocess.run(
            command,
            cwd=repo_root,
            env=codex_env,
            capture_output=True,
            text=True,
            check=False,
        )
        execution_duration_seconds = time.perf_counter() - execution_started_at

        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            raise ProviderExecutionError(
                f"Codex が終了コード {proc.returncode} で失敗しました"
                + (f": {stderr}" if stderr else "")
            )

        file_content = output_file.read_text()
        result_text = file_content if file_content else proc.stdout
        codex_output = _parse_codex_jsonl(proc.stdout, result_text)
        return _build_codex_execution_result(
            ready_execution,
            codex_output,
            preflight_duration_seconds,
            execution_duration_seconds,
        )
    finally:
        output_file.unlink(missing_ok=True)


def _build_codex_command(
    ready_execution: ReadyExecution,
    output_file: Path,
    provider_prompt: str,
) -> list[str]:
    """Codex CLI のコマンドリストを返す。"""
    session = ready_execution.resolved_session
    session_mode = session.mode if session is not None else "new"
    state_ref = session.state_ref if session is not None else None

    base_options: list[str] = [
        "--full-auto",
        "--json",
        "-c",
        'shell_environment_policy.inherit="all"',
        "-c",
        f"shell_environment_policy.include_only={_CODEX_SHELL_ENV_ALLOWLIST}",
        "-o",
        str(output_file),
    ]

    if (
        session_mode != "new"
        and state_ref is not None
        and state_ref.provider_session_id is not None
    ):
        # TODO: compact は inherit と同じ動作になる（Codex に compact 相当の API がないため）
        return [
            "codex",
            "exec",
            "resume",
            state_ref.provider_session_id,
            *base_options,
            "--",
            provider_prompt,
        ]

    return ["codex", "exec", *base_options, "--", provider_prompt]


def _build_codex_env(env: Mapping[str, str], skip_api_key_check: bool) -> dict[str, str]:
    """Codex プロセスに渡す環境変数を構築する。"""
    sanitized = _build_sanitized_env(env)
    if not skip_api_key_check:
        api_key = _resolve_api_key(env, _CODEX_OPENAI_API_KEY_FILE_ENV, _CODEX_OPENAI_API_KEY_ENV)
        sanitized["OPENAI_API_KEY"] = api_key
    return sanitized


def _parse_codex_jsonl(jsonl_stdout: str, result_text: str) -> _CodexOutput:
    """Codex CLI の JSONL 出力を解析してサマリを返す。"""
    # TODO: 実際の JSONL スキーマを確認後にフィールド抽出を更新する
    thread_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_turns: int | None = None

    for line in jsonl_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        if thread_id is None:
            for key in ("session_id", "thread_id"):
                val = event.get(key)
                if val and isinstance(val, str):
                    thread_id = val
                    break

        for usage_key in ("usage", "token_usage"):
            usage = event.get(usage_key)
            if isinstance(usage, dict):
                if input_tokens is None:
                    input_tokens = usage.get("input_tokens")
                if output_tokens is None:
                    output_tokens = usage.get("output_tokens")

    return _CodexOutput(
        result=result_text,
        thread_id=thread_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_turns=total_turns,
    )


def _build_codex_execution_result(
    ready_execution: ReadyExecution,
    codex_output: _CodexOutput,
    preflight_duration_seconds: float,
    execution_duration_seconds: float,
) -> ExecutionResult:
    """Codex 出力から ExecutionResult を組み立てる。"""
    usage = MetricsUsage(
        input_tokens=codex_output.input_tokens,
        cached_input_tokens=None,
        output_tokens=codex_output.output_tokens,
        cost_usd=None,
    )

    behavior = MetricsBehavior(
        total_turns=codex_output.total_turns,
        active_time_seconds=None,
    )

    codex_metrics = CodexProviderMetrics(
        thread_id=codex_output.thread_id,
        input_tokens=codex_output.input_tokens,
        cached_input_tokens=None,
        output_tokens=codex_output.output_tokens,
    )

    command_name = ready_execution.command.command
    result_text = codex_output.result
    report_sections = ReportSections(
        summary=f"`{command_name}` を Codex で実行した。",
        changes=result_text[:500] if result_text else "出力なし。",
        decisions="Codex の判断に基づいて実行した。",
        validation="Codex が正常終了した。",
        risks_open_questions="GitHub への反映は別途 workflow ステップで行う。",
        next_actions="実行結果をレビューし、必要に応じて追加対応を行う。",
        notes=f"thread_id={codex_output.thread_id}",
    )

    return ExecutionResult(
        status="success",
        report_sections=report_sections,
        usage=usage,
        behavior=behavior,
        tools={},
        steps={
            "preflight": StepMetric(duration_seconds=preflight_duration_seconds),
            "execution": StepMetric(duration_seconds=execution_duration_seconds),
        },
        provider_specific=ProviderSpecificMetrics(codex=codex_metrics),
        state_ref=SessionStateRef(provider_session_id=codex_output.thread_id),
        provider_session_path=None,
        allow_edits_notice_posted=False,
        response_text=codex_output.result,
    )


def _execute_claude(
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
        api_key_file_path, is_temporary = _resolve_api_key_file_path(
            env, _ANTHROPIC_API_KEY_FILE_ENV, _ANTHROPIC_API_KEY_ENV
        )
    try:
        command = _build_claude_command(ready_execution, api_key_file_path, provider_prompt)
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
    finally:
        if is_temporary:
            Path(api_key_file_path).unlink(missing_ok=True)


def _build_claude_command(
    ready_execution: ReadyExecution,
    api_key_file_path: str | None,
    provider_prompt: str,
) -> list[str]:
    """Claude Code CLI のコマンドリストを返す。"""
    settings_json = _build_claude_settings(api_key_file_path)
    session = ready_execution.resolved_session
    session_mode = session.mode if session is not None else "new"

    command: list[str] = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--bare",
        "--permission-mode",
        "acceptEdits",
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

    command.append(provider_prompt)
    return command


def _build_claude_settings(api_key_file_path: str | None) -> str:
    """Claude Code 用 settings JSON 文字列を返す。"""
    settings: dict = {
        "allowUnsandboxedCommands": False,
        "permissions": {
            "deny": [f"Read({path})" for path in _DENY_READ_PATHS],
        },
        "sandbox": {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {
                "denyRead": _DENY_READ_PATHS,
            },
        },
    }
    if api_key_file_path is not None:
        settings["apiKeyHelper"] = {"command": ["cat", api_key_file_path]}
    return json.dumps(settings)


def _resolve_api_key(
    env: Mapping[str, str],
    file_env: str,
    value_env: str,
) -> str:
    """ファイルパス env 優先、生キー値 env フォールバックで API キーを返す。"""
    file_path = env.get(file_env, "").strip()
    if file_path:
        path = Path(file_path)
        if not path.is_file():
            raise ProviderExecutionError(
                f"`{file_env}` で指定されたファイル `{file_path}` が見つかりません"
            )
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ProviderExecutionError(
                f"`{file_env}` で指定されたファイル `{file_path}` が空です"
            )
        return content
    value = env.get(value_env, "").strip()
    if not value:
        raise ProviderExecutionError(
            f"認証に必要な環境変数 `{file_env}` または `{value_env}` が設定されていません"
        )
    return value


def _resolve_api_key_file_path(
    env: Mapping[str, str],
    file_env: str,
    value_env: str,
) -> tuple[str, bool]:
    """API キーのファイルパスと一時ファイルかどうかを返す。"""
    file_path = env.get(file_env, "").strip()
    if file_path:
        if not Path(file_path).is_file():
            raise ProviderExecutionError(
                f"`{file_env}` で指定されたファイル `{file_path}` が見つかりません"
            )
        return file_path, False
    value = env.get(value_env, "").strip()
    if not value:
        raise ProviderExecutionError(
            f"認証に必要な環境変数 `{file_env}` または `{value_env}` が設定されていません"
        )
    tmp = tempfile.NamedTemporaryFile(
        prefix="vv-ai-key-", suffix=".txt", delete=False, mode="w"
    )
    tmp.write(value)
    tmp.close()
    Path(tmp.name).chmod(0o400)
    return tmp.name, True


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
        response_text=claude_output.result,
    )
