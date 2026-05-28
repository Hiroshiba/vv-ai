"""コマンド別の実行後処理。"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from vv_ai.backends.github.client import GitHubClient, build_github_client
from vv_ai.backends.github.comments import (
    post_fork_push_failure_comment,
    post_issue_comment_safely,
)
from vv_ai.backends.github.models import (
    GitHubClientError,
    GitHubIssue,
    GitHubPullRequest,
)
from vv_ai.commands.output_parser import (
    ReviewThreadAction,
    parse_breakdown_dir,
    parse_address_output,
    parse_implement_issue_output,
    parse_pr_change_output,
    parse_title_body_output,
)
from vv_ai.executions.result import ExecutionResult
from vv_ai.git.operations import (
    GitOpsError,
    commit_all_changes,
    generate_patch,
    has_commits_ahead,
    push_branch,
    try_push_current_branch,
)
from vv_ai.inputs.resolve import ResolvedTarget
from vv_ai.workflow.preflight import ReadyExecution

_PR_CHANGE_COMMANDS = frozenset({"implement", "address"})


def handle_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str | None,
    pr_info: GitHubPullRequest | None,
    head_sha_before: str | None,
    fork_base_ref: str | None,
    env: Mapping[str, str],
) -> GitHubPullRequest | None:
    """コマンド固有の後処理を行う。作成された PR があれば返す。"""
    command_name = ready_execution.command.command
    if command_name in ("reply", "review", "confirm", "requirements", "arch", "detail"):
        _post_response_comment(ready_execution, execution_result, github_client)
    elif command_name == "breakdown":
        _handle_breakdown_post_execution(
            repo_root,
            ready_execution,
            execution_result,
            github_client,
        )
    elif command_name == "issue":
        _handle_issue_post_execution(ready_execution, execution_result, github_client)
    elif command_name in _PR_CHANGE_COMMANDS and implement_branch_name is not None:
        target = ready_execution.command.target
        if target is not None and target.kind == "pr":
            _handle_pr_change_post_execution(
                repo_root,
                ready_execution,
                execution_result,
                github_client,
                implement_branch_name,
                pr_info,
                head_sha_before,
                env,
            )
        elif command_name == "implement":
            return _handle_implement_issue_post_execution(
                repo_root,
                ready_execution,
                execution_result,
                github_client,
                implement_branch_name,
                fork_base_ref,
                env,
            )
    return None


def _is_github_target(target: ResolvedTarget | None) -> bool:
    return target is not None and target.backend == "github"


def _handle_implement_issue_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str,
    fork_base_ref: str | None,
    env: Mapping[str, str],
) -> GitHubPullRequest | None:
    command = ready_execution.command
    target = command.target

    if execution_result.status != "success":
        return None

    if command.dry_run or not _is_github_target(target):
        print(f"[dry-run/local] push と PR 作成をスキップします。ブランチ: {implement_branch_name}")
        return None

    assert target is not None
    assert target.repository_full_name is not None
    assert target.number is not None
    assert github_client is not None

    base_branch = ready_execution.config.pull_request_target_branch
    if base_branch is None:
        base_branch = github_client.get_default_branch(target.repository_full_name)

    response_text = execution_result.response_text
    if response_text is None:
        raise RuntimeError("AI からの PR タイトル、コミットメッセージ、本文がありません")

    pr_title, commit_message, pr_body = parse_implement_issue_output(response_text)

    committed = commit_all_changes(repo_root, commit_message)
    if committed:
        print(f"ワーキングツリーの変更をコミットしました: {commit_message}")

    commits_ahead_ref = fork_base_ref if fork_base_ref is not None else base_branch
    ahead = has_commits_ahead(repo_root, commits_ahead_ref)
    if not ahead:
        post_issue_comment_safely(
            github_client,
            target.repository_full_name,
            target.number,
            pr_body,
            "implement 変更なしコメント投稿",
        )
        print("変更コミットがないため push と PR 作成をスキップします")
        return None

    push_branch(repo_root, implement_branch_name, env.get("GITHUB_TOKEN"))

    pr = github_client.create_pull_request(
        target.repository_full_name,
        pr_title,
        pr_body,
        implement_branch_name,
        base_branch,
        maintainer_can_modify=True,
    )
    print(f"PR を作成しました: {pr.url}")
    return pr


def _handle_pr_change_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str,
    pr_info: GitHubPullRequest | None,
    head_sha_before: str | None,
    env: Mapping[str, str],
) -> None:
    command = ready_execution.command
    target = command.target

    if execution_result.status != "success":
        return

    if command.dry_run or not _is_github_target(target):
        print(f"[dry-run/local] push をスキップします。ブランチ: {implement_branch_name}")
        return

    assert target is not None
    assert target.number is not None

    response_text = execution_result.response_text
    if response_text is None:
        raise RuntimeError("AI からのコミットメッセージと本文がありません")

    review_thread_actions: list[ReviewThreadAction] = []
    if command.command == "address":
        address_output = parse_address_output(response_text, repo_root)
        commit_message = address_output.commit_message
        response_body = address_output.body
        review_thread_actions = address_output.review_thread_actions
    else:
        commit_message, response_body = parse_pr_change_output(response_text)

    committed = commit_all_changes(repo_root, commit_message)
    if committed:
        print(f"ワーキングツリーの変更をコミットしました: {commit_message}")

    if pr_info is None or not pr_info.is_cross_repository:
        push_branch(repo_root, implement_branch_name, env.get("GITHUB_TOKEN"))
        print(f"ブランチ `{implement_branch_name}` を push しました。")
        assert target.repository_full_name is not None
        _post_pr_change_response_comment(
            ready_execution,
            github_client,
            target.repository_full_name,
            target.number,
            response_body,
        )
        _apply_review_thread_actions(github_client, review_thread_actions)
        return

    assert target.repository_full_name is not None

    if try_push_current_branch(repo_root, env.get("GITHUB_TOKEN")):
        print(f"fork ブランチ `{implement_branch_name}` を push しました。")
        _post_pr_change_response_comment(
            ready_execution,
            github_client,
            target.repository_full_name,
            target.number,
            response_body,
        )
        _apply_review_thread_actions(github_client, review_thread_actions)
        return

    _post_fork_patch_fallback(
        repo_root,
        ready_execution,
        execution_result,
        github_client,
        target.repository_full_name,
        target.number,
        pr_info,
        head_sha_before,
        response_body,
    )


def _apply_review_thread_actions(
    github_client: GitHubClient | None,
    actions: list[ReviewThreadAction],
) -> None:
    if len(actions) == 0:
        return
    if github_client is None:
        raise RuntimeError("review thread 操作には GitHub client が必要です")

    for action in actions:
        if action.action == "resolve":
            github_client.resolve_review_thread(action.thread_id)
        elif action.action == "comment":
            github_client.add_pull_request_review_thread_reply(
                action.thread_id,
                action.body,
            )
        else:
            raise RuntimeError(f"未対応の review thread 操作です: {action.action}")


def _post_fork_patch_fallback(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    repository_full_name: str,
    number: int,
    pr_info: GitHubPullRequest,
    head_sha_before: str | None,
    response_body: str,
) -> None:
    if github_client is None:
        github_client = build_github_client()

    base_sha = head_sha_before if head_sha_before is not None else "HEAD~1"
    try:
        patch = generate_patch(repo_root, base_sha)
    except GitOpsError as exc:
        print(f"patch 生成に失敗しました: {exc}", file=sys.stderr)
        return

    execution_result.allow_edits_notice_posted = post_fork_push_failure_comment(
        github_client,
        repository_full_name,
        number,
        response_body,
        patch,
        pr_info,
        _get_allow_edits_notice_posted(ready_execution)
        or execution_result.allow_edits_notice_posted,
    )
    print("fork PR への push に失敗したため、patch コメント投稿を試みました。")


def _get_allow_edits_notice_posted(ready_execution: ReadyExecution) -> bool:
    session = ready_execution.resolved_session
    if session is None:
        return False
    return session.allow_edits_notice_posted


def _post_pr_change_response_comment(
    ready_execution: ReadyExecution,
    github_client: GitHubClient | None,
    repository_full_name: str,
    number: int,
    response_body: str,
) -> None:
    if ready_execution.command.dry_run or github_client is None:
        print(response_body)
        return

    try:
        github_client.create_issue_comment(repository_full_name, number, response_body)
    except GitHubClientError as exc:
        print(f"PR 変更反映の応答コメント投稿に失敗しました: {exc}", file=sys.stderr)


def _handle_issue_post_execution(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    command = ready_execution.command
    response_text = execution_result.response_text

    if execution_result.status != "success":
        return

    if response_text is None:
        raise RuntimeError("AI からの応答がありません")

    title, body = parse_title_body_output(response_text)
    repo = command.repo
    if repo is None:
        raise RuntimeError("Issue 作成先リポジトリが不明です")

    if command.dry_run:
        print(f"[dry-run] Issue 作成をスキップします。repo: {repo}, title: {title}")
        return

    assert github_client is not None
    issue = github_client.create_issue(repo, title, body)

    print(f"Issue を作成しました: {issue.url}")

    target = command.target
    if (
        command.comment_id is not None
        and target is not None
        and target.repository_full_name is not None
        and target.number is not None
    ):
        try:
            github_client.create_issue_comment(
                target.repository_full_name,
                target.number,
                f"Created: {issue.url}",
            )
        except GitHubClientError as exc:
            print(f"Issue リンクのコメント投稿に失敗しました: {exc}", file=sys.stderr)


def _handle_breakdown_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    command = ready_execution.command
    response_text = execution_result.response_text

    if execution_result.status != "success":
        return

    if response_text is None:
        raise RuntimeError("AI からの応答がありません")

    tasks = parse_breakdown_dir(response_text, repo_root)
    target = command.target

    if command.dry_run:
        repo = command.repo or (target.repository_full_name if target else None) or "(不明)"
        print(f"[dry-run] サブ Issue 作成をスキップします。repo: {repo}, タスク数: {len(tasks)}")
        for title, _ in tasks:
            print(f"  - {title}")
        return

    assert github_client is not None
    assert target is not None
    assert target.number is not None
    assert target.repository_full_name is not None

    repo = target.repository_full_name

    created = _create_breakdown_sub_issues(
        github_client,
        repo,
        target.number,
        tasks,
    )

    if command.comment_id is not None and target.repository_full_name is not None:
        links = "\n".join(f"- {issue.url}" for issue in created)
        summary = f"サブ Issue を {len(created)} 件作成しました:\n{links}"
        try:
            github_client.create_issue_comment(
                target.repository_full_name,
                target.number,
                summary,
            )
        except GitHubClientError as exc:
            print(f"サマリコメント投稿に失敗しました: {exc}", file=sys.stderr)


def _create_breakdown_sub_issues(
    github_client: GitHubClient,
    repo: str,
    parent_number: int,
    tasks: list[tuple[str, str]],
) -> list[GitHubIssue]:
    created: list[GitHubIssue] = []
    linked_issue_ids: list[int] = []
    try:
        for title, body in tasks:
            issue = github_client.create_issue(repo, title, body)
            github_client.add_sub_issue(repo, parent_number, issue.id)
            linked_issue_ids.append(issue.id)
            created.append(issue)
            print(f"サブ Issue を作成しました: {issue.url}")
    except GitHubClientError:
        _rollback_breakdown_sub_issues(
            github_client,
            repo,
            parent_number,
            linked_issue_ids,
        )
        raise
    return created


def _rollback_breakdown_sub_issues(
    github_client: GitHubClient,
    repo: str,
    parent_number: int,
    linked_issue_ids: list[int],
) -> None:
    for child_issue_id in reversed(linked_issue_ids):
        github_client.remove_sub_issue(repo, parent_number, child_issue_id)


def _post_response_comment(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    command = ready_execution.command
    response_text = execution_result.response_text
    if response_text is None:
        return

    target = command.target
    if command.dry_run or github_client is None or not _is_github_target(target):
        print(response_text)
        return

    assert target is not None
    assert target.repository_full_name is not None
    assert target.number is not None
    try:
        github_client.create_issue_comment(
            target.repository_full_name,
            target.number,
            response_text,
        )
    except GitHubClientError as exc:
        print(f"コメント投稿に失敗しました: {exc}", file=sys.stderr)
