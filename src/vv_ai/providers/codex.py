"""Codex provider 実行処理。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.artifacts.metrics import (
    CodexProviderMetrics,
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
)
from vv_ai.artifacts.report import ReportSections
from vv_ai.executions.result import ExecutionResult
from vv_ai.workflow.preflight import ReadyExecution
from vv_ai.providers.assets import (
    ProviderAssetDeployError,
    copy_codex_provider_assets_to_work_dir,
    deploy_codex_provider_assets,
    sync_codex_provider_assets_from_work_dir,
)
from vv_ai.providers.environment import build_codex_env, resolve_codex_home_from_env
from vv_ai.providers.runner import ProviderExecutionError
from vv_ai.providers.sessions import deploy_codex_session_dir, resolve_codex_session_dir
from vv_ai.sessions.models import SessionStateRef

_CODEX_SHELL_ENV_ALLOWLIST = json.dumps(
    ["PATH", "HOME", "USER", "LANG", "TERM", "SHELL", "TMPDIR", "GH_TOKEN"]
)
_CODEX_WEB_SEARCH_DISABLE_OPTIONS = (
    "--disable",
    "web_search_request",
    "--disable",
    "web_search_cached",
)
_CODEX_WORK_DIR_RELATIVE_PATH = Path(".vv-ai/codex-work")


class _CodexOutput(BaseModel):
    """Codex CLI 実行結果のサマリ。"""

    model_config = ConfigDict(extra="allow")

    result: str
    thread_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_turns: int | None = None


def execute_codex(
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
        codex_env = build_codex_env(env, skip_api_key_check)
        codex_home = resolve_codex_home_from_env(codex_env)

        session = ready_execution.resolved_session
        if session is not None and session.restored_provider_session_path is not None:
            deploy_codex_session_dir(session.restored_provider_session_path, codex_home)
        _deploy_codex_assets_before_execution(env, codex_home)
        work_dir = _prepare_codex_work_dir(repo_root)
        codex_provider_prompt = _build_codex_provider_prompt(
            provider_prompt,
            _CODEX_WORK_DIR_RELATIVE_PATH,
        )
        command = _build_codex_command(
            ready_execution,
            output_file,
            codex_provider_prompt,
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
        provider_session_path = resolve_codex_session_dir(codex_env)
        result = _build_codex_execution_result(
            ready_execution,
            codex_output,
            preflight_duration_seconds,
            execution_duration_seconds,
            provider_session_path,
        )
        _sync_codex_work_dir(repo_root, work_dir)
        return result
    finally:
        output_file.unlink(missing_ok=True)


def _build_codex_command(
    ready_execution: ReadyExecution,
    output_file: Path,
    provider_prompt: str,
) -> list[str]:
    """Codex CLI のコマンドリストを返す。"""
    session = ready_execution.resolved_session
    restore_strategy = session.restore_strategy if session is not None else "new"
    state_ref = session.state_ref if session is not None else None

    shared_options: list[str] = [
        "-c",
        'approval_policy="never"',
        "-c",
        'sandbox_mode="workspace-write"',
        "-c",
        "sandbox_workspace_write.network_access=true",
        "-c",
        "model_reasoning_effort=high",
        *_CODEX_WEB_SEARCH_DISABLE_OPTIONS,
        "--json",
        "-c",
        'shell_environment_policy.inherit="all"',
        "-c",
        f"shell_environment_policy.include_only={_CODEX_SHELL_ENV_ALLOWLIST}",
        "-o",
        str(output_file),
    ]

    if (
        restore_strategy != "new"
        and state_ref is not None
        and state_ref.provider_session_id is not None
    ):
        return [
            "codex",
            "exec",
            "resume",
            state_ref.provider_session_id,
            *shared_options,
            "--",
            provider_prompt,
        ]

    return ["codex", "exec", *shared_options, "--", provider_prompt]


def _deploy_codex_assets_before_execution(
    env: Mapping[str, str],
    codex_home: Path,
) -> None:
    """Codex 実行前に provider asset を配置する。"""
    try:
        deploy_codex_provider_assets(env, codex_home)
    except ProviderAssetDeployError as exc:
        raise ProviderExecutionError(
            f"Codex provider asset の配置に失敗しました: {exc}"
        ) from exc


def _resolve_codex_work_dir(repo_root: Path) -> Path:
    """Codex 用 provider asset の作業用ディレクトリを返す。"""
    return repo_root / _CODEX_WORK_DIR_RELATIVE_PATH


def _prepare_codex_work_dir(repo_root: Path) -> Path:
    """Codex 用 provider asset の作業用ディレクトリを準備する。"""
    work_dir = _resolve_codex_work_dir(repo_root)
    try:
        copy_codex_provider_assets_to_work_dir(repo_root, work_dir)
    except ProviderAssetDeployError as exc:
        raise ProviderExecutionError(
            f"Codex provider asset 作業用ディレクトリの準備に失敗しました: {exc}"
        ) from exc
    return work_dir


def _sync_codex_work_dir(repo_root: Path, work_dir: Path) -> None:
    """Codex 用 provider asset の作業用ディレクトリを .codex へ同期する。"""
    try:
        sync_codex_provider_assets_from_work_dir(repo_root, work_dir)
    except ProviderAssetDeployError as exc:
        raise ProviderExecutionError(
            f"Codex provider asset 作業用ディレクトリの同期に失敗しました: {exc}"
        ) from exc


def _build_codex_provider_prompt(provider_prompt: str, work_dir: Path) -> str:
    """Codex provider 固有の作業用ディレクトリ指示を追加する。"""
    work_dir_text = work_dir.as_posix()
    return "\n\n".join(
        [
            provider_prompt,
            "\n".join(
                [
                    "Codex provider asset の編集指示",
                    "",
                    "- `.codex/` は直接編集しないでください。",
                    "- Codex 用 provider asset を変更する場合は "
                    f"`{work_dir_text}/AGENTS.md`、`{work_dir_text}/skills/`、"
                    f"`{work_dir_text}/agents/` を編集してください。",
                    f"- `{work_dir_text}/` は作業用 mirror です。"
                    "この配下のファイルは git に追加しないでください。",
                    f"- 実行後に vv-ai が `{work_dir_text}/` から `.codex/` へ同期します。",
                ]
            ),
        ]
    )


def _parse_codex_jsonl(jsonl_stdout: str, result_text: str) -> _CodexOutput:
    """Codex CLI の JSONL 出力を解析してサマリを返す。"""
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
