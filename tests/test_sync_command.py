"""sync 用 Git helper の単体テスト。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.execution import ExecutionResult, ExecutionStatus
from vv_ai.git_ops import (
    GitOpsError,
    MergeAttempt,
    commit_merge_no_edit,
    ensure_worktree_clean,
    generate_diff_patch,
    is_ancestor,
    list_changed_files,
    list_conflict_marker_files,
    list_staged_files,
    list_unmerged_files,
    merge_no_ff_no_commit,
)
from vv_ai.github import (
    GitHubActor,
    GitHubClientError,
    GitHubPullRequest,
    GitHubStatusCheckSummary,
    GitHubPullRequestSyncState,
)
from vv_ai.metrics_artifact import MetricsBehavior, MetricsUsage, ProviderSpecificMetrics
from vv_ai.preflight import ReadyExecution
from vv_ai.provider import ResolvedProvider, get_provider_spec
from vv_ai.report_artifact import ReportSections
from vv_ai.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.session import ResolvedSession, SessionKey, SessionStateRef
from vv_ai.sync_command import run_sync_command


def test_run_sync_command_merges_and_pushes_without_conflict(tmp_path: Path) -> None:
    """conflict なしの場合は merge commit を作成して push する。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)
    results = [
        _make_execution_result("success", "整合性問題なし", "s1"),
        _make_execution_result("success", "コメント本文", "s2"),
    ]

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch") as checkout,
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=False),
        patch(
            "vv_ai.sync_command.merge_no_ff_no_commit",
            return_value=MergeAttempt(True, [], "", ""),
        ),
        patch("vv_ai.sync_command.run_git_command") as run_git,
        patch("vv_ai.sync_command.commit_merge_no_edit", return_value="merge-sha") as commit_merge,
        patch("vv_ai.sync_command.execute_provider", side_effect=results) as provider,
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "success"
    checkout.assert_called_once_with(tmp_path, "feature")
    commit_merge.assert_called_once_with(tmp_path)
    run_git.assert_called_once_with(tmp_path, "add", "-A")
    push_branch.assert_called_once_with(tmp_path, "feature", None)
    github_client.create_issue_comment.assert_called_once_with(
        "org/repo",
        5,
        "コメント本文",
    )
    assert provider.call_count == 2
    assert ready.resolved_session is not None
    assert ready.resolved_session.state_ref == SessionStateRef(provider_session_id="s2")


def test_run_sync_command_skips_merge_when_base_is_ancestor(tmp_path: Path) -> None:
    """base branch が取り込み済みなら merge commit を作らない。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)
    results = [
        _make_execution_result("success", "修正不要", "s1"),
        _make_execution_result("success", "コメント本文", "s2"),
    ]

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch("vv_ai.sync_command.merge_no_ff_no_commit") as merge,
        patch("vv_ai.sync_command.commit_merge_no_edit") as commit_merge,
        patch("vv_ai.sync_command.execute_provider", side_effect=results),
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.push_branch"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "success"
    merge.assert_not_called()
    commit_merge.assert_not_called()
    github_client.create_issue_comment.assert_called_once()


def test_run_sync_command_resolves_conflict_with_ai_before_merge_commit(
    tmp_path: Path,
) -> None:
    """conflict ありの場合は AI 解消後に wrapper が merge commit を作る。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)
    results = [
        _make_execution_result("success", "conflict 解消", "s1"),
        _make_execution_result("success", "整合性問題なし", "s2"),
        _make_execution_result("success", "コメント本文", "s3"),
    ]

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=False),
        patch(
            "vv_ai.sync_command.merge_no_ff_no_commit",
            return_value=MergeAttempt(False, ["a.txt"], "", ""),
        ),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_changed_files", return_value=["a.txt"]),
        patch(
            "vv_ai.sync_command.list_conflict_marker_files",
            side_effect=[["a.txt"], []],
        ),
        patch("vv_ai.sync_command.list_unmerged_files", return_value=[]),
        patch("vv_ai.sync_command.run_git_command") as run_git,
        patch("vv_ai.sync_command.commit_merge_no_edit", return_value="merge-sha") as commit_merge,
        patch("vv_ai.sync_command.execute_provider", side_effect=results) as provider,
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.push_branch"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    conflict_prompt = provider.call_args_list[0].args[4]
    assert result.status == "success"
    assert "conflict 解消だけ" in conflict_prompt
    assert "a.txt" in conflict_prompt
    run_git.assert_called_once_with(tmp_path, "add", "--", "a.txt")
    commit_merge.assert_called_once_with(tmp_path)


