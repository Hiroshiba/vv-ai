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


def generate_implement_branch_name(issue_number: int) -> str:
    """Issue 番号から実装用ブランチ名を生成する。"""
    suffix = secrets.token_hex(4)
    return f"vv-ai/issue-{issue_number}-{suffix}"
