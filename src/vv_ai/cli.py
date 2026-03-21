"""vv-ai の CLI エントリポイント。"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from vv_ai.config import VVAIConfigError, find_repo_root
from vv_ai.input import CLIInput, InputError, build_raw_input_from_cli
from vv_ai.preflight import (
    PreflightError,
    ReadyExecution,
    SilentSkip,
    run_preflight,
)
from vv_ai.resolve import ResolutionError, resolve_raw_input


def build_parser() -> argparse.ArgumentParser:
    """最小の CLI パーサーを構築する。"""
    parser = argparse.ArgumentParser(
        prog="vv-ai",
        description=(
            "GitHub Actions とローカル実行の両方に対応する "
            "vv-ai CLI の起動入口です。"
        ),
    )
    parser.add_argument(
        "--event",
        choices=["issue_comment", "workflow_dispatch", "local"],
        default="local",
        help="入力の起動元を指定します。",
    )
    parser.add_argument(
        "--event-file",
        help="GitHub event payload JSON を読み込んで再現実行します。",
    )
    parser.add_argument(
        "--command",
        choices=["reply", "plan", "implement", "review", "issue"],
        help="実行コマンドを指定します。",
    )
    parser.add_argument("--instruction", help="自然言語の指示本文です。")
    parser.add_argument("--target-url", help="対象の Issue / PR URL またはローカルパスです。")
    parser.add_argument(
        "--target-type",
        choices=["issue", "pr"],
        help="対象種別を指定します。",
    )
    parser.add_argument(
        "--target-number",
        type=int,
        help="対象 Issue / PR の番号を指定します。",
    )
    parser.add_argument(
        "--provider",
        choices=["codex", "claude"],
        help="使用する AI プロバイダを指定します。",
    )
    parser.add_argument(
        "--session",
        choices=["inherit", "compact", "new"],
        help="セッション継続方式を指定します。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="GitHub への外部反映を行わずに実行します。",
    )
    parser.add_argument(
        "--repo",
        help="Issue 作成先のリポジトリを org/repo 形式で指定します。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI を起動し、終了コードを返す。"""
    parser = build_parser()
    namespace = parser.parse_args(argv)

    try:
        cli_input = CLIInput.model_validate(vars(namespace))
        raw_input = build_raw_input_from_cli(cli_input)
        resolved_command = resolve_raw_input(raw_input)
        repo_root = find_repo_root(Path.cwd())
        preflight_result = run_preflight(repo_root, resolved_command, os.environ)
    except (
        ValidationError,
        InputError,
        ResolutionError,
        VVAIConfigError,
        PreflightError,
    ) as exc:
        print(f"入力エラー: {exc}", file=sys.stderr)
        return 2

    if isinstance(preflight_result, SilentSkip):
        return _handle_silent_skip(preflight_result)

    print(_format_ready_message(preflight_result))
    return 0


def _handle_silent_skip(result: SilentSkip) -> int:
    """silent skip を処理する。"""
    if result.reason != "unauthorized_comment":
        raise AssertionError(f"未対応の silent skip 理由です: {result.reason}")
    return 0


def _format_ready_message(result: ReadyExecution) -> str:
    """preflight 成功時の確認用メッセージを組み立てる。"""
    return (
        "preflight 解決完了: "
        f"event={result.command.event_name}, "
        f"command={result.command.command}, "
        f"provider={result.provider}, "
        f"provider_source={result.provider_source}"
    )
