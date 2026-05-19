"""sync コマンドの専用 workflow。"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from vv_ai.executions.result import ExecutionResult, ExecutionStatus
from vv_ai.git_ops import (
    GitOpsError,
    checkout_fork_pr,
    commit_all_changes,
    commit_merge_no_edit,
    ensure_merge_base_available,
    ensure_worktree_clean,
    fetch_and_checkout_branch,
    fetch_remote_branch,
    generate_diff_patch,
    get_staged_diff_signature,
    get_head_sha,
    is_ancestor,
    list_changed_files,
    list_conflict_marker_files,
    list_staged_files,
    list_unstaged_files,
    list_unmerged_files,
    merge_no_ff_no_commit,
    push_branch,
    stage_paths,
    try_push_current_branch,
)
from vv_ai.backends.github.client import GitHubClient
from vv_ai.backends.github.comments import (
    post_fork_push_failure_comment,
    post_issue_comment_safely,
)
from vv_ai.backends.github.models import GitHubClientError, GitHubPullRequestSyncState
from vv_ai.artifacts.metrics import (
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
    ToolMetric,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.providers.runner import execute_provider
from vv_ai.artifacts.report import ReportSections
from vv_ai.session import SessionStateRef


class SyncCommandError(RuntimeError):
    """sync コマンドの実行条件違反や同期失敗を表す例外。"""


@dataclass(frozen=True)
class ConflictFileSnapshot:
    """conflict file の AI 実行前状態。"""

    exists: bool
    content_hash: str | None
    had_marker: bool


@dataclass(frozen=True)
class SyncExecutionFacts:
    """sync 実行で確定した事実。"""

    repository_full_name: str
    number: int
    head_ref_name: str
    base_ref_name: str
    is_cross_repository: bool
    conflict_occurred: bool
    conflict_resolved_by_ai: bool
    merge_commit_created: bool
    consistency_commit_created: bool
    push_needed: bool
    push_succeeded: bool


@dataclass
class SyncRuntimeState:
    """sync 実行中に集約する状態。"""

    provider_results: list[ExecutionResult] = field(default_factory=list)
    allow_edits_notice_posted: bool = False
    conflict_occurred: bool = False
    conflict_resolved_by_ai: bool = False
    conflict_result_text: str | None = None
    merge_commit_created: bool = False
    consistency_commit_created: bool = False
    push_needed: bool = False
    push_succeeded: bool = False
    github_state_text: str | None = None


def run_sync_command(
    repo_root: Path,
    ready_execution: ReadyExecution,
    github_client: GitHubClient,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> ExecutionResult:
    """PR ブランチを base branch に同期する。"""
    command = ready_execution.command
    target = command.target
    if target is None or target.kind != "pr" or target.backend != "github":
        raise SyncCommandError("`sync` コマンドは GitHub PR を対象に指定してください")
    if target.repository_full_name is None or target.number is None:
        raise SyncCommandError("`sync` コマンドに必要な PR 情報が不足しています")

    runtime = SyncRuntimeState(
        allow_edits_notice_posted=_get_restored_allow_edits_notice_posted(
            ready_execution
        )
    )

    try:
        ensure_worktree_clean(repo_root)
        pr_info = github_client.get_pull_request(
            target.repository_full_name,
            target.number,
        )
        if pr_info.is_cross_repository:
            checkout_fork_pr(repo_root, target.repository_full_name, target.number)
        else:
            fetch_and_checkout_branch(repo_root, pr_info.head_ref_name)
        fetch_remote_branch(repo_root, "origin", pr_info.base_ref_name)

        base_ref = f"origin/{pr_info.base_ref_name}"
        ensure_merge_base_available(
            repo_root,
            "origin",
            target.number,
            pr_info.base_ref_name,
            base_ref,
        )
        head_sha_before = get_head_sha(repo_root)
        if not is_ancestor(repo_root, base_ref, "HEAD"):
            _merge_base_branch(
                repo_root,
                ready_execution,
                env,
                preflight_duration_seconds,
                base_ref,
                runtime,
            )

        head_sha_before_consistency = get_head_sha(repo_root)
        consistency_result = _run_provider_step(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            _build_consistency_prompt(pr_info.head_ref_name, pr_info.base_ref_name),
            runtime,
        )
        if consistency_result.status != "success":
            return _build_sync_result("failure", runtime, "整合性確認 AI が失敗しました")
        if get_head_sha(repo_root) != head_sha_before_consistency:
            return _build_sync_result("failure", runtime, "整合性確認 AI が commit しました")
        if len(list_staged_files(repo_root)) > 0:
            return _build_sync_result("failure", runtime, "整合性確認 AI が stage しました")
        changed_files_after_consistency = list_changed_files(repo_root)
        marker_files_after_consistency = list_conflict_marker_files(
            repo_root,
            changed_files_after_consistency,
        )
        if len(marker_files_after_consistency) > 0:
            return _build_sync_result(
                "failure",
                runtime,
                "整合性確認 AI が conflict marker を残しました: "
                + ", ".join(marker_files_after_consistency),
            )
        if len(changed_files_after_consistency) > 0:
            runtime.consistency_commit_created = commit_all_changes(
                repo_root,
                "chore: sync 整合性を修正する",
            )
            runtime.push_needed = (
                runtime.push_needed or runtime.consistency_commit_created
            )

        facts = SyncExecutionFacts(
            repository_full_name=target.repository_full_name,
            number=target.number,
            head_ref_name=pr_info.head_ref_name,
            base_ref_name=pr_info.base_ref_name,
            is_cross_repository=pr_info.is_cross_repository,
            conflict_occurred=runtime.conflict_occurred,
            conflict_resolved_by_ai=runtime.conflict_resolved_by_ai,
            merge_commit_created=runtime.merge_commit_created,
            consistency_commit_created=runtime.consistency_commit_created,
            push_needed=runtime.push_needed,
            push_succeeded=False,
        )
        if not command.dry_run and runtime.push_needed:
            if pr_info.is_cross_repository:
                if not try_push_current_branch(repo_root, env.get("GITHUB_TOKEN")):
                    patch = _generate_push_failure_patch(repo_root, head_sha_before)
                    runtime.allow_edits_notice_posted = post_fork_push_failure_comment(
                        github_client,
                        target.repository_full_name,
                        target.number,
                        "",
                        patch,
                        pr_info,
                        runtime.allow_edits_notice_posted,
                    )
                    return _build_sync_result(
                        "failure",
                        runtime,
                        "fork PR ブランチへ push できませんでした",
                    )
            else:
                push_branch(repo_root, pr_info.head_ref_name, env.get("GITHUB_TOKEN"))
            runtime.push_succeeded = True
            facts = _replace_push_succeeded(facts, True)

        if not command.dry_run:
            runtime.github_state_text = _fetch_github_state_text(
                github_client,
                target.repository_full_name,
                target.number,
            )

        head_sha_before_comment = get_head_sha(repo_root)
        comment_result = _run_provider_step(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            _build_final_comment_prompt(
                facts,
                runtime.github_state_text,
                _resolve_explicit_runtime_text(
                    ready_execution,
                    runtime.conflict_result_text,
                ),
                _resolve_explicit_provider_result(
                    ready_execution,
                    consistency_result,
                ),
            ),
            runtime,
        )
        if comment_result.status != "success":
            return _build_sync_result("failure", runtime, "最終コメント生成 AI が失敗しました")
        if get_head_sha(repo_root) != head_sha_before_comment:
            return _build_sync_result("failure", runtime, "最終コメント生成 AI が commit しました")
        if len(list_staged_files(repo_root)) > 0:
            return _build_sync_result("failure", runtime, "最終コメント生成 AI が stage しました")
        if len(list_changed_files(repo_root)) > 0:
            return _build_sync_result("failure", runtime, "最終コメント生成 AI がファイルを変更しました")

        comment_body = _parse_final_comment_body(comment_result.response_text)
        if comment_body == "":
            return _build_sync_result("failure", runtime, "最終コメント本文が空です")
        if not command.dry_run:
            post_issue_comment_safely(
                github_client,
                target.repository_full_name,
                target.number,
                comment_body,
                "sync 最終コメント投稿",
            )

        return _build_sync_result("success", runtime, "sync が完了しました")
    except (GitHubClientError, GitOpsError, SyncCommandError) as exc:
        return _build_sync_result("failure", runtime, str(exc))


def _merge_base_branch(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
    base_ref: str,
    runtime: SyncRuntimeState,
) -> None:
    """base branch を merge し、必要なら conflict 解消 AI を実行する。"""
    attempt = merge_no_ff_no_commit(repo_root, base_ref)
    if attempt.succeeded:
        commit_merge_no_edit(repo_root)
        runtime.merge_commit_created = True
        runtime.push_needed = True
        return

    conflict_files = attempt.unmerged_files
    conflict_file_set = set(conflict_files)
    runtime.conflict_occurred = True
    snapshots = _snapshot_conflict_files(repo_root, conflict_files)
    head_sha_before_ai = get_head_sha(repo_root)
    staged_signature_before_ai = get_staged_diff_signature(repo_root)
    changed_files_before_ai = set(list_changed_files(repo_root))
    conflict_result = _run_provider_step(
        repo_root,
        ready_execution,
        env,
        preflight_duration_seconds,
        _build_conflict_prompt(conflict_files),
        runtime,
    )
    if conflict_result.status != "success":
        raise SyncCommandError("conflict 解消 AI が失敗しました")
    if get_head_sha(repo_root) != head_sha_before_ai:
        raise SyncCommandError("conflict 解消 AI が commit しました")
    if get_staged_diff_signature(repo_root) != staged_signature_before_ai:
        raise SyncCommandError("conflict 解消 AI が stage しました")
    unexpected_changed_files = sorted(
        set(list_changed_files(repo_root)) - changed_files_before_ai - conflict_file_set
    )
    unexpected_unstaged_files = sorted(
        set(list_unstaged_files(repo_root)) - conflict_file_set
    )
    unexpected_files = sorted(
        set(unexpected_changed_files) | set(unexpected_unstaged_files)
    )
    if len(unexpected_files) > 0:
        raise SyncCommandError(
            "conflict file 以外が変更されました: " + ", ".join(unexpected_files)
        )
    marker_files = list_conflict_marker_files(repo_root, conflict_files)
    if len(marker_files) > 0:
        raise SyncCommandError(
            "conflict marker が残っています: " + ", ".join(marker_files)
        )

    stage_targets = _resolve_conflict_stage_targets(
        repo_root,
        conflict_files,
        snapshots,
    )
    if len(stage_targets) == 0:
        raise SyncCommandError("AI による conflict file の変更がありません")
    stage_paths(repo_root, stage_targets)
    remaining_unmerged = list_unmerged_files(repo_root)
    if len(remaining_unmerged) > 0:
        raise SyncCommandError(
            "未解消 conflict が残っています: " + ", ".join(remaining_unmerged)
        )
    commit_merge_no_edit(repo_root)
    runtime.conflict_resolved_by_ai = True
    runtime.conflict_result_text = _get_provider_response_text(conflict_result)
    runtime.merge_commit_created = True
    runtime.push_needed = True


def _run_provider_step(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
    prompt: str,
    runtime: SyncRuntimeState,
) -> ExecutionResult:
    """provider を実行し、次回用 state と集約状態へ反映する。"""
    result = execute_provider(
        repo_root,
        ready_execution,
        env,
        preflight_duration_seconds,
        prompt,
    )
    runtime.provider_results.append(result)
    runtime.allow_edits_notice_posted = (
        runtime.allow_edits_notice_posted or result.allow_edits_notice_posted
    )
    if ready_execution.resolved_session is not None:
        ready_execution.resolved_session.state_ref = result.state_ref
    return result


def _snapshot_conflict_files(
    repo_root: Path,
    conflict_files: list[str],
) -> dict[str, ConflictFileSnapshot]:
    """conflict file の存在状態と内容 hash を取得する。"""
    marker_files = set(list_conflict_marker_files(repo_root, conflict_files))
    snapshots: dict[str, ConflictFileSnapshot] = {}
    for path in conflict_files:
        file_path = repo_root / path
        exists = file_path.exists()
        snapshots[path] = ConflictFileSnapshot(
            exists=exists,
            content_hash=(
                _hash_file(file_path) if exists and file_path.is_file() else None
            ),
            had_marker=path in marker_files,
        )
    return snapshots


def _resolve_conflict_stage_targets(
    repo_root: Path,
    conflict_files: list[str],
    snapshots: dict[str, ConflictFileSnapshot],
) -> list[str]:
    """AI 後の conflict file から wrapper が stage する path を返す。"""
    stage_targets: list[str] = []
    marker_files = set(list_conflict_marker_files(repo_root, conflict_files))
    for path in conflict_files:
        snapshot = snapshots[path]
        if snapshot.had_marker:
            if path not in marker_files:
                stage_targets.append(path)
            continue
        file_path = repo_root / path
        exists = file_path.exists()
        content_hash = _hash_file(file_path) if exists and file_path.is_file() else None
        if snapshot.exists != exists or snapshot.content_hash != content_hash:
            stage_targets.append(path)
    return stage_targets


def _hash_file(path: Path) -> str:
    """ファイル内容の SHA-256 を返す。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_conflict_prompt(conflict_files: list[str]) -> str:
    """conflict 解消専用 prompt を返す。"""
    files = "\n".join(f"- {path}" for path in conflict_files)
    return (
        "sync コマンドの conflict 解消だけを行ってください。\n"
        "対象ファイル以外は変更しないでください。\n"
        "commit と stage は行わないでください。\n"
        "conflict marker を解消し、内容の整合性を保ってください。\n\n"
        f"対象ファイル:\n{files}"
    )


