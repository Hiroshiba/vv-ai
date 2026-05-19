"""implement コマンド用の Git 操作ユーティリティ。"""

from __future__ import annotations

import base64
import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitOpsError(Exception):
    """Git 操作に失敗したことを表す例外。"""


@dataclass(frozen=True)
class MergeAttempt:
    """merge の試行結果を表す。"""

    succeeded: bool
    unmerged_files: list[str]
    stdout: str
    stderr: str


_MERGE_BASE_DEEPEN_DEPTHS = (32, 128, 512, 2048, 8192)


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


def _run_git_command_result(
    repo_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Git コマンドを実行して結果を返す。"""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )


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


def ensure_worktree_clean(repo_root: Path) -> None:
    """ワーキングツリーに変更がないことを検証する。"""
    status = run_git_command(repo_root, "status", "--porcelain").strip()
    if status != "":
        raise GitOpsError("ワーキングツリーに未コミットの変更があります")


def has_merge_base(repo_root: Path, left_ref: str, right_ref: str) -> bool:
    """left_ref と right_ref の共通祖先が存在するか返す。"""
    result = _run_git_command_result(repo_root, "merge-base", left_ref, right_ref)
    if result.returncode == 0:
        return result.stdout.strip() != ""
    if result.returncode == 1:
        return False
    stderr = result.stderr.strip()
    detail = f": {stderr}" if stderr else ""
    raise GitOpsError("共通祖先の判定に失敗しました" + detail)


def is_shallow_repository(repo_root: Path) -> bool:
    """repository が shallow clone か返す。"""
    result = run_git_command(repo_root, "rev-parse", "--is-shallow-repository").strip()
    if result == "true":
        return True
    if result == "false":
        return False
    raise GitOpsError("shallow repository の判定結果が不正です")


def ensure_merge_base_available(
    repo_root: Path,
    remote: str,
    pull_number: int,
    base_branch_name: str,
    base_ref: str,
) -> None:
    """HEAD と base_ref の共通祖先を判定できる履歴を取得する。"""
    if has_merge_base(repo_root, "HEAD", base_ref):
        return
    if not is_shallow_repository(repo_root):
        raise GitOpsError("PR branch と base branch の共通祖先が見つかりません")

    for depth in _MERGE_BASE_DEEPEN_DEPTHS:
        _fetch_pull_request_merge_base_history(
            repo_root,
            remote,
            pull_number,
            base_branch_name,
            depth,
        )
        if has_merge_base(repo_root, "HEAD", base_ref):
            return

    raise GitOpsError("PR branch と base branch の共通祖先を取得できませんでした")


def is_ancestor(repo_root: Path, ancestor_ref: str, descendant_ref: str) -> bool:
    """ancestor_ref が descendant_ref の祖先か返す。"""
    result = _run_git_command_result(
        repo_root,
        "merge-base",
        "--is-ancestor",
        ancestor_ref,
        descendant_ref,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    stderr = result.stderr.strip()
    detail = f": {stderr}" if stderr else ""
    raise GitOpsError("祖先関係の判定に失敗しました" + detail)


def merge_no_ff_no_commit(repo_root: Path, base_ref: str) -> MergeAttempt:
    """base_ref を no-ff かつ no-commit で merge する。"""
    result = _run_git_command_result(
        repo_root,
        "merge",
        "--no-ff",
        "--no-commit",
        base_ref,
    )
    if result.returncode == 0:
        return MergeAttempt(
            succeeded=True,
            unmerged_files=[],
            stdout=result.stdout,
            stderr=result.stderr,
        )
    unmerged_files = list_unmerged_files(repo_root)
    if len(unmerged_files) > 0:
        return MergeAttempt(
            succeeded=False,
            unmerged_files=unmerged_files,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    stderr = result.stderr.strip()
    detail = f": {stderr}" if stderr else ""
    raise GitOpsError(
        f"`git merge --no-ff --no-commit {base_ref}` の実行に失敗しました{detail}"
    )


def list_unmerged_files(repo_root: Path) -> list[str]:
    """未解消 conflict のファイル一覧を返す。"""
    return _split_git_path_nul(
        run_git_command(repo_root, "diff", "--name-only", "--diff-filter=U", "-z")
    )


def list_changed_files(repo_root: Path) -> list[str]:
    """ワーキングツリーで変更されたファイル一覧を返す。"""
    status = run_git_command(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    return sorted(_parse_porcelain_z_paths(status))


def list_staged_files(repo_root: Path) -> list[str]:
    """staged diff を持つファイル一覧を返す。"""
    return _split_git_path_nul(
        run_git_command(repo_root, "diff", "--cached", "--name-only", "-z")
    )


def get_staged_diff_signature(repo_root: Path) -> str:
    """staged diff の内容比較用 signature を返す。"""
    return run_git_command(repo_root, "diff", "--cached", "--raw", "-z")


def list_unstaged_files(repo_root: Path) -> list[str]:
    """unstaged diff を持つファイル一覧を返す。"""
    return _split_git_path_nul(run_git_command(repo_root, "diff", "--name-only", "-z"))


def list_conflict_marker_files(repo_root: Path, paths: list[str]) -> list[str]:
    """conflict marker を含むファイル一覧を返す。"""
    marker_files: list[str] = []
    for path in paths:
        file_path = repo_root / path
        if (
            file_path.exists()
            and file_path.is_file()
            and _has_conflict_markers(file_path)
        ):
            marker_files.append(path)
    return marker_files


def commit_merge_no_edit(repo_root: Path) -> str:
    """merge commit を既定メッセージで作成して SHA を返す。"""
    run_git_command(repo_root, "commit", "--no-edit")
    return get_head_sha(repo_root)


def push_current_branch(repo_root: Path, token: str | None) -> None:
    """現在のブランチを upstream へ push する。"""
    if token is not None:
        _run_git_command_env(repo_root, _build_push_env(token), "push")
        return
    run_git_command(repo_root, "push")


def generate_diff_patch(repo_root: Path, base_sha: str) -> str:
    """base_sha から HEAD までの diff patch を生成する。"""
    return run_git_command(repo_root, "diff", "--binary", f"{base_sha}..HEAD")


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


def fetch_remote_branch(repo_root: Path, remote: str, branch_name: str) -> None:
    """指定リモートブランチを remote-tracking ref へ fetch する。"""
    run_git_command(
        repo_root,
        "fetch",
        remote,
        f"+refs/heads/{branch_name}:refs/remotes/{remote}/{branch_name}",
    )


def _fetch_pull_request_merge_base_history(
    repo_root: Path,
    remote: str,
    pull_number: int,
    base_branch_name: str,
    depth: int,
) -> None:
    """Pull Request と base branch の履歴を指定 commit 数だけ深く fetch する。"""
    run_git_command(
        repo_root,
        "fetch",
        f"--deepen={depth}",
        remote,
        (
            f"+refs/pull/{pull_number}/head:"
            f"refs/remotes/{remote}/pull/{pull_number}/head"
        ),
        f"+refs/heads/{base_branch_name}:refs/remotes/{remote}/{base_branch_name}",
    )


def checkout_ref(repo_root: Path, ref_name: str) -> None:
    """指定 ref をチェックアウトする。"""
    run_git_command(repo_root, "checkout", ref_name)


def fetch_and_checkout_branch(repo_root: Path, branch_name: str) -> None:
    """リモートブランチを fetch してチェックアウトする。"""
    fetch_remote_branch(repo_root, "origin", branch_name)
    run_git_command(repo_root, "checkout", "-B", branch_name, f"origin/{branch_name}")


def push_branch(repo_root: Path, branch_name: str, token: str | None) -> None:
    """ブランチを origin へ push する。"""
    if token is not None:
        _run_git_command_env(
            repo_root,
            _build_push_env(token),
            "push",
            "-u",
            "origin",
            branch_name,
        )
    else:
        run_git_command(repo_root, "push", "-u", "origin", branch_name)


def stage_paths(repo_root: Path, paths: list[str]) -> None:
    """指定 path だけを stage する。"""
    if len(paths) == 0:
        raise GitOpsError("stage 対象の path が指定されていません")
    run_git_command(repo_root, "add", "-A", "--", *paths)


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


def _split_git_path_nul(output: str) -> list[str]:
    """NUL 区切りの Git path 出力を一覧へ変換する。"""
    return [path for path in output.split("\0") if path != ""]


def _parse_porcelain_z_paths(output: str) -> set[str]:
    """NUL 区切りの porcelain status から path 一覧を返す。"""
    records = _split_git_path_nul(output)
    paths: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4:
            raise GitOpsError("git status の出力形式が不正です")
        status = record[:2]
        paths.add(record[3:])
        if "R" in status or "C" in status:
            if index + 1 >= len(records):
                raise GitOpsError("git status の rename 出力形式が不正です")
            index += 2
        else:
            index += 1
    return paths


def _has_conflict_markers(file_path: Path) -> bool:
    """ファイルが conflict marker 一式を含むか返す。"""
    marker_lines = set()
    for line in file_path.read_bytes().splitlines():
        if line.startswith(b"<<<<<<< "):
            marker_lines.add("start")
        elif line == b"=======":
            marker_lines.add("middle")
        elif line.startswith(b">>>>>>> "):
            marker_lines.add("end")
    return marker_lines == {"start", "middle", "end"}
