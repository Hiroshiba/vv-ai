"""vv-ai ラベル起動用の GitHub ラベルを作成するツール。"""

from __future__ import annotations

import argparse
import json
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
    LabelDefinition("vv-ai:reply", "5319e7", "vv-ai に返信を実行させる"),
    LabelDefinition("vv-ai:confirm", "5319e7", "vv-ai に意図確認を実行させる"),
    LabelDefinition("vv-ai:requirements", "5319e7", "vv-ai に要件定義を実行させる"),
    LabelDefinition("vv-ai:arch", "5319e7", "vv-ai に基本設計を実行させる"),
    LabelDefinition("vv-ai:detail", "5319e7", "vv-ai に詳細設計を実行させる"),
    LabelDefinition("vv-ai:breakdown", "5319e7", "vv-ai にタスク分割を実行させる"),
    LabelDefinition("vv-ai:implement", "5319e7", "vv-ai に実装を実行させる"),
    LabelDefinition("vv-ai:review", "5319e7", "vv-ai にレビューを実行させる"),
    LabelDefinition("vv-ai:issue", "5319e7", "vv-ai に Issue 作成を実行させる"),
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
    output = _run_gh(
        [
            "label",
            "list",
            "--limit",
            "1000",
            "--json",
            "name",
            *_build_repo_args(repo),
        ]
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as e:
        raise RuntimeError("gh label list の JSON 解析に失敗しました") from e
    if not isinstance(payload, list):
        raise RuntimeError("gh label list の結果が配列ではありません")

    label_names: set[str] = set()
    for raw_label in payload:
        if not isinstance(raw_label, dict):
            raise RuntimeError("gh label list のラベル形式が不正です")
        raw_name = raw_label.get("name")
        if not isinstance(raw_name, str):
            raise RuntimeError("gh label list のラベル名が不正です")
        label_names.add(raw_name)
    return label_names


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
