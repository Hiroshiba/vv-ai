"""provider 実行用環境変数処理。"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

_CODEX_OPENAI_API_KEY_ENV = "VV_OPENAI_API_KEY"
_CODEX_OPENAI_API_KEY_FILE_ENV = "VV_OPENAI_API_KEY_FILE"
_CODEX_HOME_ENV = "VV_CODEX_HOME"
_ANTHROPIC_API_KEY_ENV = "VV_ANTHROPIC_API_KEY"
_ANTHROPIC_API_KEY_FILE_ENV = "VV_ANTHROPIC_API_KEY_FILE"
_CLAUDE_EXTRA_SETTINGS_ENV = "VV_CLAUDE_SETTINGS"

_ALLOWED_ENV_KEYS = frozenset(
    [
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "XDG_RUNTIME_DIR",
        "TERM",
        "SHELL",
        "VV_GH_READONLY_TOKEN",
    ]
)


def build_codex_env(
    env: Mapping[str, str],
    skip_api_key_check: bool,
) -> dict[str, str]:
    """Codex プロセスに渡す環境変数を構築する。"""
    sanitized = build_sanitized_env(env)
    codex_home = resolve_codex_home(env)
    if codex_home is not None:
        sanitized["CODEX_HOME"] = str(codex_home)
    if skip_api_key_check:
        return sanitized

    api_key = try_resolve_api_key(
        env, _CODEX_OPENAI_API_KEY_FILE_ENV, _CODEX_OPENAI_API_KEY_ENV
    )
    if api_key is not None:
        sanitized["OPENAI_API_KEY"] = api_key
        return sanitized

    if codex_home is not None and (codex_home / "auth.json").is_file():
        return sanitized

    from vv_ai.providers.runner import ProviderExecutionError

    raise ProviderExecutionError(
        f"認証に必要な環境変数 `{_CODEX_OPENAI_API_KEY_FILE_ENV}` /"
        f" `{_CODEX_OPENAI_API_KEY_ENV}` / `{_CODEX_HOME_ENV}` のいずれも設定されていません"
    )


def resolve_codex_home(env: Mapping[str, str]) -> Path | None:
    """VV_CODEX_HOME から Codex home を返す。"""
    codex_home = env.get(_CODEX_HOME_ENV, "").strip()
    if codex_home == "":
        return None
    return Path(codex_home)


def resolve_codex_home_from_env(codex_env: Mapping[str, str]) -> Path:
    """Codex 実行環境から実際の Codex home を返す。"""
    codex_home = codex_env.get("CODEX_HOME", "").strip()
    if codex_home == "":
        return Path.home() / ".codex"
    return Path(codex_home)


def resolve_claude_api_key_file_path(
    env: Mapping[str, str],
) -> tuple[str, bool]:
    """Claude 用 API キーファイルパスと一時ファイルかどうかを返す。"""
    return resolve_api_key_file_path(
        env,
        _ANTHROPIC_API_KEY_FILE_ENV,
        _ANTHROPIC_API_KEY_ENV,
    )


def get_claude_extra_settings_json(env: Mapping[str, str]) -> str | None:
    """Claude 用の追加 settings JSON を返す。"""
    return env.get(_CLAUDE_EXTRA_SETTINGS_ENV)


def extract_mcp_domains() -> list[str]:
    """~/.claude.json の mcpServers URL からドメインを抽出する。"""
    claude_json_path = Path.home() / ".claude.json"
    if not claude_json_path.is_file():
        return []
    try:
        data = json.loads(claude_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"~/.claude.json の読み込みに失敗しました: {exc}", file=sys.stderr)
        return []
    mcp_servers = data.get("mcpServers", {})
    domains: list[str] = []
    for server in mcp_servers.values():
        url = server.get("url", "")
        if url != "":
            hostname = urlparse(url).hostname
            if hostname is not None:
                domains.append(hostname)
    return domains


def try_resolve_api_key(
    env: Mapping[str, str],
    file_env: str,
    value_env: str,
) -> str | None:
    """ファイルパス env 優先、生キー値 env フォールバックで API キーを返す。"""
    file_path = env.get(file_env, "").strip()
    if file_path:
        if not Path(file_path).is_file():
            return None
        content = Path(file_path).read_text(encoding="utf-8").strip()
        return content if content else None
    value = env.get(value_env, "").strip()
    return value if value else None


def resolve_api_key_file_path(
    env: Mapping[str, str],
    file_env: str,
    value_env: str,
) -> tuple[str, bool]:
    """API キーのファイルパスと一時ファイルかどうかを返す。"""
    file_path = env.get(file_env, "").strip()
    if file_path:
        if not Path(file_path).is_file():
            from vv_ai.providers.runner import ProviderExecutionError

            raise ProviderExecutionError(
                f"`{file_env}` で指定されたファイル `{file_path}` が見つかりません"
            )
        return file_path, False
    value = env.get(value_env, "").strip()
    if not value:
        from vv_ai.providers.runner import ProviderExecutionError

        raise ProviderExecutionError(
            f"認証に必要な環境変数 `{file_env}` または `{value_env}` が設定されていません"
        )
    tmp = tempfile.NamedTemporaryFile(
        prefix="vv-ai-key-", suffix=".txt", delete=False, mode="w"
    )
    tmp.write(value)
    tmp.close()
    Path(tmp.name).chmod(0o400)
    return tmp.name, True


def build_sanitized_env(env: Mapping[str, str]) -> dict[str, str]:
    """AI プロセスに渡す環境変数をホワイトリストで絞り込む。"""
    sanitized = {key: value for key, value in env.items() if key in _ALLOWED_ENV_KEYS}
    if "VV_GH_READONLY_TOKEN" in sanitized:
        sanitized["GH_TOKEN"] = sanitized.pop("VV_GH_READONLY_TOKEN")
    return sanitized
