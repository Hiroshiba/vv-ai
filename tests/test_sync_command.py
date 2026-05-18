"""sync 用 Git helper の単体テスト。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.execution import ExecutionResult, ExecutionStatus
from vv_ai.git_ops import (
    GitOpsError,
    commit_merge_no_edit,
    ensure_worktree_clean,
    fetch_and_checkout_branch,
    fetch_remote_branch,
    generate_diff_patch,
    is_ancestor,
    list_changed_files,
    list_conflict_marker_files,
    list_staged_files,
    list_unstaged_files,
    list_unmerged_files,
    merge_no_ff_no_commit,
    stage_paths,
)
from vv_ai.github import (
    GitHubActor,
    GitHubPullRequest,
    GitHubPullRequestSyncState,
    GitHubStatusCheckSummary,
)
from vv_ai.metrics_artifact import MetricsBehavior, MetricsUsage, ProviderSpecificMetrics
from vv_ai.preflight import ReadyExecution
from vv_ai.provider import ResolvedProvider, get_provider_spec
from vv_ai.report_artifact import ReportSections
from vv_ai.resolve import ResolvedCommand, ResolvedTarget
from vv_ai.session import ResolvedSession, SessionKey, SessionStateRef
from vv_ai.sync_command import SyncCommandError, run_sync_command


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


def test_stage_paths_stages_only_specified_paths(tmp_path: Path) -> None:
    """stage_paths は指定 path だけを stage する。"""
    repo = _init_repo(tmp_path)
    _write(repo, "対象.txt", "対象\n")
    _write(repo, "対象外.txt", "対象外\n")

    stage_paths(repo, ["対象.txt"])

    assert list_staged_files(repo) == ["対象.txt"]
    assert list_changed_files(repo) == ["対象.txt", "対象外.txt"]


def test_stage_paths_rejects_empty_paths(tmp_path: Path) -> None:
    """stage_paths は空の path 一覧を拒否する。"""
    repo = _init_repo(tmp_path)

    with pytest.raises(GitOpsError, match="stage 対象"):
        stage_paths(repo, [])


def test_fetch_and_checkout_branch_checks_out_branch_from_shallow_clone(
    tmp_path: Path,
) -> None:
    """fetch_and_checkout_branch は shallow clone から別ブランチを checkout する。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "checkout", "-b", "feature")
    _write(source, "feature.txt", "feature\n")
    _run_git(source, "add", "feature.txt")
    _run_git(source, "commit", "-m", "feature")
    _run_git(source, "checkout", "main")
    clone = _clone_main_only(tmp_path, source, "feature-checkout")

    fetch_and_checkout_branch(clone, "feature")

    assert _run_git(clone, "rev-parse", "--is-shallow-repository").strip() == "true"
    assert _run_git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature"
    assert (clone / "feature.txt").read_text(encoding="utf-8") == "feature\n"


def test_fetch_remote_branch_creates_remote_tracking_ref_from_shallow_clone(
    tmp_path: Path,
) -> None:
    """fetch_remote_branch は shallow clone から remote-tracking ref を作成する。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "checkout", "-b", "base")
    _write(source, "base.txt", "base\n")
    _run_git(source, "add", "base.txt")
    _run_git(source, "commit", "-m", "base branch")
    _run_git(source, "checkout", "main")
    clone = _clone_main_only(tmp_path, source, "base-fetch")

    fetch_remote_branch(clone, "origin", "base")

    assert _run_git(clone, "rev-parse", "--is-shallow-repository").strip() == "true"
    assert _run_git(clone, "rev-parse", "origin/base").strip() == _run_git(
        source, "rev-parse", "refs/heads/base"
    ).strip()


def test_fetch_remote_branch_fetches_branch_when_tag_has_same_name(
    tmp_path: Path,
) -> None:
    """fetch_remote_branch は同名 tag ではなく branch の tip を取得する。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "tag", "release")
    _run_git(source, "checkout", "-b", "release")
    _write(source, "release.txt", "release\n")
    _run_git(source, "add", "release.txt")
    _run_git(source, "commit", "-m", "release branch")
    _run_git(source, "checkout", "main")
    clone = _clone_main_only(tmp_path, source, "release-fetch")

    fetch_remote_branch(clone, "origin", "release")

    assert _run_git(clone, "rev-parse", "origin/release").strip() == _run_git(
        source, "rev-parse", "refs/heads/release"
    ).strip()
    assert _run_git(clone, "rev-parse", "origin/release").strip() != _run_git(
        source, "rev-parse", "refs/tags/release"
    ).strip()


