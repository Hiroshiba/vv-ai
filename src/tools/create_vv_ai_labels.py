"""vv-ai ラベル起動用の GitHub ラベルを作成するツール。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass


def main() -> None:
    """vv-ai ラベル起動用の GitHub ラベルを同期する。"""
    args = _parse_args()
    try:
        _sync_labels(args.repo, args.dry_run)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


@dataclass(frozen=True)
class LabelDefinition:
    """vv-ai ラベル起動用 GitHub ラベルの定義。"""

    name: str
    color: str
    description: str


VV_AI_LABELS: tuple[LabelDefinition, ...] = (
    LabelDefinition("vv-ai:reply", "5319e7", "vv-ai で返信する"),
    LabelDefinition("vv-ai:confirm", "5319e7", "vv-ai で意図確認する"),
    LabelDefinition("vv-ai:requirements", "5319e7", "vv-ai で要件定義する"),
    LabelDefinition("vv-ai:arch", "5319e7", "vv-ai で基本設計する"),
    LabelDefinition("vv-ai:detail", "5319e7", "vv-ai で詳細設計する"),
    LabelDefinition("vv-ai:breakdown", "5319e7", "vv-ai でタスク分割する"),
    LabelDefinition("vv-ai:implement", "5319e7", "vv-ai で実装する"),
    LabelDefinition("vv-ai:address", "5319e7", "vv-ai でレビュー指摘対応する"),
    LabelDefinition("vv-ai:review", "5319e7", "vv-ai でレビューする"),
    LabelDefinition("vv-ai:issue", "5319e7", "vv-ai で Issue 作成する"),
    LabelDefinition("vv-ai:next", "5319e7", "vv-ai で次の工程を実行する"),
    LabelDefinition("vv-ai:sync", "5319e7", "vv-ai で PR を同期する"),
)


def _parse_args() -> argparse.Namespace:
    """コマンドライン引数を解釈する。"""
    parser = argparse.ArgumentParser(
        description="vv-ai ラベル起動用の GitHub ラベルを作成または更新する"
    )
    parser.add_argument(
        "--repo", help="対象リポジトリ 例: Hiroshiba/vv-ai。省略時は現在のリポジトリ。"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="GitHub ラベルの変更は行わない"
    )
    return parser.parse_args()


def _sync_labels(repo: str | None, dry_run: bool) -> None:
    """vv-ai ラベル起動用 GitHub ラベルを同期する。"""
    existing_label_names = _list_existing_label_names(repo)

    for label in VV_AI_LABELS:
        if label.name in existing_label_names:
            if dry_run is True:
                print(f"更新予定: {label.name}")
            else:
                _edit_label(repo, label)
                print(f"更新しました: {label.name}")
        else:
            if dry_run is True:
                print(f"作成予定: {label.name}")
            else:
                _create_label(repo, label)
                print(f"作成しました: {label.name}")

    repo_label = repo if repo is not None else "現在のリポジトリ"
    if dry_run is True:
        print(f"--dry-run: GitHub ラベルは変更していません。対象: {repo_label}")
    else:
        print(f"{repo_label} の vv-ai ラベルを同期しました")


def _list_existing_label_names(repo: str | None) -> set[str]:
    """GitHub リポジトリの既存ラベル名を取得する。"""
    repository_full_name = _resolve_repository_full_name(repo)
    output = _run_gh(
        [
            "api",
            f"repos/{repository_full_name}/labels?per_page=100",
            "--paginate",
            "--jq",
            ".[].name",
        ]
    )
    return set(output.splitlines())


def _resolve_repository_full_name(repo: str | None) -> str:
    """gh api に渡す GitHub リポジトリ名を解決する。"""
    if repo is not None:
        return repo
    repository_full_name = _run_gh(
        ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
    ).strip()
    if repository_full_name == "":
        raise RuntimeError("現在のリポジトリ名を解決できませんでした")
    return repository_full_name


def _create_label(repo: str | None, label: LabelDefinition) -> None:
    """GitHub ラベルを作成する。"""
    _run_gh(
        [
            "label",
            "create",
            label.name,
            "--color",
            label.color,
            "--description",
            label.description,
            *_build_repo_args(repo),
        ]
    )


def _edit_label(repo: str | None, label: LabelDefinition) -> None:
    """GitHub ラベルを更新する。"""
    _run_gh(
        [
            "label",
            "edit",
            label.name,
            "--color",
            label.color,
            "--description",
            label.description,
            *_build_repo_args(repo),
        ]
    )


def _run_gh(args: Sequence[str]) -> str:
    """gh コマンドを実行し、標準出力を返す。"""
    cmd = ["gh", *args]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
        )
    except OSError as e:
        raise RuntimeError(f"gh コマンドの実行に失敗しました: {e}") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"gh コマンドが失敗しました exit {result.returncode}:\n{result.stderr}"
        )
    return result.stdout


def _build_repo_args(repo: str | None) -> list[str]:
    """gh コマンドの repo 引数を構築する。"""
    if repo is None:
        return []
    return ["--repo", repo]


if __name__ == "__main__":
    main()
