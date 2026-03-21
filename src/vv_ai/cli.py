"""vv-ai の CLI エントリポイント。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """最小の CLI パーサーを構築する。"""
    return argparse.ArgumentParser(
        prog="vv-ai",
        description=(
            "GitHub Actions とローカル実行の両方に対応する "
            "vv-ai CLI の起動入口です。"
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI を起動し、終了コードを返す。"""
    parser = build_parser()
    parser.parse_args(argv)
    print("vv-ai CLI は初期化済みです。詳細なコマンド実装はこれから追加します。")
    return 0
