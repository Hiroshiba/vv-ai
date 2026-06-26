"""vv-ai 用の age 鍵を生成するツール。"""

from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> None:
    """vv-ai 用の age 鍵を生成する。"""
    _parse_args()
    try:
        output = _generate_age_key()
    except GenerateVVAIKeyError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(output)


class GenerateVVAIKeyError(RuntimeError):
    """vv-ai 用 age 鍵生成の失敗を表すエラー。"""


def _parse_args() -> argparse.Namespace:
    """コマンドライン引数を解釈する。"""
    parser = argparse.ArgumentParser(description="vv-ai 用の age 鍵を生成する")
    return parser.parse_args()


def _generate_age_key() -> str:
    """age-keygen を実行し、標準出力を返す。"""
    try:
        result = subprocess.run(
            ["age-keygen"],
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as e:
        raise GenerateVVAIKeyError("age-keygen が見つかりません") from e
    except OSError as e:
        raise GenerateVVAIKeyError(f"age-keygen の実行に失敗しました: {e}") from e

    if result.returncode != 0:
        raise GenerateVVAIKeyError(
            f"age-keygen が失敗しました exit {result.returncode}:\n{result.stderr}"
        )
    return result.stdout


if __name__ == "__main__":
    main()