def test_run_sync_command_stops_when_conflict_remains(tmp_path: Path) -> None:
    """未解消 conflict が残る場合は commit と push を行わない。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=False),
        patch(
            "vv_ai.sync_command.merge_no_ff_no_commit",
            return_value=MergeAttempt(False, ["a.txt"], "", ""),
        ),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_changed_files", return_value=["a.txt"]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.list_unmerged_files", return_value=["a.txt"]),
        patch("vv_ai.sync_command.run_git_command") as run_git,
        patch(
            "vv_ai.sync_command.execute_provider",
            return_value=_make_execution_result("success", "途中", "s1"),
        ),
        patch("vv_ai.sync_command.commit_merge_no_edit") as commit_merge,
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "failure"
    run_git.assert_not_called()
    commit_merge.assert_not_called()
    push_branch.assert_not_called()


def test_run_sync_command_stops_markerless_conflict_without_staging(
    tmp_path: Path,
) -> None:
    """marker がない conflict は wrapper が stage せず未解消として停止する。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=False),
        patch(
            "vv_ai.sync_command.merge_no_ff_no_commit",
            return_value=MergeAttempt(False, ["binary.dat"], "", ""),
        ),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_changed_files", return_value=["binary.dat"]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.list_unmerged_files", return_value=["binary.dat"]),
        patch("vv_ai.sync_command.run_git_command") as run_git,
        patch(
            "vv_ai.sync_command.execute_provider",
            return_value=_make_execution_result("success", "途中", "s1"),
        ),
        patch("vv_ai.sync_command.commit_merge_no_edit") as commit_merge,
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "failure"
    run_git.assert_not_called()
    commit_merge.assert_not_called()
    push_branch.assert_not_called()


def test_run_sync_command_commits_consistency_fix_after_merge(
    tmp_path: Path,
) -> None:
    """AI が整合性修正を残した場合は merge commit 後に別 commit を作る。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)
    results = [
        _make_execution_result("success", "修正した", "s1"),
        _make_execution_result("success", "コメント本文", "s2"),
    ]

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=False),
        patch(
            "vv_ai.sync_command.merge_no_ff_no_commit",
            return_value=MergeAttempt(True, [], "", ""),
        ),
        patch("vv_ai.sync_command.run_git_command"),
        patch("vv_ai.sync_command.commit_merge_no_edit", return_value="merge-sha"),
        patch("vv_ai.sync_command.execute_provider", side_effect=results),
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=["b.txt"]),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=True) as commit_all,
        patch("vv_ai.sync_command.push_branch"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "success"
    commit_all.assert_called_once_with(tmp_path, "chore: sync 整合性を修正する")


def test_run_sync_command_stops_when_ai_commits(tmp_path: Path) -> None:
    """AI が commit した場合は HEAD SHA の変化で失敗する。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", side_effect=["sha0", "sha0", "sha1"]),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch(
            "vv_ai.sync_command.execute_provider",
            return_value=_make_execution_result("success", "修正した", "s1"),
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "failure"
    assert result.report_sections.summary == "AI が commit を作成したため sync を停止しました。"
    push_branch.assert_not_called()