def test_fetch_remote_branch_force_updates_remote_tracking_ref(
    tmp_path: Path,
) -> None:
    """fetch_remote_branch は remote-tracking ref を強制更新する。"""
    source = _init_repo_at(tmp_path / "source")
    main_sha = _run_git(source, "rev-parse", "main").strip()
    _run_git(source, "checkout", "-b", "feature")
    _write(source, "feature.txt", "feature\n")
    _run_git(source, "add", "feature.txt")
    _run_git(source, "commit", "-m", "feature")
    feature_sha = _run_git(source, "rev-parse", "feature").strip()
    _run_git(source, "checkout", "main")
    clone = _clone_main_only(tmp_path, source, "force-fetch")

    fetch_remote_branch(clone, "origin", "feature")
    _run_git(source, "branch", "-f", "feature", "main")
    fetch_remote_branch(clone, "origin", "feature")

    assert _run_git(clone, "rev-parse", "origin/feature").strip() == main_sha
    assert _run_git(clone, "rev-parse", "origin/feature").strip() != feature_sha


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


def test_list_unstaged_files_detects_worktree_only_changes(tmp_path: Path) -> None:
    """list_unstaged_files は未 stage の tracked 変更を返す。"""
    repo = _init_repo(tmp_path)
    _write(repo, "file.txt", "変更\n")

    assert list_unstaged_files(repo) == ["file.txt"]


