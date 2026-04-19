"""vv-ai の CLI エントリポイント。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from vv_ai.config import VVAIConfigError, find_repo_root
from vv_ai.execution import (
    ExecutionArtifactError,
    ExecutionResult,
    SavedExecutionArtifacts,
    save_execution_artifacts,
)
from vv_ai.github import GitHubPullRequest
from vv_ai.input import CLIInput, InputError, build_raw_input_from_cli
from vv_ai.metrics_artifact import (
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
)
from vv_ai.preflight import (
    PreflightError,
    ReadyExecution,
    SilentSkip,
    run_preflight,
)
from vv_ai.provider import ProviderResolutionError
from vv_ai.command_handler import run_command
from vv_ai.session import SessionKey
from vv_ai.session_artifact import SessionArtifactError, fork_session_artifact
from vv_ai.report_artifact import ReportSections
from vv_ai.resolve import ResolutionError, resolve_raw_input
from vv_ai.session import SessionResolutionError, SessionStateRef, resolve_session
from vv_ai.target import TargetResolutionError, resolve_target
from vv_ai.verify import VerifyResult, run_verify


def build_parser() -> argparse.ArgumentParser:
    """最小の CLI パーサーを構築する。"""
    parser = argparse.ArgumentParser(
        prog="vv-ai",
        description=(
            "GitHub Actions とローカル実行の両方に対応する vv-ai CLI の起動入口です。"
        ),
    )
    parser.add_argument(
        "--event",
        choices=["issue_comment", "workflow_dispatch", "local"],
        default="local",
        help="入力の起動元を指定します。",
    )
    parser.add_argument(
        "--event-file",
        help="GitHub event payload JSON を読み込んで再現実行します。",
    )
    parser.add_argument(
        "--command",
        choices=["reply", "plan", "implement", "review", "issue"],
        help="実行コマンドを指定します。",
    )
    parser.add_argument("--instruction", help="自然言語の指示本文です。")
    parser.add_argument(
        "--target-url", help="対象の Issue / PR URL またはローカルパスです。"
    )
    parser.add_argument(
        "--target-type",
        choices=["issue", "pr"],
        help="対象種別を指定します。",
    )
    parser.add_argument(
        "--target-number",
        type=int,
        help="対象 Issue / PR の番号を指定します。",
    )
    parser.add_argument(
        "--provider",
        choices=["codex", "claude"],
        help="使用する AI プロバイダを指定します。",
    )
    parser.add_argument(
        "--session_mode",
        choices=["inherit", "inherit_or_new", "compact", "new"],
        help="セッション継続方式を指定します。未指定時は inherit_or_new です。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="GitHub への外部反映を行わずに実行します。",
    )
    parser.add_argument(
        "--repo",
        help="Issue 作成先のリポジトリを org/repo 形式で指定します。",
    )
    parser.add_argument(
        "--skip-api-key-check",
        action="store_true",
        default=False,
        help="API キーの存在確認をスキップします。ローカル環境で provider が自前の認証を持つ場合に使います。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI を起動し、終了コードを返す。"""
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] == "verify":
        return _run_verify_subcommand(argv_list[1:])

    started_at = time.perf_counter()
    parser = build_parser()
    namespace = parser.parse_args(argv_list)

    try:
        cli_input = CLIInput.model_validate(vars(namespace))
        raw_input = build_raw_input_from_cli(cli_input)
        resolved_command = resolve_raw_input(raw_input)
        repo_root = find_repo_root(Path.cwd())
        preflight_result = run_preflight(repo_root, resolved_command, os.environ)
        if isinstance(preflight_result, ReadyExecution):
            resolved_target_command = resolve_target(
                repo_root, preflight_result.command
            )
            resolved_session = resolve_session(
                repo_root,
                preflight_result.workflow_id,
                resolved_target_command,
                preflight_result.resolved_provider,
                os.environ,
            )
            preflight_result = preflight_result.model_copy(
                update={
                    "command": resolved_target_command,
                    "resolved_session": resolved_session,
                }
            )
    except (
        ValidationError,
        InputError,
        ResolutionError,
        VVAIConfigError,
        PreflightError,
        ProviderResolutionError,
        SessionResolutionError,
        TargetResolutionError,
    ) as exc:
        print(f"入力エラー: {exc}", file=sys.stderr)
        return 2

    if isinstance(preflight_result, SilentSkip):
        return _handle_silent_skip(preflight_result)

    return _run_ready_execution(
        repo_root,
        preflight_result,
        os.environ,
        preflight_duration_seconds=time.perf_counter() - started_at,
    )


