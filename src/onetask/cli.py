"""onetask の CLI エントリポイント。"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onetask.router import RouterError, run_task


def _find_repo_root(start: Path) -> Path:
    """git リポジトリのルートを探す。"""
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    raise RouterError(f"git リポジトリが見つかりません: {start}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI のメインエントリポイント。"""
    parser = argparse.ArgumentParser(prog="onetask")
    parser.add_argument("--repo-root", type=Path, help="リポジトリルートのパス")
    parser.add_argument("--max-review-loops", type=int, default=5)
    ns = parser.parse_args(argv)

    repo_root: Path = ns.repo_root or _find_repo_root(Path.cwd())

    try:
        run_task(repo_root, max_review_loops=ns.max_review_loops)
    except RouterError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0