def _build_consistency_prompt(head_ref_name: str, base_ref_name: str) -> str:
    """merge 後の整合性確認 prompt を返す。"""
    return (
        "sync コマンドの整合性確認を行ってください。\n"
        "必要最小限の修正だけをファイルへ反映してください。\n"
        "commit と stage は行わないでください。\n\n"
        f"head branch: {head_ref_name}\n"
        f"base branch: {base_ref_name}"
    )


def _build_final_comment_prompt(
    facts: SyncExecutionFacts,
    github_state_text: str | None,
    conflict_result_text: str | None,
    consistency_result_text: str | None,
) -> str:
    """最終コメント生成 prompt を返す。"""
    state_text = (
        github_state_text if github_state_text is not None else "取得できませんでした"
    )
    conflict_block = (
        ""
        if conflict_result_text is None
        else f"\nconflict 解消 AI の出力:\n{conflict_result_text}"
    )
    consistency_block = (
        ""
        if consistency_result_text is None
        else f"\n整合性確認 AI の出力:\n{consistency_result_text}"
    )
    return (
        "sync コマンドの最終コメント本文を作成してください。\n"
        "1行目は BODY: とし、2行目以降に GitHub PR へ投稿する本文だけを書いてください。\n"
        "ファイル変更、commit、stage は行わないでください。\n\n"
        f"repository: {facts.repository_full_name}\n"
        f"pull request: #{facts.number}\n"
        f"head branch: {facts.head_ref_name}\n"
        f"base branch: {facts.base_ref_name}\n"
        f"fork PR: {facts.is_cross_repository}\n"
        f"conflict occurred: {facts.conflict_occurred}\n"
        f"conflict resolved by AI: {facts.conflict_resolved_by_ai}\n"
        f"merge commit created: {facts.merge_commit_created}\n"
        f"consistency commit created: {facts.consistency_commit_created}\n"
        f"push needed: {facts.push_needed}\n"
        f"push succeeded: {facts.push_succeeded}\n"
        f"GitHub state: {state_text}"
        f"{conflict_block}"
        f"{consistency_block}"
    )


