"""provider session の収集と復元。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from vv_ai.providers.runner import ProviderExecutionError


def resolve_claude_session_dir(repo_root: Path, session_id: str) -> Path | None:
    """Claude Code のセッションファイルを一時ディレクトリに集めて返す。"""
    sanitized = str(repo_root).replace("/", "-")
    project_dir = Path.home() / ".claude" / "projects" / sanitized
    if not project_dir.is_dir():
        return None

    session_jsonl = project_dir / f"{session_id}.jsonl"
    if not session_jsonl.is_file():
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="vv-ai-claude-session-"))
    try:
        shutil.copy2(session_jsonl, tmp_dir / session_jsonl.name)

        session_subdir = project_dir / session_id
        if session_subdir.is_dir():
            shutil.copytree(session_subdir, tmp_dir / session_id)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return tmp_dir


def resolve_codex_session_dir(codex_env: dict[str, str]) -> Path:
    """Codex のセッションファイルを一時ディレクトリに集めて返す。"""
    codex_home = Path(codex_env.get("CODEX_HOME", str(Path.home() / ".codex")))
    if not codex_home.is_dir():
        raise ProviderExecutionError(
            f"`{codex_home}` は Codex home directory ではありません"
        )

    sessions_dir = codex_home / "sessions"
    if not sessions_dir.is_dir():
        raise ProviderExecutionError(
            f"`{sessions_dir}` は Codex session directory ではありません"
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="vv-ai-codex-session-"))
    try:
        shutil.copytree(sessions_dir, tmp_dir / "sessions")
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise ProviderExecutionError(
            f"`{sessions_dir}` の収集に失敗しました"
        ) from exc

    return tmp_dir


def deploy_codex_session_dir(source: str, codex_home: Path) -> None:
    """復元された Codex session directory を CODEX_HOME へコピーする。"""
    source_sessions_dir = Path(source) / "sessions"
    if not source_sessions_dir.is_dir():
        raise ProviderExecutionError(
            f"`{source_sessions_dir}` は Codex session directory ではありません"
        )

    codex_home.mkdir(parents=True, exist_ok=True)
    destination_sessions_dir = codex_home / "sessions"
    try:
        if destination_sessions_dir.exists():
            if not destination_sessions_dir.is_dir():
                raise ProviderExecutionError(
                    f"`{destination_sessions_dir}` は directory ではありません"
                )
            shutil.rmtree(destination_sessions_dir)
        shutil.copytree(source_sessions_dir, destination_sessions_dir)
    except ProviderExecutionError:
        raise
    except Exception as exc:
        raise ProviderExecutionError(
            f"`{destination_sessions_dir}` への Codex session 復元に失敗しました"
        ) from exc


def deploy_provider_session_dir(source: str, destination: Path) -> None:
    """復元されたセッションファイルを provider が期待する場所にコピーする。"""
    source_path = Path(source)
    destination.mkdir(parents=True, exist_ok=True)
    for item in source_path.iterdir():
        dest_item = destination / item.name
        if item.is_dir():
            if dest_item.exists():
                shutil.rmtree(dest_item)
            shutil.copytree(item, dest_item)
        else:
            shutil.copy2(item, dest_item)
