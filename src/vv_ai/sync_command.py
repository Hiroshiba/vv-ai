"""sync コマンド専用 workflow。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vv_ai.execution import ExecutionResult, ExecutionStatus
from vv_ai.git_ops import (
    GitOpsError,
    checkout_fork_pr,
    commit_all_changes,
    commit_merge_no_edit,
    ensure_worktree_clean,
    fetch_and_checkout_branch,
    fetch_remote,
    generate_diff_patch,
    get_head_sha,
    is_ancestor,
    list_changed_files,
    list_conflict_marker_files,
    list_staged_files,
    list_unmerged_files,
    merge_no_ff_no_commit,
    push_branch,
    run_git_command,
    try_push_current_branch,
)
from vv_ai.github import GitHubClient, GitHubPullRequest, GitHubPullRequestSyncState
from vv_ai.github_comment import (
    build_allow_edits_notice,
    mark_allow_edits_notice_posted,
    post_issue_comment_safely,
)
from vv_ai.metrics_artifact import (
    ClaudeProviderMetrics,
    CodexProviderMetrics,
    MetricsBehavior,
    MetricsUsage,
    ProviderSpecificMetrics,
    StepMetric,
    ToolMetric,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.provider_execution import execute_provider
from vv_ai.report_artifact import ReportSections
from vv_ai.session import SessionStateRef

_PATCH_COMMENT_LIMIT = 60000


class SyncCommandError(RuntimeError):
    """sync コマンドの実行に失敗したことを表す例外。"""


class SyncExecutionFacts(BaseModel):
    """sync 実行で発生した事実を表す。"""

    model_config = ConfigDict(extra="forbid")

    repository_full_name: str
    number: int
    head_ref_name: str
    base_ref_name: str
    is_cross_repository: bool
    initial_head_sha: str
    merge_needed: bool
    conflict_detected: bool
    merge_commit_sha: str | None
    consistency_commit_created: bool
    consistency_commit_sha: str | None
    pushed: bool
    push_failed: bool
    patch_comment_posted: bool


def run_sync_command(
    repo_root: Path,
    ready_execution: ReadyExecution,
    github_client: GitHubClient,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> ExecutionResult:
    """sync 専用 workflow を実行して結果を返す。"""
    target = ready_execution.command.target
    if target is None or target.backend != "github" or target.kind != "pr":
        raise SyncCommandError("`sync` コマンドは GitHub PR を対象に指定してください")
    if target.repository_full_name is None or target.number is None:
        raise SyncCommandError("GitHub PR の識別情報が不足しています")

    pr = github_client.get_pull_request(target.repository_full_name, target.number)
    _checkout_pull_request(repo_root, pr)
    ensure_worktree_clean(repo_root)
    initial_head_sha = get_head_sha(repo_root)
    fetch_remote(repo_root, "origin")

    base_ref = f"origin/{pr.base_ref_name}"
    merge_needed = not is_ancestor(repo_root, base_ref, "HEAD")
    provider_results: list[ExecutionResult] = []
    conflict_detected = False
    merge_commit_sha: str | None = None
    consistency_commit_sha: str | None = None

    if merge_needed:
        merge_attempt = merge_no_ff_no_commit(repo_root, base_ref)
        conflict_detected = len(merge_attempt.unmerged_files) > 0
        marker_files_before_conflict_ai: list[str] = []
        if conflict_detected:
            staged_files_before_conflict_ai = list_staged_files(repo_root)
            marker_files_before_conflict_ai = list_conflict_marker_files(
                repo_root,
                merge_attempt.unmerged_files,
            )
            result = _execute_provider_step(
                repo_root,
                ready_execution,
                env,
                preflight_duration_seconds,
                _build_conflict_prompt(pr, merge_attempt.unmerged_files),
                provider_results,
            )
            if result.status != "success":
                return _build_sync_result(
                    "failure",
                    ready_execution,
                    preflight_duration_seconds,
                    provider_results,
                    "conflict 解消 AI が失敗しました。",
                    result.response_text,
                )
            failure_message = _validate_provider_did_not_take_over_git(
                repo_root,
                initial_head_sha,
                staged_files_before_conflict_ai,
                list_changed_files(repo_root),
            )
            if failure_message is not None:
                return _build_sync_result(
                    "failure",
                    ready_execution,
                    preflight_duration_seconds,
                    provider_results,
                    failure_message,
                    result.response_text,
                )
            marker_files = list_conflict_marker_files(
                repo_root,
                list_changed_files(repo_root),
            )
            if len(marker_files) > 0:
                return _build_sync_result(
                    "failure",
                    ready_execution,
                    preflight_duration_seconds,
                    provider_results,
                    _format_unresolved_conflict_message([], marker_files),
                    result.response_text,
                )
        if conflict_detected:
            resolved_marker_files = sorted(
                set(marker_files_before_conflict_ai) - set(marker_files)
            )
            if len(resolved_marker_files) > 0:
                run_git_command(repo_root, "add", "--", *resolved_marker_files)
            unresolved = list_unmerged_files(repo_root)
            if len(unresolved) > 0:
                return _build_sync_result(
                    "failure",
                    ready_execution,
                    preflight_duration_seconds,
                    provider_results,
                    _format_unresolved_conflict_message(unresolved, []),
                    result.response_text,
                )
        else:
            run_git_command(repo_root, "add", "-A")
        merge_commit_sha = commit_merge_no_edit(repo_root)

    before_consistency_sha = get_head_sha(repo_root)
    result = _execute_provider_step(
        repo_root,
        ready_execution,
        env,
        preflight_duration_seconds,
        _build_consistency_prompt(pr, merge_needed, conflict_detected),
        provider_results,
    )
    if result.status != "success":
        return _build_sync_result(
            "failure",
            ready_execution,
            preflight_duration_seconds,
            provider_results,
            "整合性確認 AI が失敗しました。",
            result.response_text,
        )
    failure_message = _validate_provider_did_not_take_over_git(
        repo_root,
        before_consistency_sha,
        [],
        list_changed_files(repo_root),
    )
    if failure_message is not None:
        return _build_sync_result(
            "failure",
            ready_execution,
            preflight_duration_seconds,
            provider_results,
            failure_message,
            result.response_text,
        )

    consistency_commit_created = commit_all_changes(repo_root, "chore: sync 整合性を修正する")
    if consistency_commit_created:
        consistency_commit_sha = get_head_sha(repo_root)

    pushed, patch_comment_posted = _push_or_comment_patch(
        repo_root,
        ready_execution,
        result,
        github_client,
        env,
        pr,
        initial_head_sha,
    )
    sync_state = _get_sync_state_if_pushed(github_client, pr, pushed)
    facts = SyncExecutionFacts(
        repository_full_name=pr.repository_full_name,
        number=pr.number,
        head_ref_name=pr.head_ref_name,
        base_ref_name=pr.base_ref_name,
        is_cross_repository=pr.is_cross_repository,
        initial_head_sha=initial_head_sha,
        merge_needed=merge_needed,
        conflict_detected=conflict_detected,
        merge_commit_sha=merge_commit_sha,
        consistency_commit_created=consistency_commit_created,
        consistency_commit_sha=consistency_commit_sha,
        pushed=pushed,
        push_failed=not pushed,
        patch_comment_posted=patch_comment_posted,
    )
    if not pushed:
        return _build_sync_result(
            "failure",
            ready_execution,
            preflight_duration_seconds,
            provider_results,
            "PR ブランチへ push できなかったため、sync は未完了です。",
            None,
        )

    before_comment_sha = get_head_sha(repo_root)
    comment_result = _execute_provider_step(
        repo_root,
        ready_execution,
        env,
        preflight_duration_seconds,
        _build_final_comment_prompt(facts, sync_state),
        provider_results,
    )
    failure_message = _validate_provider_did_not_take_over_git(
        repo_root,
        before_comment_sha,
        [],
        list_changed_files(repo_root),
    )
    if failure_message is not None:
        return _build_sync_result(
            "failure",
            ready_execution,
            preflight_duration_seconds,
            provider_results,
            failure_message,
            comment_result.response_text,
        )
    if comment_result.status != "success":
        return _build_sync_result(
            "failure",
            ready_execution,
            preflight_duration_seconds,
            provider_results,
            "最終コメント生成 AI が失敗しました。",
            comment_result.response_text,
        )
    comment_body = _require_response_text(comment_result)
    if ready_execution.command.dry_run:
        print(comment_body)
    else:
        post_issue_comment_safely(
            github_client,
            pr.repository_full_name,
            pr.number,
            comment_body,
            "sync コメント投稿",
        )

    return _build_sync_result(
        comment_result.status,
        ready_execution,
        preflight_duration_seconds,
        provider_results,
        "sync workflow を完了しました。",
        comment_body,
    )


def _checkout_pull_request(repo_root: Path, pr: GitHubPullRequest) -> None:
    """PR の head branch を checkout する。"""
    if pr.is_cross_repository:
        checkout_fork_pr(repo_root, pr.repository_full_name, pr.number)
        return
    fetch_and_checkout_branch(repo_root, pr.head_ref_name)


def _execute_provider_step(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
    prompt: str,
    provider_results: list[ExecutionResult],
) -> ExecutionResult:
    """provider を実行し、次回実行用の state_ref を更新する。"""
    result = execute_provider(
        repo_root,
        ready_execution,
        env,
        preflight_duration_seconds,
        prompt,
    )
    provider_results.append(result)
    session = ready_execution.resolved_session
    if session is not None:
        session.state_ref = result.state_ref
    return result


def _validate_provider_did_not_take_over_git(
    repo_root: Path,
    expected_head_sha: str,
    allowed_staged_files: list[str],
    changed_files: list[str],
) -> str | None:
    """provider が commit や staging を行っていないことを検証する。"""
    if get_head_sha(repo_root) != expected_head_sha:
        return "AI が commit を作成したため sync を停止しました。"
    staged_files = list_staged_files(repo_root)
    unexpected_staged_files = sorted(set(staged_files) - set(allowed_staged_files))
    if len(unexpected_staged_files) > 0:
        return (
            "AI が staged diff を残したため sync を停止しました: "
            + ", ".join(unexpected_staged_files)
        )
    marker_files = list_conflict_marker_files(repo_root, changed_files)
    if len(marker_files) > 0:
        return (
            "conflict marker が残っているため sync を停止しました: "
            + ", ".join(marker_files)
        )
    return None


def _push_or_comment_patch(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient,
    env: Mapping[str, str],
    pr: GitHubPullRequest,
    initial_head_sha: str,
) -> tuple[bool, bool]:
    """PR branch へ push し、失敗時は fallback コメントを投稿する。"""
    if ready_execution.command.dry_run:
        return True, False
    if not pr.is_cross_repository:
        push_branch(repo_root, pr.head_ref_name, env.get("GITHUB_TOKEN"))
        return True, False
    if try_push_current_branch(repo_root, env.get("GITHUB_TOKEN")):
        return True, False
    body, notice = _build_fork_push_failure_body(
        repo_root,
        ready_execution,
        execution_result,
        pr,
        initial_head_sha,
    )
    posted = post_issue_comment_safely(
        github_client,
        pr.repository_full_name,
        pr.number,
        body,
        "fork push 失敗コメント投稿",
    )
    if posted:
        mark_allow_edits_notice_posted(execution_result, notice)
    return False, posted


def _build_fork_push_failure_body(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    pr: GitHubPullRequest,
    initial_head_sha: str,
) -> tuple[str, str]:
    """fork PR へ push できなかった場合のコメント本文を返す。"""
    notice = build_allow_edits_notice(ready_execution, execution_result, pr)
    try:
        patch = generate_diff_patch(repo_root, initial_head_sha)
    except GitOpsError as exc:
        return (
            (
                "fork リポジトリへの push に失敗し、patch 生成にも失敗しました。"
                f"\n\n{exc}"
                f"{notice}"
            ),
            notice,
        )
    if patch.strip() == "":
        return (
            (
                "fork リポジトリへの push に失敗しました。ローカル差分は空です。"
                f"{notice}"
            ),
            notice,
        )
    if len(patch) > _PATCH_COMMENT_LIMIT:
        return (
            (
                "fork リポジトリへの push に失敗しました。\n\n"
                "patch が大きいためコメントには含めません。"
                "メンテナーが push できる環境で sync を再実行してください。"
                f"{notice}"
            ),
            notice,
        )
    return (
        (
            "fork リポジトリへの push に失敗したため、変更内容を patch として提示します。\n\n"
            f"```diff\n{patch}\n```"
            f"{notice}"
        ),
        notice,
    )


def _get_sync_state_if_pushed(
    github_client: GitHubClient,
    pr: GitHubPullRequest,
    pushed: bool,
) -> GitHubPullRequestSyncState | None:
    """push 後に取得できる GitHub 状態を返す。"""
    if not pushed:
        return None
    return github_client.get_pull_request_sync_state(pr.repository_full_name, pr.number)


def _build_conflict_prompt(pr: GitHubPullRequest, unmerged_files: list[str]) -> str:
    """conflict 解消 AI 用 prompt を返す。"""
    return "\n".join(
        [
            "sync workflow の conflict 解消だけを行ってください。",
            "git add、git commit、git push は実行しないでください。",
            "未解消 conflict と conflict marker を残さないでください。",
            f"対象 PR: {pr.url}",
            f"base branch: {pr.base_ref_name}",
            f"head branch: {pr.head_ref_name}",
            "conflict files:",
            *[f"- {path}" for path in unmerged_files],
        ]
    )


def _build_consistency_prompt(
    pr: GitHubPullRequest,
    merge_needed: bool,
    conflict_detected: bool,
) -> str:
    """整合性確認 AI 用 prompt を返す。"""
    merge_status = "merge commit 作成済み" if merge_needed else "base branch 取り込み済み"
    conflict_status = "conflict 解消済み" if conflict_detected else "conflict なし"
    return "\n".join(
        [
            "sync workflow の整合性確認を行ってください。",
            "必要最小限の修正だけをファイル変更として残してください。",
            "不要なら何も変更しないでください。",
            "git add、git commit、git push は実行しないでください。",
            f"対象 PR: {pr.url}",
            f"状態: {merge_status}",
            f"conflict: {conflict_status}",
        ]
    )


def _build_final_comment_prompt(
    facts: SyncExecutionFacts,
    sync_state: GitHubPullRequestSyncState | None,
) -> str:
    """PR コメント本文生成 AI 用 prompt を返す。"""
    state_text = "取得なし" if sync_state is None else sync_state.model_dump_json()
    return "\n".join(
        [
            "sync workflow の完了コメント本文だけを Markdown で作成してください。",
            "git add、git commit、git push は実行しないでください。",
            "事実と GitHub 状態に基づいて簡潔に書いてください。",
            "タイトル行や前置きは不要です。",
            f"sync facts: {facts.model_dump_json()}",
            f"GitHub 状態: {state_text}",
        ]
    )


def _require_response_text(result: ExecutionResult) -> str:
    """provider の応答本文を返す。"""
    if result.response_text is None or result.response_text.strip() == "":
        raise SyncCommandError("AI からのコメント本文がありません")
    return result.response_text


def _format_unresolved_conflict_message(
    unresolved: list[str],
    marker_files: list[str],
) -> str:
    """未解消 conflict の失敗メッセージを返す。"""
    blocks: list[str] = []
    if len(unresolved) > 0:
        blocks.append("未解消 conflict: " + ", ".join(unresolved))
    if len(marker_files) > 0:
        blocks.append("conflict marker 残存: " + ", ".join(marker_files))
    return " / ".join(blocks)


def _build_sync_result(
    status: ExecutionStatus,
    ready_execution: ReadyExecution,
    preflight_duration_seconds: float,
    provider_results: list[ExecutionResult],
    summary: str,
    response_text: str | None,
) -> ExecutionResult:
    """sync の ExecutionResult を集約して返す。"""
    return ExecutionResult(
        status=status,
        report_sections=_build_report_sections(summary, provider_results),
        usage=_merge_usage([result.usage for result in provider_results]),
        behavior=_merge_behavior([result.behavior for result in provider_results]),
        tools=_merge_metric_dicts([result.tools for result in provider_results]),
        steps=_merge_steps(preflight_duration_seconds, provider_results),
        provider_specific=_merge_provider_specific(
            [result.provider_specific for result in provider_results]
        ),
        state_ref=_resolve_final_state_ref(ready_execution, provider_results),
        provider_session_path=_resolve_final_provider_session_path(provider_results),
        allow_edits_notice_posted=any(
            result.allow_edits_notice_posted for result in provider_results
        ),
        response_text=response_text,
    )


def _build_report_sections(
    summary: str,
    provider_results: list[ExecutionResult],
) -> ReportSections:
    """sync 用 report sections を返す。"""
    changes = "provider 実行なし。"
    if len(provider_results) > 0:
        changes = "\n\n".join(result.report_sections.changes for result in provider_results)
    return ReportSections(
        summary=summary,
        changes=changes,
        decisions="sync 専用 workflow が merge、commit、push、コメント投稿を制御した。",
        validation="commit と staged diff を wrapper 側で検査した。",
        risks_open_questions="GitHub の非同期 status check はコメント投稿時点の状態のみ反映した。",
        next_actions="PR の状態と CI 結果を確認する。",
        notes="sync workflow の実行結果を集約した。",
    )


def _merge_usage(usages: list[MetricsUsage]) -> MetricsUsage:
    """usage metrics を合算する。"""
    return MetricsUsage(
        input_tokens=_sum_optional_int([usage.input_tokens for usage in usages]),
        cached_input_tokens=_sum_optional_int(
            [usage.cached_input_tokens for usage in usages]
        ),
        output_tokens=_sum_optional_int([usage.output_tokens for usage in usages]),
        cost_usd=_sum_optional_float([usage.cost_usd for usage in usages]),
    )


def _merge_behavior(behaviors: list[MetricsBehavior]) -> MetricsBehavior:
    """behavior metrics を合算する。"""
    return MetricsBehavior(
        total_turns=_sum_optional_int([behavior.total_turns for behavior in behaviors]),
        failed_turns=_sum_optional_int([behavior.failed_turns for behavior in behaviors]),
        success_rate=_last_optional_float([behavior.success_rate for behavior in behaviors]),
        command_execution_count=_sum_optional_int(
            [behavior.command_execution_count for behavior in behaviors]
        ),
        file_change_count=_sum_optional_int(
            [behavior.file_change_count for behavior in behaviors]
        ),
        mcp_tool_call_count=_sum_optional_int(
            [behavior.mcp_tool_call_count for behavior in behaviors]
        ),
        web_search_count=_sum_optional_int(
            [behavior.web_search_count for behavior in behaviors]
        ),
        plan_update_count=_sum_optional_int(
            [behavior.plan_update_count for behavior in behaviors]
        ),
        session_count=_sum_optional_int([behavior.session_count for behavior in behaviors]),
        lines_added=_sum_optional_int([behavior.lines_added for behavior in behaviors]),
        lines_removed=_sum_optional_int([behavior.lines_removed for behavior in behaviors]),
        pr_count=_sum_optional_int([behavior.pr_count for behavior in behaviors]),
        commit_count=_sum_optional_int([behavior.commit_count for behavior in behaviors]),
        active_time_seconds=_sum_optional_float(
            [behavior.active_time_seconds for behavior in behaviors]
        ),
        code_edit_decisions=_merge_code_edit_decisions(
            [behavior.code_edit_decisions for behavior in behaviors]
        ),
    )


def _merge_metric_dicts(metric_dicts: list[dict[str, ToolMetric]]) -> dict[str, ToolMetric]:
    """tool metrics を step 名付きで集約する。"""
    merged: dict[str, ToolMetric] = {}
    for index, metric_dict in enumerate(metric_dicts, start=1):
        for name, metric in metric_dict.items():
            merged[f"sync-{index}:{name}"] = metric
    return merged


def _merge_steps(
    preflight_duration_seconds: float,
    provider_results: list[ExecutionResult],
) -> dict[str, StepMetric]:
    """step metrics を集約する。"""
    steps = {"preflight": StepMetric(duration_seconds=preflight_duration_seconds)}
    for index, result in enumerate(provider_results, start=1):
        for name, metric in result.steps.items():
            steps[f"sync-{index}:{name}"] = metric
    return steps


def _merge_provider_specific(
    values: list[ProviderSpecificMetrics],
) -> ProviderSpecificMetrics:
    """provider 固有 metrics を合算する。"""
    codex_values = [value.codex for value in values if value.codex is not None]
    claude_values = [value.claude for value in values if value.claude is not None]
    return ProviderSpecificMetrics(
        codex=_merge_codex_metrics(codex_values),
        claude=_merge_claude_metrics(claude_values),
    )


def _merge_codex_metrics(values: list[CodexProviderMetrics]) -> CodexProviderMetrics | None:
    """Codex metrics を合算する。"""
    if len(values) == 0:
        return None
    return CodexProviderMetrics(
        thread_id=_last_optional_text([value.thread_id for value in values]),
        input_tokens=_sum_optional_int([value.input_tokens for value in values]),
        cached_input_tokens=_sum_optional_int(
            [value.cached_input_tokens for value in values]
        ),
        output_tokens=_sum_optional_int([value.output_tokens for value in values]),
        total_turns=_sum_optional_int([value.total_turns for value in values]),
        failed_turns=_sum_optional_int([value.failed_turns for value in values]),
        success_rate=_last_optional_float([value.success_rate for value in values]),
        command_execution_count=_sum_optional_int(
            [value.command_execution_count for value in values]
        ),
        file_change_count=_sum_optional_int([value.file_change_count for value in values]),
        mcp_tool_call_count=_sum_optional_int(
            [value.mcp_tool_call_count for value in values]
        ),
        web_search_count=_sum_optional_int([value.web_search_count for value in values]),
        plan_update_count=_sum_optional_int([value.plan_update_count for value in values]),
    )


def _merge_claude_metrics(
    values: list[ClaudeProviderMetrics],
) -> ClaudeProviderMetrics | None:
    """Claude metrics を合算する。"""
    if len(values) == 0:
        return None
    return ClaudeProviderMetrics(
        session_count=_sum_optional_int([value.session_count for value in values]),
        input_tokens=_sum_optional_int([value.input_tokens for value in values]),
        cached_input_tokens=_sum_optional_int(
            [value.cached_input_tokens for value in values]
        ),
        output_tokens=_sum_optional_int([value.output_tokens for value in values]),
        cost_usd=_sum_optional_float([value.cost_usd for value in values]),
        lines_added=_sum_optional_int([value.lines_added for value in values]),
        lines_removed=_sum_optional_int([value.lines_removed for value in values]),
        pr_count=_sum_optional_int([value.pr_count for value in values]),
        commit_count=_sum_optional_int([value.commit_count for value in values]),
        active_time_seconds=_sum_optional_float(
            [value.active_time_seconds for value in values]
        ),
        code_edit_decisions=_merge_code_edit_decisions(
            [value.code_edit_decisions for value in values]
        ),
    )


def _resolve_final_state_ref(
    ready_execution: ReadyExecution,
    provider_results: list[ExecutionResult],
) -> SessionStateRef:
    """最終的な state_ref を返す。"""
    if len(provider_results) > 0:
        return provider_results[-1].state_ref
    session = ready_execution.resolved_session
    if session is not None and session.state_ref is not None:
        return session.state_ref
    return SessionStateRef()


def _resolve_final_provider_session_path(
    provider_results: list[ExecutionResult],
) -> Path | None:
    """最後に取得した provider session path を返す。"""
    for result in reversed(provider_results):
        if result.provider_session_path is not None:
            return result.provider_session_path
    return None


def _sum_optional_int(values: list[int | None]) -> int | None:
    """None を除外して int を合算する。"""
    present = [value for value in values if value is not None]
    if len(present) == 0:
        return None
    return sum(present)


def _sum_optional_float(values: list[float | None]) -> float | None:
    """None を除外して float を合算する。"""
    present = [value for value in values if value is not None]
    if len(present) == 0:
        return None
    return sum(present)


def _last_optional_float(values: list[float | None]) -> float | None:
    """最後の float 値を返す。"""
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _last_optional_text(values: list[str | None]) -> str | None:
    """最後の文字列を返す。"""
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _merge_code_edit_decisions(
    values: list[dict[str, int] | None],
) -> dict[str, int] | None:
    """code edit decisions を合算する。"""
    merged: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        for key, count in value.items():
            merged[key] = merged.get(key, 0) + count
    if len(merged) == 0:
        return None
    return merged