def _resolve_explicit_provider_result(
    ready_execution: ReadyExecution,
    provider_result: ExecutionResult,
) -> str | None:
    """provider session が継続されない場合だけ provider 結果を返す。"""
    if _continues_provider_session(ready_execution):
        return None
    return _get_provider_response_text(provider_result)


def _resolve_explicit_runtime_text(
    ready_execution: ReadyExecution,
    text: str | None,
) -> str | None:
    """provider session が継続されない場合だけ runtime text を返す。"""
    if _continues_provider_session(ready_execution):
        return None
    return text


def _get_provider_response_text(provider_result: ExecutionResult) -> str:
    """provider result の応答本文を prompt 用に返す。"""
    if provider_result.response_text is None:
        return "応答本文なし"
    return provider_result.response_text


def _continues_provider_session(ready_execution: ReadyExecution) -> bool:
    """次の provider 実行が直前の provider session を継続するか返す。"""
    session = ready_execution.resolved_session
    if session is None:
        return False
    if session.restore_strategy == "new":
        return False
    state_ref = session.state_ref
    if state_ref is None:
        return False
    return state_ref.provider_session_id is not None


def _parse_final_comment_body(response_text: str | None) -> str:
    """最終コメント AI の出力から本文を返す。"""
    if response_text is None:
        return ""
    lines = response_text.split("\n")
    if len(lines) > 0 and lines[0].strip() == "BODY:":
        return "\n".join(lines[1:]).strip()
    return response_text.strip()