def test_run_sync_command_stops_when_ai_stages_diff(tmp_path: Path) -> None:
    """AI が staged diff を残した場合は失敗する。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch(
            "vv_ai.sync_command.execute_provider",
            return_value=_make_execution_result("success", "修正した", "s1"),
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=["a.txt"]),
        patch("vv_ai.sync_command.list_staged_files", return_value=["a.txt"]),
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "failure"
    assert "staged diff" in result.report_sections.summary
    push_branch.assert_not_called()


def test_run_sync_command_final_prompt_includes_github_state(tmp_path: Path) -> None:
    """push 後の GitHub 状態を最終コメント AI prompt に含める。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)
    github_client.get_pull_request_sync_state.return_value = GitHubPullRequestSyncState(
        mergeable="MERGEABLE",
        merge_state_status="CLEAN",
        status_check_summary=GitHubStatusCheckSummary(
            success_count=1,
            failure_count=0,
            pending_count=0,
            unknown_count=0,
        ),
    )
    results = [
        _make_execution_result("success", "修正不要", "s1"),
        _make_execution_result("success", "コメント本文", "s2"),
    ]

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch("vv_ai.sync_command.execute_provider", side_effect=results) as provider,
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.push_branch"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    final_prompt = provider.call_args_list[1].args[4]
    assert result.status == "success"
    assert "GitHub 状態" in final_prompt
    assert "CLEAN" in final_prompt