def test_run_sync_command_skips_push_when_base_is_ancestor(tmp_path: Path) -> None:
    """run_sync_command は base 取り込み済みなら push しない。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "checkout", "-b", "feature")
    _write(source, "feature.txt", "feature\n")
    _run_git(source, "add", "feature.txt")
    _run_git(source, "commit", "-m", "feature")
    _run_git(source, "checkout", "main")
    clone = _clone_main_only(tmp_path, source, "sync-no-push")
    github_client = _make_github_client(is_cross_repository=False)
    ready_execution = _make_ready_execution()

    with (
        patch(
            "vv_ai.sync_command.execute_provider",
            side_effect=[
                _make_execution_result("success", "整合性確認完了"),
                _make_execution_result("success", "BODY:\nsync 完了"),
            ],
        ),
        patch("vv_ai.sync_command.push_branch") as push_branch,
        patch("vv_ai.sync_command.try_push_current_branch") as try_push_current_branch,
    ):
        result = run_sync_command(clone, ready_execution, github_client, {}, 0.0)

    assert result.status == "success"
    push_branch.assert_not_called()
    try_push_current_branch.assert_not_called()
    assert github_client.comments == [("org/repo", 1, "sync 完了")]


def test_run_sync_command_pushes_after_merge_commit(tmp_path: Path) -> None:
    """run_sync_command は merge commit 作成後に head branch を push する。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "checkout", "-b", "feature")
    _write(source, "feature.txt", "feature\n")
    _run_git(source, "add", "feature.txt")
    _run_git(source, "commit", "-m", "feature")
    _run_git(source, "checkout", "main")
    _write(source, "main.txt", "main\n")
    _run_git(source, "add", "main.txt")
    _run_git(source, "commit", "-m", "main advance")
    clone = _clone_all_branches(tmp_path, source, "sync-push")
    github_client = _make_github_client(is_cross_repository=False)
    ready_execution = _make_ready_execution()

    with (
        patch(
            "vv_ai.sync_command.execute_provider",
            side_effect=[
                _make_execution_result("success", "整合性確認完了"),
                _make_execution_result("success", "BODY:\nsync 完了"),
            ],
        ),
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(
            clone,
            ready_execution,
            github_client,
            {"GITHUB_TOKEN": "token"},
            0.0,
        )

    assert result.status == "success"
    push_branch.assert_called_once_with(clone, "feature", "token")


def test_run_sync_command_passes_consistency_result_to_final_prompt_when_session_is_new(
    tmp_path: Path,
) -> None:
    """run_sync_command は session 継続なしなら整合性確認結果を最終 prompt に渡す。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "checkout", "-b", "feature")
    _write(source, "feature.txt", "feature\n")
    _run_git(source, "add", "feature.txt")
    _run_git(source, "commit", "-m", "feature")
    _run_git(source, "checkout", "main")
    clone = _clone_main_only(tmp_path, source, "sync-final-prompt")
    github_client = _make_github_client(is_cross_repository=False)
    ready_execution = _make_ready_execution()
    prompts: list[str] = []

    def execute_provider_mock(
        repo_root: Path,
        ready_execution: ReadyExecution,
        env: object,
        preflight_duration_seconds: float,
        provider_prompt: str,
    ) -> ExecutionResult:
        prompts.append(provider_prompt)
        if len(prompts) == 1:
            return _make_execution_result("success", "整合性確認で追従漏れを修正しました")
        return _make_execution_result("success", "BODY:\nsync 完了")

    with patch("vv_ai.sync_command.execute_provider", side_effect=execute_provider_mock):
        result = run_sync_command(clone, ready_execution, github_client, {}, 0.0)

    assert result.status == "success"
    assert "整合性確認 AI の出力:\n整合性確認で追従漏れを修正しました" in prompts[1]


def test_run_sync_command_rejects_consistency_marker_before_commit(
    tmp_path: Path,
) -> None:
    """run_sync_command は整合性修正 commit 前に conflict marker を拒否する。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "checkout", "-b", "feature")
    _write(source, "feature.txt", "feature\n")
    _run_git(source, "add", "feature.txt")
    _run_git(source, "commit", "-m", "feature")
    _run_git(source, "checkout", "main")
    clone = _clone_main_only(tmp_path, source, "sync-consistency-marker")
    github_client = _make_github_client(is_cross_repository=False)
    ready_execution = _make_ready_execution()

    def execute_provider_mock(
        repo_root: Path,
        ready_execution: ReadyExecution,
        env: object,
        preflight_duration_seconds: float,
        provider_prompt: str,
    ) -> ExecutionResult:
        _write(repo_root, "marker.txt", "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> main\n")
        return _make_execution_result("success", "整合性修正")

    with patch("vv_ai.sync_command.execute_provider", side_effect=execute_provider_mock):
        result = run_sync_command(clone, ready_execution, github_client, {}, 0.0)

    assert result.status == "failure"
    assert result.response_text == "整合性確認 AI が conflict marker を残しました: marker.txt"


def test_run_sync_command_rejects_ai_staging_conflict_file(tmp_path: Path) -> None:
    """run_sync_command は conflict 解消 AI の stage を拒否する。"""
    source = _make_conflict_source(tmp_path)
    clone = _clone_all_branches(tmp_path, source, "sync-ai-stage")
    github_client = _make_github_client(is_cross_repository=False)
    ready_execution = _make_ready_execution()

    with (
        patch(
            "vv_ai.sync_command.execute_provider",
            side_effect=_resolve_conflict_and_stage,
        ),
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(clone, ready_execution, github_client, {}, 0.0)

    assert result.status == "failure"
    assert result.response_text == "conflict 解消 AI が stage しました"
    push_branch.assert_not_called()


def test_run_sync_command_rejects_ai_changes_outside_conflict_files(
    tmp_path: Path,
) -> None:
    """run_sync_command は conflict file 以外の AI 変更を拒否する。"""
    source = _make_conflict_source(tmp_path)
    clone = _clone_all_branches(tmp_path, source, "sync-ai-outside-change")
    github_client = _make_github_client(is_cross_repository=False)
    ready_execution = _make_ready_execution()

    with (
        patch(
            "vv_ai.sync_command.execute_provider",
            side_effect=_resolve_conflict_and_change_other_file,
        ),
        patch("vv_ai.sync_command.push_branch") as push_branch,
    ):
        result = run_sync_command(clone, ready_execution, github_client, {}, 0.0)

    assert result.status == "failure"
    assert result.response_text == "conflict file 以外が変更されました: other.txt"
    push_branch.assert_not_called()


def test_run_sync_command_rejects_non_pr_target(tmp_path: Path) -> None:
    """run_sync_command は PR 以外の target を拒否する。"""
    command = ResolvedCommand(
        event_name="local",
        command="sync",
        target_type="issue",
        target_number=1,
        has_target=True,
        target=ResolvedTarget(
            backend="github",
            kind="issue",
            canonical_id="github:org/repo#1",
            repository_full_name="org/repo",
            number=1,
        ),
    )
    ready_execution = _make_ready_execution_with_command(command)

    with pytest.raises(SyncCommandError):
        run_sync_command(tmp_path, ready_execution, _make_github_client(False), {}, 0.0)


def _init_repo(tmp_path: Path) -> Path:
    """テスト用 repository を作成する。"""
    return _init_repo_at(tmp_path / "repo")


def _init_repo_at(repo: Path) -> Path:
    """指定 path にテスト用 repository を作成する。"""
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "テストユーザー")
    _write(repo, "file.txt", "base\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-m", "base")
    return repo


def _clone_main_only(tmp_path: Path, source: Path, name: str) -> Path:
    """main だけを shallow clone した repository を作成する。"""
    clone = tmp_path / name
    result = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "main",
            "--single-branch",
            source.as_uri(),
            str(clone),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    _run_git(clone, "config", "user.email", "test@example.com")
    _run_git(clone, "config", "user.name", "テストユーザー")
    return clone


