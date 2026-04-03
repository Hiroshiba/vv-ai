"""~/.codex/auth.json の内容を GitHub Secret VV_CODEX_AUTH_JSON に設定するツール。"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="~/.codex/auth.json を GitHub Secret VV_CODEX_AUTH_JSON に設定する"
    )
    parser.add_argument("--repo", help="対象リポジトリ (例: Hiroshiba/vv-ai)。省略時は現在のリポジトリ。")
    parser.add_argument("--dry-run", action="store_true", help="auth.json の読み込みのみ行い、Secret の設定は行わない")
    args = parser.parse_args()

    auth_json_path = _resolve_auth_json_path()
    print(f"auth.json: {auth_json_path}")

    try:
        content, parsed = _load_auth_json(auth_json_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"auth.json を読み込みました (キー: {list(parsed.keys())})")

    if args.dry_run:
        print("--dry-run: Secret の設定をスキップします")
        return

    try:
        _set_secret(args.repo, content)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    repo_label = args.repo or "現在のリポジトリ"
    print(f"VV_CODEX_AUTH_JSON を {repo_label} に設定しました")


def _resolve_auth_json_path() -> Path:
    """CODEX_HOME 環境変数または ~/.codex から auth.json のパスを解決する。"""
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _load_auth_json(path: Path) -> tuple[str, dict[str, object]]:
    """auth.json を読み込み、(生テキスト, パース済み dict) を返す。"""
    if not path.exists():
        raise FileNotFoundError(f"auth.json が見つかりません: {path}")
    content = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"auth.json が有効な JSON ではありません: {e}") from e
    return content, parsed


def _set_secret(repo: str | None, content: str) -> None:
    """gh secret set で VV_CODEX_AUTH_JSON を設定する。"""
    cmd = ["gh", "secret", "set", "VV_CODEX_AUTH_JSON"]
    if repo:
        cmd += ["--repo", repo]
    result = subprocess.run(
        cmd,
        input=content,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh secret set が失敗しました (exit {result.returncode}):\n{result.stderr}"
        )


if __name__ == "__main__":
    main()
