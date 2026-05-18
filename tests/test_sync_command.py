"""sync 用 Git helper の単体テスト。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
    list_unmerged_files,
    merge_no_ff_no_commit,
    stage_paths,
)


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