def test_run_sync_command_does_not_post_failed_final_comment(
    tmp_path: Path,
) -> None:
    """最終コメント生成 AI が失敗した場合は応答本文を投稿しない。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)
    results = [
        _make_execution_result("success", "修正不要", "s1"),
        _make_execution_result("failure", "エラー本文", "s2"),
    ]

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch("vv_ai.sync_command.execute_provider", side_effect=results),
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.push_branch"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "failure"
    assert result.report_sections.summary == "最終コメント生成 AI が失敗しました。"
    github_client.create_issue_comment.assert_not_called()


def test_run_sync_command_comment_post_failure_does_not_fail_sync(
    tmp_path: Path,
) -> None:
    """sync コメント投稿に失敗しても push 済みの sync は成功扱いにする。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(False, True)
    github_client = _make_github_client(pr)
    github_client.create_issue_comment.side_effect = GitHubClientError("一時エラー")
    results = [
        _make_execution_result("success", "修正不要", "s1"),
        _make_execution_result("success", "コメント本文", "s2"),
    ]

    with (
        patch("vv_ai.sync_command.fetch_and_checkout_branch"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch("vv_ai.sync_command.execute_provider", side_effect=results),
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.push_branch"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "success"
    github_client.create_issue_comment.assert_called_once()


def test_run_sync_command_fork_push_failure_posts_patch_and_fails(
    tmp_path: Path,
) -> None:
    """fork PR で push できない場合は patch コメントを投稿して failure を返す。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(True, True)
    github_client = _make_github_client(pr)

    with (
        patch("vv_ai.sync_command.checkout_fork_pr") as checkout,
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch(
            "vv_ai.sync_command.execute_provider",
            return_value=_make_execution_result("success", "修正不要", "s1"),
        ),
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.try_push_current_branch", return_value=False),
        patch("vv_ai.sync_command.generate_diff_patch", return_value="diff --git a/a b/a"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    assert result.status == "failure"
    checkout.assert_called_once_with(tmp_path, "org/repo", 5)
    github_client.create_issue_comment.assert_called_once()
    assert "diff --git" in github_client.create_issue_comment.call_args.args[2]


def test_run_sync_command_fork_push_failure_posts_allow_edits_notice(
    tmp_path: Path,
) -> None:
    """fork PR で maintainer edits が無効なら有効化案内を一度だけ載せる。"""
    ready = _make_ready_execution()
    pr = _make_pull_request(True, False)
    github_client = _make_github_client(pr)

    with (
        patch("vv_ai.sync_command.checkout_fork_pr"),
        patch("vv_ai.sync_command.ensure_worktree_clean"),
        patch("vv_ai.sync_command.fetch_remote"),
        patch("vv_ai.sync_command.get_head_sha", return_value="sha0"),
        patch("vv_ai.sync_command.is_ancestor", return_value=True),
        patch(
            "vv_ai.sync_command.execute_provider",
            return_value=_make_execution_result("success", "修正不要", "s1"),
        ),
        patch(
            "vv_ai.sync_command._validate_provider_did_not_take_over_git",
            return_value=None,
        ),
        patch("vv_ai.sync_command.list_changed_files", return_value=[]),
        patch("vv_ai.sync_command.list_staged_files", return_value=[]),
        patch("vv_ai.sync_command.list_conflict_marker_files", return_value=[]),
        patch("vv_ai.sync_command.commit_all_changes", return_value=False),
        patch("vv_ai.sync_command.try_push_current_branch", return_value=False),
        patch("vv_ai.sync_command.generate_diff_patch", return_value="diff --git a/a b/a"),
    ):
        result = run_sync_command(tmp_path, ready, github_client, {}, 0.1)

    body = github_client.create_issue_comment.call_args.args[2]
    assert result.status == "failure"
    assert result.allow_edits_notice_posted is True
    assert "Allow edits from maintainers" in body


def test_ensure_worktree_clean_rejects_dirty_worktree(tmp_path: Path) -> None:
    """ensure_worktree_clean は変更がある作業ツリーを拒否する。"""
    repo = _init_repo(tmp_path)
    _write(repo, "file.txt", "変更\n")

    with pytest.raises(GitOpsError, match="未コミット"):
        ensure_worktree_clean(repo)


def test_list_changed_and_staged_files_detects_worktree_state(tmp_path: Path) -> None:
    """list_changed_files と list_staged_files は変更状態を返す。"""
    repo = _init_repo(tmp_path)
    _write(repo, "変更.txt", "変更\n")
    _write(repo, "未追跡.txt", "追加\n")
    _run_git(repo, "add", "変更.txt")

    assert list_changed_files(repo) == ["変更.txt", "未追跡.txt"]
    assert list_staged_files(repo) == ["変更.txt"]


def test_merge_no_ff_no_commit_can_commit_successful_merge(tmp_path: Path) -> None:
    """merge_no_ff_no_commit は成功した merge を commit できる状態にする。"""
    repo = _init_repo(tmp_path)
    base_sha = _run_git(repo, "rev-parse", "HEAD").strip()
    _run_git(repo, "checkout", "-b", "incoming")
    _write(repo, "incoming.txt", "追加\n")
    _run_git(repo, "add", "incoming.txt")
    _run_git(repo, "commit", "-m", "incoming")
    _run_git(repo, "checkout", "main")

    attempt = merge_no_ff_no_commit(repo, "incoming")

    assert attempt.succeeded is True
    assert attempt.unmerged_files == []
    assert list_staged_files(repo) == ["incoming.txt"]
    merge_sha = commit_merge_no_edit(repo)
    assert merge_sha != base_sha
    assert is_ancestor(repo, base_sha, merge_sha) is True
    assert "diff --git" in generate_diff_patch(repo, base_sha)
    ensure_worktree_clean(repo)


def test_merge_no_ff_no_commit_returns_conflict_files(tmp_path: Path) -> None:
    """merge_no_ff_no_commit は conflict を構造化して返す。"""
    repo = _init_repo(tmp_path)
    conflict_path = "日本語.txt"
    _write(repo, conflict_path, "base\n")
    _run_git(repo, "add", conflict_path)
    _run_git(repo, "commit", "-m", "日本語ファイル追加")
    _run_git(repo, "checkout", "-b", "incoming")
    _write(repo, conflict_path, "incoming\n")
    _run_git(repo, "add", conflict_path)
    _run_git(repo, "commit", "-m", "incoming")
    _run_git(repo, "checkout", "main")
    _write(repo, conflict_path, "main\n")
    _run_git(repo, "add", conflict_path)
    _run_git(repo, "commit", "-m", "main")

    attempt = merge_no_ff_no_commit(repo, "incoming")

    assert attempt.succeeded is False
    assert attempt.unmerged_files == [conflict_path]
    assert list_unmerged_files(repo) == [conflict_path]
    assert list_conflict_marker_files(repo, [conflict_path]) == [conflict_path]


def test_list_conflict_marker_files_ignores_resolved_content(tmp_path: Path) -> None:
    """list_conflict_marker_files は marker がないファイルを除外する。"""
    repo = _init_repo(tmp_path)
    _write(repo, "file.txt", "解決済み\n")

    assert list_conflict_marker_files(repo, ["file.txt"]) == []


def _make_ready_execution() -> ReadyExecution:
    """テスト用 ReadyExecution を生成する。"""
    target = ResolvedTarget(
        backend="github",
        kind="pr",
        canonical_id="org/repo#5",
        repository_full_name="org/repo",
        number=5,
        url="https://github.com/org/repo/pull/5",
    )
    command = ResolvedCommand(
        event_name="local",
        command="sync",
        target_url="https://github.com/org/repo/pull/5",
        target_type=None,
        target_number=None,
        has_target=True,
        dry_run=False,
        target=target,
    )
    session = ResolvedSession(
        requested_mode="new",
        lane="main",
        key=SessionKey(
            backend="github",
            target_key="org/repo#5",
            provider="codex",
            lane="main",
            canonical_key="github/org/repo#5/codex/main",
        ),
        restore_strategy="new",
        save_manifest_path="/tmp/session.json",
    )
    return ReadyExecution(
        command=command,
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=ResolvedProvider(
            spec=get_provider_spec("codex"),
            source="explicit",
        ),
        resolved_session=session,
        workflow_id="workflow",
    )


def _make_pull_request(
    is_cross_repository: bool,
    maintainer_can_modify: bool,
) -> GitHubPullRequest:
    """テスト用 GitHubPullRequest を生成する。"""
    head_repository_full_name = "fork/repo" if is_cross_repository else "org/repo"
    return GitHubPullRequest(
        repository_full_name="org/repo",
        number=5,
        title="テスト PR",
        body="本文",
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url="https://github.com/org/repo/pull/5",
        head_ref_name="feature",
        base_ref_name="main",
        head_repository_full_name=head_repository_full_name,
        is_cross_repository=is_cross_repository,
        maintainer_can_modify=maintainer_can_modify,
    )


def _make_github_client(pr: GitHubPullRequest) -> MagicMock:
    """テスト用 GitHubClient mock を生成する。"""
    github_client = MagicMock()
    github_client.get_pull_request.return_value = pr
    github_client.get_pull_request_sync_state.return_value = GitHubPullRequestSyncState(
        mergeable="UNKNOWN",
        merge_state_status="UNKNOWN",
        status_check_summary=GitHubStatusCheckSummary(
            success_count=0,
            failure_count=0,
            pending_count=0,
            unknown_count=0,
        ),
    )
    return github_client


def _make_execution_result(
    status: ExecutionStatus,
    response_text: str,
    provider_session_id: str,
) -> ExecutionResult:
    """テスト用 ExecutionResult を生成する。"""
    return ExecutionResult(
        status=status,
        report_sections=ReportSections(
            summary="summary",
            changes=response_text,
            decisions="decisions",
            validation="validation",
            risks_open_questions="risks",
            next_actions="next",
            notes="notes",
        ),
        usage=MetricsUsage(input_tokens=1, output_tokens=1),
        behavior=MetricsBehavior(total_turns=1),
        tools={},
        steps={},
        provider_specific=ProviderSpecificMetrics(),
        state_ref=SessionStateRef(provider_session_id=provider_session_id),
        provider_session_path=None,
        allow_edits_notice_posted=False,
        response_text=response_text,
    )


def _init_repo(tmp_path: Path) -> Path:
    """テスト用 repository を作成する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "テストユーザー")
    _write(repo, "file.txt", "base\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-m", "base")
    return repo


def _write(repo: Path, relative_path: str, text: str) -> None:
    """repository 内のファイルへ文字列を書く。"""
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_git(repo: Path, *args: str) -> str:
    """テスト用 Git コマンドを実行して標準出力を返す。"""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout
