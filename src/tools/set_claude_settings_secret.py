"""~/.claude/settings.json の env から VV_CLAUDE_SETTINGS を GitHub Secret に設定するツール。"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

_SECRET_ENV_KEYS = frozenset(["ANTHROPIC_AUTH_TOKEN"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="~/.claude/settings.json の env から VV_CLAUDE_SETTINGS を設定する"
    )
    parser.add_argument(
        "--repo", help="対象リポジトリ (例: Hiroshiba/vv-ai)。省略時は現在のリポジトリ。"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Secret の設定は行わない"
    )
    parser.add_argument(
        "--include-auth-token",
        action="store_true",
        help="ANTHROPIC_AUTH_TOKEN を VV_CLAUDE_SETTINGS に含める",
    )
    args = parser.parse_args()

    settings_path = _resolve_settings_path()
    print(f"settings.json: {settings_path}")

    try:
        env_dict = _load_env_from_settings(settings_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    filtered = _filter_env(env_dict, include_auth_token=args.include_auth_token)
    secret_value = json.dumps({"env": filtered})
    print(f"含めるキー: {list(filtered.keys())}")

    if args.dry_run:
        print("--dry-run: Secret の設定をスキップします")
        return

    try:
        _set_secret(args.repo, secret_value)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    repo_label = args.repo or "現在のリポジトリ"
    print(f"VV_CLAUDE_SETTINGS を {repo_label} に設定しました")


def _resolve_settings_path() -> Path:
    """~/.claude/settings.json のパスを返す。"""
    return Path.home() / ".claude" / "settings.json"


def _load_env_from_settings(path: Path) -> dict[str, str]:
    """settings.json から env フィールドを読み込む。"""
    if not path.exists():
        raise FileNotFoundError(f"settings.json が見つかりません: {path}")
    content = path.read_text(encoding="utf-8")
    try:
        settings = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"settings.json が有効な JSON ではありません: {e}") from e
    env = settings.get("env")
    if env is None:
        raise ValueError("settings.json に env フィールドがありません")
    return env


def _filter_env(env: dict[str, str], include_auth_token: bool) -> dict[str, str]:
    """CI に含める env キーだけを抽出する。"""
    filtered = {}
    for key, value in env.items():
        if not include_auth_token and key in _SECRET_ENV_KEYS:
            print(f"  秘匿キーを除外: {key}")
            continue
        filtered[key] = value
    return filtered


def _set_secret(repo: str | None, content: str) -> None:
    """gh secret set で VV_CLAUDE_SETTINGS を設定する。"""
    cmd = ["gh", "secret", "set", "VV_CLAUDE_SETTINGS"]
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