def _clone_all_branches(tmp_path: Path, source: Path, name: str) -> Path:
    """全履歴を持つテスト用 repository clone を作成する。"""
    clone = tmp_path / name
    result = subprocess.run(
        [
            "git",
            "clone",
            source.as_uri(),
            str(clone),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    _run_git(clone, "checkout", "main")
    _run_git(clone, "config", "user.email", "test@example.com")
    _run_git(clone, "config", "user.name", "テストユーザー")
    return clone


def _make_conflict_source(tmp_path: Path) -> Path:
    """sync conflict テスト用 repository を作成する。"""
    source = _init_repo_at(tmp_path / "source")
    _run_git(source, "checkout", "-b", "feature")
    _write(source, "file.txt", "feature\n")
    _run_git(source, "add", "file.txt")
    _run_git(source, "commit", "-m", "feature")
    _run_git(source, "checkout", "main")
    _write(source, "file.txt", "main\n")
    _run_git(source, "add", "file.txt")
    _run_git(source, "commit", "-m", "main")
    return source


def _resolve_conflict_and_stage(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: object,
    preflight_duration_seconds: float,
    provider_prompt: str,
) -> ExecutionResult:
    """conflict file を解消して stage する provider mock。"""
    _write(repo_root, "file.txt", "resolved\n")
    _run_git(repo_root, "add", "file.txt")
    return _make_execution_result("success", "conflict 解消")


def _resolve_conflict_and_change_other_file(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: object,
    preflight_duration_seconds: float,
    provider_prompt: str,
) -> ExecutionResult:
    """conflict file と対象外ファイルを変更する provider mock。"""
    _write(repo_root, "file.txt", "resolved\n")
    _write(repo_root, "other.txt", "outside\n")
    return _make_execution_result("success", "conflict 解消")


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


class _FakeGitHubClient:
    """sync テスト用 GitHub client。"""

    def __init__(self, is_cross_repository: bool) -> None:
        self.pr = GitHubPullRequest(
            repository_full_name="org/repo",
            number=1,
            title="テスト PR",
            body="本文",
            state="OPEN",
            author=GitHubActor(login="Hiroshiba"),
            url="https://github.com/org/repo/pull/1",
            head_ref_name="feature",
            base_ref_name="main",
            head_repository_full_name="fork/repo" if is_cross_repository else "org/repo",
            is_cross_repository=is_cross_repository,
            maintainer_can_modify=True,
        )
        self.comments: list[tuple[str, int, str]] = []

    def get_pull_request(
        self,
        repository_full_name: str,
        number: int,
    ) -> GitHubPullRequest:
        """Pull Request を返す。"""
        return self.pr

    def get_pull_request_sync_state(
        self,
        repository_full_name: str,
        number: int,
    ) -> GitHubPullRequestSyncState:
        """Pull Request sync 状態を返す。"""
        return GitHubPullRequestSyncState(
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            status_check_summary=GitHubStatusCheckSummary(
                success_count=1,
                failure_count=0,
                pending_count=0,
                unknown_count=0,
            ),
        )

    def create_issue_comment(
        self,
        repository_full_name: str,
        number: int,
        body: str,
    ) -> None:
        """Issue comment を保存する。"""
        self.comments.append((repository_full_name, number, body))


def _make_github_client(is_cross_repository: bool) -> _FakeGitHubClient:
    """sync テスト用 GitHub client を生成する。"""
    return _FakeGitHubClient(is_cross_repository)


def _make_ready_execution() -> ReadyExecution:
    """sync テスト用 ReadyExecution を生成する。"""
    command = ResolvedCommand(
        event_name="local",
        command="sync",
        target_type="pr",
        target_number=1,
        has_target=True,
        target=ResolvedTarget(
            backend="github",
            kind="pr",
            canonical_id="github:org/repo#1",
            repository_full_name="org/repo",
            number=1,
            url="https://github.com/org/repo/pull/1",
        ),
    )
    return _make_ready_execution_with_command(command)


def _make_ready_execution_with_command(command: ResolvedCommand) -> ReadyExecution:
    """指定 command で ReadyExecution を生成する。"""
    provider = "codex"
    return ReadyExecution(
        command=command,
        config=VVAIConfig(allowed_users=["Hiroshiba"]),
        resolved_provider=ResolvedProvider(
            spec=get_provider_spec(provider),
            source="explicit",
        ),
        resolved_session=ResolvedSession(
            requested_mode="new",
            lane="main",
            key=SessionKey(
                backend="github",
                target_key="org/repo#1",
                provider=provider,
                lane="main",
                canonical_key=f"github/org/repo#1/{provider}/main",
            ),
            restore_strategy="new",
            save_manifest_path="/tmp/test-manifest.json",
        ),
        workflow_id="test-workflow",
    )


def _make_execution_result(
    status: ExecutionStatus,
    response_text: str | None,
) -> ExecutionResult:
    """sync テスト用 ExecutionResult を生成する。"""
    return ExecutionResult(
        status=status,
        report_sections=ReportSections(
            summary="summary",
            changes="changes",
            decisions="decisions",
            validation="validation",
            risks_open_questions="risks",
            next_actions="next",
            notes="notes",
        ),
        usage=MetricsUsage(),
        behavior=MetricsBehavior(),
        tools={},
        steps={},
        provider_specific=ProviderSpecificMetrics(),
        state_ref=SessionStateRef(),
        provider_session_path=None,
        allow_edits_notice_posted=False,
        response_text=response_text,
    )
