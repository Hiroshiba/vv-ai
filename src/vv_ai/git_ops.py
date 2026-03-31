"""implement コマンド用の Git 操作ユーティリティ。"""

from __future__ import annotations

import secrets
import subprocess
from pathlib import Path


class GitOpsError(Exception):
    """Git 操作に失敗したことを表す例外。"""


def run_git_command(repo_root: Path, *args: str) -> str:
    """Git コマンドを実行して標準出力を返す。"""
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        raise GitOpsError(f"`{' '.join(command)}` の実行に失敗しました{detail}")
    return result.stdout


def create_and_checkout_branch(repo_root: Path, branch_name: str) -> None:
    """ブランチを作成してチェックアウトする。"""
    run_git_command(repo_root, "checkout", "-b", branch_name)


def fetch_and_checkout_branch(repo_root: Path, branch_name: str) -> None:
    """リモートブランチを fetch してチェックアウトする。"""
    run_git_command(repo_root, "fetch", "origin", branch_name)
    run_git_command(repo_root, "checkout", branch_name)


def push_branch(repo_root: Path, branch_name: str) -> None:
    """ブランチを origin へ push する。"""
    run_git_command(repo_root, "push", "-u", "origin", branch_name)


def get_default_branch(repo_root: Path) -> str:
    """リモートのデフォルトブランチ名を返す。"""
    ref = run_git_command(
        repo_root, "rev-parse", "--abbrev-ref", "origin/HEAD"
    ).strip()
    if ref.startswith("origin/"):
        return ref[len("origin/"):]
    if not ref:
        raise GitOpsError("origin/HEAD が未設定のためデフォルトブランチを取得できません")
    return ref


def get_head_sha(repo_root: Path) -> str:
    """HEAD の SHA を返す。"""
    sha = run_git_command(repo_root, "rev-parse", "HEAD").strip()
    if not sha:
        raise GitOpsError("HEAD SHA を取得できません")
    return sha


def checkout_fork_pr(
    repo_root: Path,
    repository_full_name: str,
    number: int,
) -> None:
    """gh pr checkout で fork PR のブランチをチェックアウトする。"""
    command = ["gh", "pr", "checkout", str(number), "--repo", repository_full_name]
    result = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        raise GitOpsError(f"fork PR のチェックアウトに失敗しました{detail}")


def try_push_current_branch(repo_root: Path) -> bool:
    """現在のブランチを upstream へ push する。成功なら True、失敗なら False。"""
    result = subprocess.run(
        ["git", "push"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def generate_patch(repo_root: Path, base_sha: str) -> str:
    """base_sha 以降のコミットから patch を生成する。"""
    return run_git_command(repo_root, "format-patch", "--stdout", f"{base_sha}..HEAD")


def generate_implement_branch_name(issue_id: str) -> str:
    """Issue 識別子から実装用ブランチ名を生成する。"""
    suffix = secrets.token_hex(4)
    return f"vv-ai/issue-{issue_id}-{suffix}"
