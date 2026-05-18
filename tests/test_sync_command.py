"""sync 用 Git helper の単体テスト。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vv_ai.git_ops import (
    GitOpsError,
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


def test_ensure_worktree_clean_rejects_dirty_worktree(tmp_path: Path) -> None:
    """ensure_worktree_clean は変更がある作業ツリーを拒否する。"""
    repo = _init_repo(tmp_path)
    _write(repo, "file.txt", "変更\n")

    with pytest.raises(GitOpsError, match="未コミット"):
        ensure_worktree_clean(repo)


def test_list_changed_and_staged_files_detects_worktree_state(tmp_path: Path) -> None:
    """list_changed_files と list_staged_files は変更状態を返す。"""
    repo = _init_repo(tmp_path)
    _write(repo, "tracked.txt", "変更\n")
    _write(repo, "untracked.txt", "追加\n")
    _run_git(repo, "add", "tracked.txt")

    assert list_changed_files(repo) == ["tracked.txt", "untracked.txt"]
    assert list_staged_files(repo) == ["tracked.txt"]


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
    _run_git(repo, "checkout", "-b", "incoming")
    _write(repo, "file.txt", "incoming\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-m", "incoming")
    _run_git(repo, "checkout", "main")
    _write(repo, "file.txt", "main\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-m", "main")

    attempt = merge_no_ff_no_commit(repo, "incoming")

    assert attempt.succeeded is False
    assert attempt.unmerged_files == ["file.txt"]
    assert list_unmerged_files(repo) == ["file.txt"]
    assert list_conflict_marker_files(repo, ["file.txt"]) == ["file.txt"]


def test_list_conflict_marker_files_ignores_resolved_content(tmp_path: Path) -> None:
    """list_conflict_marker_files は marker がないファイルを除外する。"""
    repo = _init_repo(tmp_path)
    _write(repo, "file.txt", "解決済み\n")

    assert list_conflict_marker_files(repo, ["file.txt"]) == []


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
