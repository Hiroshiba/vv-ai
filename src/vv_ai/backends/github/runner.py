"""GitHub CLI 実行関数。"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence

from vv_ai.backends.github.models import GitHubClientError

GhTextRunner = Callable[[Sequence[str]], str]
GhBinaryRunner = Callable[[Sequence[str]], bytes]


def run_gh_text(args: Sequence[str]) -> str:
    """`gh` を実行して標準出力を返す。"""
    return run_gh_text_with_env(args, os.environ)


def run_gh_text_with_env(args: Sequence[str], env: Mapping[str, str]) -> str:
    """指定 env で `gh` を実行して標準出力を返す。"""
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
    except OSError as exc:
        raise GitHubClientError(f"`gh` の実行に失敗しました: {exc}") from exc

    if completed.returncode == 0:
        return completed.stdout

    stderr = completed.stderr.strip()
    if stderr:
        raise GitHubClientError(f"`gh` の実行に失敗しました: {stderr}")
    raise GitHubClientError(
        f"`gh` の実行に失敗しました。終了コード: {completed.returncode}"
    )


def run_gh_binary(args: Sequence[str]) -> bytes:
    """`gh` を実行して標準出力 bytes を返す。"""
    return run_gh_binary_with_env(args, os.environ)


def run_gh_binary_with_env(args: Sequence[str], env: Mapping[str, str]) -> bytes:
    """指定 env で `gh` を実行して標準出力 bytes を返す。"""
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=False,
            env=env,
        )
    except OSError as exc:
        raise GitHubClientError(f"`gh` の実行に失敗しました: {exc}") from exc

    if completed.returncode == 0:
        return completed.stdout

    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if stderr:
        raise GitHubClientError(f"`gh` の実行に失敗しました: {stderr}")
    raise GitHubClientError(
        f"`gh` の実行に失敗しました。終了コード: {completed.returncode}"
    )
