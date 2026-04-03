"""provider CLI 実行ラッパー。"""

from __future__ import annotations

import json
import shutil
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
_CODEX_HOME_ENV = "VV_CODEX_HOME"
_ANTHROPIC_API_KEY_ENV = "VV_ANTHROPIC_API_KEY"
_ANTHROPIC_API_KEY_FILE_ENV = "VV_ANTHROPIC_API_KEY_FILE"
_CLAUDE_EXTRA_SETTINGS_ENV = "VV_CLAUDE_SETTINGS"

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
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            provider_prompt,
            skip,
        )
    if provider_name == "claude":
        return _execute_claude(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            provider_prompt,
            skip,
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

        session = ready_execution.resolved_session
        if session is not None and session.restored_provider_session_path is not None:
            codex_home = codex_env.get("CODEX_HOME", str(Path.home() / ".codex"))
            _deploy_provider_session_dir(
                session.restored_provider_session_path, Path(codex_home)
            )

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
        provider_session_path = _resolve_codex_session_dir(codex_env)
        return _build_codex_execution_result(
            ready_execution,
            codex_output,
            preflight_duration_seconds,
            execution_duration_seconds,
            provider_session_path,
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


def _build_codex_env(
    env: Mapping[str, str], skip_api_key_check: bool
) -> dict[str, str]:
    """Codex プロセスに渡す環境変数を構築する。"""
    sanitized = _build_sanitized_env(env)
    if skip_api_key_check:
        return sanitized

    api_key = _try_resolve_api_key(
        env, _CODEX_OPENAI_API_KEY_FILE_ENV, _CODEX_OPENAI_API_KEY_ENV
    )
    if api_key is not None:
        sanitized["OPENAI_API_KEY"] = api_key
        return sanitized

    # TODO: GitHub-hosted runner (ephemeral) では実行後に更新された auth.json を
    #       secret に書き戻す仕組みが必要。現時点では毎回 secret から注入する運用。
    codex_home = env.get(_CODEX_HOME_ENV, "").strip()
    if codex_home and (Path(codex_home) / "auth.json").is_file():
        sanitized["CODEX_HOME"] = codex_home
        return sanitized

    raise ProviderExecutionError(
        f"認証に必要な環境変数 `{_CODEX_OPENAI_API_KEY_FILE_ENV}` /"
        f" `{_CODEX_OPENAI_API_KEY_ENV}` / `{_CODEX_HOME_ENV}` のいずれも設定されていません"
    )


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
    provider_session_path: Path | None,
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
        provider_session_path=provider_session_path,
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
        command = _build_claude_command(
            ready_execution,
            api_key_file_path,
            env.get(_CLAUDE_EXTRA_SETTINGS_ENV),
            provider_prompt,
        )
        sanitized_env = _build_sanitized_env(env)

        session = ready_execution.resolved_session
        if session is not None and session.restored_provider_session_path is not None:
            sanitized = str(repo_root).replace("/", "-")
            project_dir = Path.home() / ".claude" / "projects" / sanitized
            _deploy_provider_session_dir(
                session.restored_provider_session_path, project_dir
            )

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
        provider_session_path = _resolve_claude_session_dir(
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


def _build_claude_settings(
    api_key_file_path: str | None, extra_settings_json: str | None
) -> str:
    """Claude Code 用 settings JSON 文字列を返す。"""
    # TODO: これだとClaude内のコードから見えてしまうので、真に隠すには他の方法が必要。
    settings: dict = json.loads(extra_settings_json) if extra_settings_json else {}

    # セキュリティ設定を最後に適用。extra_settings_json による上書きを防ぐ。
    settings["allowUnsandboxedCommands"] = False
    settings["permissions"] = {
        "deny": [f"Read({path})" for path in _DENY_READ_PATHS],
    }
    settings["sandbox"] = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "filesystem": {
            "denyRead": _DENY_READ_PATHS,
        },
    }
    if api_key_file_path is not None:
        settings["apiKeyHelper"] = f"cat {api_key_file_path}"
    return json.dumps(settings)


def _try_resolve_api_key(
    env: Mapping[str, str],
    file_env: str,
    value_env: str,
) -> str | None:
    """ファイルパス env 優先、生キー値 env フォールバックで API キーを返す。認証なしなら None を返す。"""
    file_path = env.get(file_env, "").strip()
    if file_path:
        if not Path(file_path).is_file():
            return None
        content = Path(file_path).read_text(encoding="utf-8").strip()
        return content if content else None
    value = env.get(value_env, "").strip()
    return value if value else None


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


def _resolve_claude_session_dir(repo_root: Path, session_id: str) -> Path | None:
    """Claude Code のセッションファイルを一時ディレクトリに集めて返す。"""
    sanitized = str(repo_root).replace("/", "-")
    project_dir = Path.home() / ".claude" / "projects" / sanitized
    if not project_dir.is_dir():
        return None

    session_jsonl = project_dir / f"{session_id}.jsonl"
    if not session_jsonl.is_file():
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="vv-ai-claude-session-"))
    try:
        shutil.copy2(session_jsonl, tmp_dir / session_jsonl.name)

        session_subdir = project_dir / session_id
        if session_subdir.is_dir():
            shutil.copytree(session_subdir, tmp_dir / session_id)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return tmp_dir


def _resolve_codex_session_dir(codex_env: dict[str, str]) -> Path | None:
    """Codex のセッションファイルを一時ディレクトリに集めて返す。"""
    codex_home = Path(codex_env.get("CODEX_HOME", str(Path.home() / ".codex")))
    if not codex_home.is_dir():
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="vv-ai-codex-session-"))
    try:
        ignore = shutil.ignore_patterns("auth.json", "config.toml")
        for item in codex_home.iterdir():
            dest = tmp_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=ignore)
            elif item.name not in ("auth.json", "config.toml"):
                shutil.copy2(item, dest)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return tmp_dir


def _deploy_provider_session_dir(source: str, destination: Path) -> None:
    """復元されたセッションファイルを provider が期待する場所にコピーする。"""
    source_path = Path(source)
    destination.mkdir(parents=True, exist_ok=True)
    for item in source_path.iterdir():
        dest_item = destination / item.name
        if item.is_dir():
            if dest_item.exists():
                shutil.rmtree(dest_item)
            shutil.copytree(item, dest_item)
        else:
            shutil.copy2(item, dest_item)


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
