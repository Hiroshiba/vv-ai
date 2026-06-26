"""vv-ai の導入作業を案内するツール。"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    """vv-ai の導入作業を案内する。"""
    _parse_args()
    try:
        _setup_vv_ai()
    except SetupVVAIError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


class SetupVVAIError(RuntimeError):
    """vv-ai 導入作業の失敗を表すエラー。"""


def _parse_args() -> argparse.Namespace:
    """コマンドライン引数を解釈する。"""
    parser = argparse.ArgumentParser(description="vv-ai の導入作業を案内する")
    return parser.parse_args()


def _setup_vv_ai() -> None:
    """vv-ai の導入作業を実行する。"""
    # TODO: Issue #271 で vv-ai 導入作業を実装する。
    raise SetupVVAIError("setup-vv-ai は未実装です。Issue #271 で実装します")


if __name__ == "__main__":
    main()
