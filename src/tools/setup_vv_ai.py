"""vv-ai の導入作業を案内して実行するツール。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass


def main() -> None:
    """vv-ai の導入作業を実行する。"""
    args = _parse_args()
    try:
        _setup_vv_ai(args.repo, args.dry_run, args.yes)
    except SetupVVAIError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


class SetupVVAIError(RuntimeError):
    """vv-ai 導入作業の失敗を表すエラー。"""


@dataclass(frozen=True)
class SetupStep:
    """vv-ai 導入作業の実行単位。"""

    prompt: str
    module_name: str


_SETUP_STEPS: tuple[SetupStep, ...] = (
    SetupStep(
        "GitHub ラベルを作成または更新しますか？",
        "tools.create_vv_ai_labels",
    ),
    SetupStep(
        "Codex 認証情報を GitHub Secret に設定しますか？",
        "tools.set_codex_auth_secret",
    ),
    SetupStep(
        "Claude 設定を GitHub Secret に設定しますか？",
        "tools.set_claude_settings_secret",
    ),
)


def _parse_args() -> argparse.Namespace:
    """コマンドライン引数を解釈する。"""
    parser = argparse.ArgumentParser(description="vv-ai の導入作業を案内して実行する")
    parser.add_argument(
        "--repo", help="対象リポジトリ 例: Hiroshiba/vv-ai。省略時は現在のリポジトリ。"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="変更を行わず、実行予定の確認だけを行う"
    )
    parser.add_argument(
        "--yes", action="store_true", help="すべての確認に yes と回答して実行する"
    )
    return parser.parse_args()


def _setup_vv_ai(repo: str | None, dry_run: bool, yes: bool) -> None:
    """vv-ai の導入作業を順に実行する。"""
    for step in _SETUP_STEPS:
        if _confirm_step(step.prompt, yes) is True:
            _run_step(step.module_name, repo, dry_run)
        else:
            print(f"スキップしました: {step.prompt}")


def _confirm_step(prompt: str, yes: bool) -> bool:
    """導入作業を実行するか確認する。"""
    if yes is True:
        return True

    answer = input(f"{prompt} [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    raise SetupVVAIError("回答は y または n で入力してください")


def _run_step(module_name: str, repo: str | None, dry_run: bool) -> None:
    """導入作業の個別ツールを実行する。"""
    cmd = [sys.executable, "-m", module_name, *_build_repo_args(repo)]
    if dry_run is True:
        cmd.append("--dry-run")

    try:
        result = subprocess.run(cmd)
    except OSError as e:
        raise SetupVVAIError(f"{module_name} の実行に失敗しました: {e}") from e

    if result.returncode != 0:
        raise SetupVVAIError(
            f"{module_name} が失敗しました exit {result.returncode}"
        )


def _build_repo_args(repo: str | None) -> list[str]:
    """個別ツールへ渡す repo 引数を構築する。"""
    if repo is None:
        return []
    return ["--repo", repo]


if __name__ == "__main__":
    main()