def _fetch_github_state_text(
    github_client: GitHubClient,
    repository_full_name: str,
    number: int,
) -> str | None:
    """GitHub PR 状態を取得して prompt 用文字列へ整形する。"""
    try:
        state = github_client.get_pull_request_sync_state(repository_full_name, number)
    except GitHubClientError as exc:
        print(f"sync 状態取得に失敗しました: {exc}", file=sys.stderr)
        return None
    return _format_github_sync_state(state)


def _format_github_sync_state(state: GitHubPullRequestSyncState) -> str:
    """GitHub PR 状態を短い文字列へ整形する。"""
    checks = state.status_check_summary
    return (
        f"mergeable={state.mergeable}, "
        f"merge_state_status={state.merge_state_status}, "
        f"checks success={checks.success_count}, "
        f"failure={checks.failure_count}, "
        f"pending={checks.pending_count}, "
        f"unknown={checks.unknown_count}"
    )


def _generate_push_failure_patch(repo_root: Path, base_sha: str) -> str:
    """fork PR push 失敗時の patch を生成する。"""
    try:
        return generate_diff_patch(repo_root, base_sha)
    except GitOpsError as exc:
        print(f"patch 生成に失敗しました: {exc}", file=sys.stderr)
        return ""


def _replace_push_succeeded(
    facts: SyncExecutionFacts,
    push_succeeded: bool,
) -> SyncExecutionFacts:
    """push 結果だけを差し替えた SyncExecutionFacts を返す。"""
    return SyncExecutionFacts(
        repository_full_name=facts.repository_full_name,
        number=facts.number,
        head_ref_name=facts.head_ref_name,
        base_ref_name=facts.base_ref_name,
        is_cross_repository=facts.is_cross_repository,
        conflict_occurred=facts.conflict_occurred,
        conflict_resolved_by_ai=facts.conflict_resolved_by_ai,
        merge_commit_created=facts.merge_commit_created,
        consistency_commit_created=facts.consistency_commit_created,
        push_needed=facts.push_needed,
        push_succeeded=push_succeeded,
    )