def _run_verify_subcommand(verify_argv: Sequence[str]) -> int:
    """`vv-ai verify` を実行し、判定結果を stdout に JSON で出力する。"""
    parser = argparse.ArgumentParser(
        prog="vv-ai verify",
        description=(
            "workflow に届いた event を本体実行すべきかを vv-ai.yml と event "
            "payload だけから判定する。"
        ),
    )
    parser.add_argument(
        "--event",
        choices=["issue_comment", "workflow_dispatch"],
        required=True,
        help="event 種別を指定する。",
    )
    parser.add_argument(
        "--event-file",
        type=Path,
        required=True,
        help="GitHub event payload JSON のパスを指定する。",
    )
    namespace = parser.parse_args(verify_argv)

    try:
        repo_root = find_repo_root(Path.cwd())
        result = run_verify(namespace.event, namespace.event_file, repo_root)
    except (VVAIConfigError, InputError) as exc:
        print(f"verify エラー: {exc}", file=sys.stderr)
        return 2

    print(_format_verify_result(result))
    return 0


def _format_verify_result(result: VerifyResult) -> str:
    """VerifyResult を 1 行 JSON に整形する。"""
    return json.dumps(result.model_dump(exclude_none=True), ensure_ascii=False)


def _handle_silent_skip(result: SilentSkip) -> int:
    """silent skip を処理する。"""
    if result.reason != "unauthorized_comment":
        raise AssertionError(f"未対応の silent skip 理由です: {result.reason}")
    return 0


def _format_ready_message(result: ReadyExecution) -> str:
    """preflight 成功時の確認用メッセージを組み立てる。"""
    return (
        "preflight 解決完了: "
        f"event={result.command.event_name}, "
        f"command={result.command.command}, "
        f"provider={result.provider}, "
        f"provider_source={result.provider_source}"
        f"{_format_workflow_id_suffix(result)}"
        f"{_format_target_suffix(result)}"
        f"{_format_session_suffix(result)}"
    )


def _format_workflow_id_suffix(result: ReadyExecution) -> str:
    """local workflow_id があれば確認用メッセージへ含める。"""
    return f", workflow_id={result.workflow_id}"


def _format_target_suffix(result: ReadyExecution) -> str:
    """解決済み target があれば確認用メッセージへ含める。"""
    target = result.command.target
    if target is None:
        return ""
    return f", target={target.canonical_id}"


def _format_session_suffix(result: ReadyExecution) -> str:
    """解決済み session があれば確認用メッセージへ含める。"""
    session = result.resolved_session
    if session is None:
        return ""
    restore_suffix = "new"
    if session.restore_manifest is not None:
        restore_suffix = (
            f"{session.restore_strategy}:{session.restore_manifest.workflow_id}"
        )
    return (
        f", session_mode={session.requested_mode}, "
        f"session_lane={session.lane}, "
        f"session_key={session.key.canonical_key}, "
        f"session_restore={restore_suffix}"
    )


def _run_ready_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    *,
    preflight_duration_seconds: float,
) -> int:
    """実行本体と artifact 保存を行う。"""
    ready_message = _format_ready_message(ready_execution)
    execution_started_at = time.perf_counter()
    runtime_error: BaseException | None = None
    exit_code = 0

    created_pr = None
    try:
        execution_result, created_pr = run_command(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
        )
    except KeyboardInterrupt as exc:
        runtime_error = exc
        exit_code = 130
        execution_result = _build_cancelled_result(
            ready_execution,
            preflight_duration_seconds,
            execution_duration_seconds=time.perf_counter() - execution_started_at,
        )
    except Exception as exc:
        runtime_error = exc
        exit_code = 1
        execution_result = _build_failure_result(
            ready_execution,
            exc,
            preflight_duration_seconds,
            execution_duration_seconds=time.perf_counter() - execution_started_at,
        )

    try:
        saved_artifacts = save_execution_artifacts(
            repo_root,
            ready_execution,
            env,
            execution_result,
        )
    except ExecutionArtifactError as exc:
        print(f"artifact 保存エラー: {exc}", file=sys.stderr)
        return 1

    if created_pr is not None and _should_rebind_session(ready_execution):
        try:
            _fork_session_for_pr(repo_root, ready_execution, saved_artifacts, created_pr.number)
        except SessionArtifactError as exc:
            print(f"session fork エラー: {exc}", file=sys.stderr)
            return 1

    if runtime_error is None:
        print(ready_message)
        return 0 if execution_result.status == "success" else 1
    if isinstance(runtime_error, KeyboardInterrupt):
        print("実行を中断しました", file=sys.stderr)
        return exit_code

    print(f"実行エラー: {_format_exception(runtime_error)}", file=sys.stderr)
    return exit_code


