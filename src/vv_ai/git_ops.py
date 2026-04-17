"""implement コマンド用の Git 操作ユーティリティ。"""

from __future__ import annotations

import base64
import os
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


def _run_git_command_env(repo_root: Path, env: dict[str, str], *args: str) -> str:
    """環境変数を指定して git コマンドを実行して標準出力を返す。"""
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        raise GitOpsError(f"`{' '.join(command)}` の実行に失敗しました{detail}")
    return result.stdout


def _build_push_env(token: str) -> dict[str, str]:
    """push 用の HTTP 認証ヘッダーを git 設定として注入した環境変数を返す。"""
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {auth}",
    }


def create_and_checkout_branch(
    repo_root: Path, branch_name: str, start_point: str | None
) -> None:
    """ブランチを作成してチェックアウトする。"""
    if start_point is not None:
        run_git_command(repo_root, "checkout", "-b", branch_name, start_point)
    else:
        run_git_command(repo_root, "checkout", "-b", branch_name)


def _normalize_git_url(url: str) -> str:
    """Git URL を正規化する。末尾の .git を除去して小文字に変換する。"""
    return url.lower().removesuffix(".git")


def setup_upstream_remote(repo_root: Path, upstream_url: str) -> None:
    """upstream remote を追加する。既に同じ URL で存在する場合は何もしない。"""
    result = subprocess.run(
        ["git", "remote", "get-url", "upstream"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        existing_url = result.stdout.strip()
        if _normalize_git_url(existing_url) != _normalize_git_url(upstream_url):
            raise GitOpsError(
                f"upstream remote が既に別の URL で存在します: {existing_url}"
            )
        return
    run_git_command(repo_root, "remote", "add", "upstream", upstream_url)


def fetch_remote(repo_root: Path, remote: str) -> None:
    """指定リモートを fetch する。"""
    run_git_command(repo_root, "fetch", remote)


def fetch_and_checkout_branch(repo_root: Path, branch_name: str) -> None:
    """リモートブランチを fetch してチェックアウトする。"""
    run_git_command(repo_root, "fetch", "origin", branch_name)
    run_git_command(repo_root, "checkout", branch_name)


def push_branch(repo_root: Path, branch_name: str, token: str | None) -> None:
    """ブランチを origin へ push する。"""
    if token is not None:
        _run_git_command_env(repo_root, _build_push_env(token), "push", "-u", "origin", branch_name)
    else:
        run_git_command(repo_root, "push", "-u", "origin", branch_name)


# TODO: git add -A は AI が残した不要ファイルも含めてしまうリスクがある。本来は変更対象を絞りたい。
# TODO: Claude は .git への書き込みが制限されていないため、AI 自身がコミットする可能性がある。その場合ラッパーのコミットと二重になる。
def commit_all_changes(repo_root: Path, message: str) -> bool:
    """ワーキングツリーの全変更をコミットする。変更がなければ False を返す。"""
    status = run_git_command(repo_root, "status", "--porcelain").strip()
    if not status:
        return False
    run_git_command(repo_root, "add", "-A")
    run_git_command(repo_root, "commit", "-m", message)
    return True


def has_commits_ahead(repo_root: Path, base_ref: str) -> bool:
    """base_ref より HEAD が先行するコミットを持つか返す。"""
    log = run_git_command(repo_root, "log", "--oneline", f"{base_ref}..HEAD").strip()
    return len(log) > 0


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


def try_push_current_branch(repo_root: Path, token: str | None) -> bool:
    """現在のブランチを upstream へ push する。成功なら True、失敗なら False。"""
    env = _build_push_env(token) if token is not None else None
    result = subprocess.run(
        ["git", "push"],
        cwd=repo_root,
        env=env,
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