def _build_sync_result(
    status: ExecutionStatus,
    runtime: SyncRuntimeState,
    summary: str,
) -> ExecutionResult:
    """複数 provider 結果から sync 全体の ExecutionResult を返す。"""
    results = runtime.provider_results
    return ExecutionResult(
        status=status,
        report_sections=_merge_report_sections(results, summary),
        usage=_merge_usage(results),
        behavior=_merge_behavior(results),
        tools=_merge_tools(results),
        steps=_merge_steps(results),
        provider_specific=(
            results[-1].provider_specific
            if len(results) > 0
            else ProviderSpecificMetrics()
        ),
        state_ref=results[-1].state_ref if len(results) > 0 else SessionStateRef(),
        provider_session_path=(
            results[-1].provider_session_path if len(results) > 0 else None
        ),
        allow_edits_notice_posted=runtime.allow_edits_notice_posted,
        response_text=summary,
    )


def _merge_report_sections(
    results: list[ExecutionResult],
    summary: str,
) -> ReportSections:
    """複数 provider 結果の report を sync 用にまとめる。"""
    if len(results) == 0:
        return ReportSections(
            summary=summary,
            changes="provider 実行前に終了しました",
            decisions="sync 専用 workflow が結果を判定しました",
            validation="追加検証はありません",
            risks_open_questions="未完了の同期処理がある可能性があります",
            next_actions="実行ログを確認してください",
            notes="sync コマンドの wrapper が生成しました",
        )
    return ReportSections(
        summary=summary,
        changes=_join_report_field(results, "changes"),
        decisions=_join_report_field(results, "decisions"),
        validation=_join_report_field(results, "validation"),
        risks_open_questions=_join_report_field(results, "risks_open_questions"),
        next_actions=_join_report_field(results, "next_actions"),
        notes=_join_report_field(results, "notes"),
    )