def _build_cancelled_result(
    ready_execution: ReadyExecution,
    preflight_duration_seconds: float,
    execution_duration_seconds: float,
) -> ExecutionResult:
    """中断時の保存結果を返す。"""
    return ExecutionResult(
        status="cancelled",
        report_sections=ReportSections(
            summary=f"`{ready_execution.command.command}` 実行中に処理を中断した。",
            changes="preflight と実行準備までは完了しており、中断時点の状態を保存した。",
            decisions="中断でも後続調査に使えるよう 3 種 artifact の保存を優先した。",
            validation="KeyboardInterrupt を cancel として扱った。",
            risks_open_questions=(
                "GitHub Actions 上で job 自体が停止された場合の後処理は"
                "workflow 側実装が必要。"
            ),
            next_actions="workflow の always 実行と provider 実行本体を接続する。",
            notes="provider session は未保存である。",
        ),
        usage=MetricsUsage(),
        behavior=MetricsBehavior(active_time_seconds=execution_duration_seconds),
        tools={},
        steps=_build_steps(
            preflight_duration_seconds,
            execution_duration_seconds,
        ),
        provider_specific=ProviderSpecificMetrics(),
        state_ref=SessionStateRef(),
        provider_session_path=None,
        allow_edits_notice_posted=False,
        response_text=None,
    )


def _build_failure_result(
    ready_execution: ReadyExecution,
    error: Exception,
    preflight_duration_seconds: float,
    execution_duration_seconds: float,
) -> ExecutionResult:
    """失敗時の保存結果を返す。"""
    detail = _format_exception(error)
    return ExecutionResult(
        status="failure",
        report_sections=ReportSections(
            summary=f"`{ready_execution.command.command}` 実行中に失敗した。",
            changes="preflight と実行準備までは完了しており、失敗時点の状態を保存した。",
            decisions="失敗でも session / metrics / report を残す方針を優先した。",
            validation=f"失敗要因: {detail}",
            risks_open_questions=detail,
            next_actions=(
                "失敗要因を解消したうえで再実行し、provider 実行本体へ接続する。"
            ),
            notes="provider session は未保存である。",
        ),
        usage=MetricsUsage(),
        behavior=MetricsBehavior(
            failed_turns=1,
            success_rate=0.0,
            active_time_seconds=execution_duration_seconds,
        ),
        tools={},
        steps=_build_steps(
            preflight_duration_seconds,
            execution_duration_seconds,
        ),
        provider_specific=ProviderSpecificMetrics(),
        state_ref=SessionStateRef(),
        provider_session_path=None,
        allow_edits_notice_posted=False,
        response_text=None,
    )


def _build_steps(
    preflight_duration_seconds: float,
    execution_duration_seconds: float,
) -> dict[str, StepMetric]:
    """最低限の step metrics を返す。"""
    return {
        "preflight": StepMetric(duration_seconds=preflight_duration_seconds),
        "execution": StepMetric(duration_seconds=execution_duration_seconds),
    }


def _format_exception(error: BaseException) -> str:
    """例外を短い文字列に整形する。"""
    message = str(error).strip()
    if message == "":
        return error.__class__.__name__
    return f"{error.__class__.__name__}: {message}"


def _should_rebind_session(ready_execution: ReadyExecution) -> bool:
    """PR 作成後に session を rebind すべきかを返す。"""
    session = ready_execution.resolved_session
    if session is None:
        return False
    return session.requested_mode != "new"


def _fork_session_for_pr(
    repo_root: Path,
    ready_execution: ReadyExecution,
    saved_artifacts: SavedExecutionArtifacts,
    pr_number: int,
) -> None:
    """Issue session artifact を作成された PR の session key に複製する。"""
    session = ready_execution.resolved_session
    target = ready_execution.command.target
    if session is None or target is None or target.repository_full_name is None:
        return

    source_key = session.key
    pr_target_key = f"{target.repository_full_name}#{pr_number}"
    new_session_key = SessionKey(
        backend=source_key.backend,
        target_key=pr_target_key,
        provider=source_key.provider,
        lane=source_key.lane,
        canonical_key=f"{source_key.backend}/{pr_target_key}/{source_key.provider}/{source_key.lane}",
    )

    fork_session_artifact(
        Path(saved_artifacts.session.artifact_path),
        source_key,
        new_session_key,
        repo_root,
        ready_execution.workflow_id,
    )