def _join_report_field(results: list[ExecutionResult], field_name: str) -> str:
    """report の同名 field を連結する。"""
    values = [
        str(getattr(result.report_sections, field_name)).strip()
        for result in results
        if str(getattr(result.report_sections, field_name)).strip() != ""
    ]
    return "\n\n".join(values) if len(values) > 0 else "該当なし"


def _merge_usage(results: list[ExecutionResult]) -> MetricsUsage:
    """usage metrics を合算する。"""
    return MetricsUsage(
        input_tokens=_sum_optional_int(
            [result.usage.input_tokens for result in results]
        ),
        cached_input_tokens=_sum_optional_int(
            [result.usage.cached_input_tokens for result in results]
        ),
        output_tokens=_sum_optional_int(
            [result.usage.output_tokens for result in results]
        ),
        cost_usd=_sum_optional_float([result.usage.cost_usd for result in results]),
    )


def _merge_behavior(results: list[ExecutionResult]) -> MetricsBehavior:
    """behavior metrics を合算する。"""
    return MetricsBehavior(
        total_turns=_sum_optional_int(
            [result.behavior.total_turns for result in results]
        ),
        failed_turns=_sum_optional_int(
            [result.behavior.failed_turns for result in results]
        ),
        command_execution_count=_sum_optional_int(
            [result.behavior.command_execution_count for result in results]
        ),
        file_change_count=_sum_optional_int(
            [result.behavior.file_change_count for result in results]
        ),
        mcp_tool_call_count=_sum_optional_int(
            [result.behavior.mcp_tool_call_count for result in results]
        ),
        web_search_count=_sum_optional_int(
            [result.behavior.web_search_count for result in results]
        ),
        plan_update_count=_sum_optional_int(
            [result.behavior.plan_update_count for result in results]
        ),
        session_count=_sum_optional_int(
            [result.behavior.session_count for result in results]
        ),
        lines_added=_sum_optional_int(
            [result.behavior.lines_added for result in results]
        ),
        lines_removed=_sum_optional_int(
            [result.behavior.lines_removed for result in results]
        ),
        pr_count=_sum_optional_int([result.behavior.pr_count for result in results]),
        commit_count=_sum_optional_int([result.behavior.commit_count for result in results]),
        active_time_seconds=_sum_optional_float(
            [result.behavior.active_time_seconds for result in results]
        ),
    )


def _merge_tools(results: list[ExecutionResult]) -> dict[str, ToolMetric]:
    """tool metrics を key ごとに合算する。"""
    merged: dict[str, list[ToolMetric]] = {}
    for result in results:
        for key, metric in result.tools.items():
            merged.setdefault(key, []).append(metric)
    return {
        key: ToolMetric(
            success_count=_sum_optional_int(
                [metric.success_count for metric in metrics]
            ),
            failure_count=_sum_optional_int(
                [metric.failure_count for metric in metrics]
            ),
            duration_seconds=_sum_optional_float(
                [metric.duration_seconds for metric in metrics]
            ),
        )
        for key, metrics in merged.items()
    }


def _merge_steps(results: list[ExecutionResult]) -> dict[str, StepMetric]:
    """step metrics を key ごとに合算する。"""
    merged: dict[str, list[StepMetric]] = {}
    for result in results:
        for key, metric in result.steps.items():
            merged.setdefault(key, []).append(metric)
    return {
        key: StepMetric(
            duration_seconds=_sum_optional_float(
                [metric.duration_seconds for metric in metrics]
            )
        )
        for key, metrics in merged.items()
    }


def _sum_optional_int(values: list[int | None]) -> int | None:
    """None を除いて int を合算する。"""
    present = [value for value in values if value is not None]
    return sum(present) if len(present) > 0 else None


def _sum_optional_float(values: list[float | None]) -> float | None:
    """None を除いて float を合算する。"""
    present = [value for value in values if value is not None]
    return sum(present) if len(present) > 0 else None


def _get_restored_allow_edits_notice_posted(
    ready_execution: ReadyExecution,
) -> bool:
    """復元済み session の maintainer edits 案内済み状態を返す。"""
    session = ready_execution.resolved_session
    if session is None:
        return False
    return session.allow_edits_notice_posted
