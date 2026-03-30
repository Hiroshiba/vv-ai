"""codex exec の実行ラッパー。"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from onetask.provider import ProviderRunError, RunResult, wait_for_exitcode
from onetask.tmux import TmuxManager

T = TypeVar("T", bound=BaseModel)

_PLAN_FLAGS = ["--sandbox", "read-only", "-c", "model_reasoning_effort=high"]
_IMPL_FLAGS = ["--full-auto"]


class CodexRunner:
    """Codex CLI の実行ラッパー。"""

    def run(
        self,
        *,
        tmux: TmuxManager,
        window_name: str,
        prompt: str,
        json_schema: str,
        work_dir: Path,
        prefix: str,
        repo_root: Path,
        session_id: str | None,
        permission_mode: str,
        timeout: float,
        settings_file: Path | None,
    ) -> RunResult:
        """tmux ウィンドウ内で codex exec を実行し、結果を返す。"""
        if permission_mode == "plan":
            print(
                "警告: Codex には plan モードがないため --sandbox read-only で代替します。"
                " ファイル書き込みは物理的にブロックされます。"
            )

        schema_path = work_dir / f"{prefix}-schema.json"
        schema_path.write_text(json_schema)

        script_file = work_dir / f"{prefix}-script.sh"
        result_file = work_dir / f"{prefix}-result.txt"
        log_file = work_dir / f"{prefix}-log.jsonl"
        exitcode_file = work_dir / f"{prefix}-exitcode"

        result_file.unlink(missing_ok=True)
        exitcode_file.unlink(missing_ok=True)

        script = _build_script(
            prompt=prompt,
            schema_path=schema_path,
            result_file=result_file,
            log_file=log_file,
            exitcode_file=exitcode_file,
            repo_root=repo_root,
            session_id=session_id,
            permission_mode=permission_mode,
        )
        script_file.write_text(script)
        script_file.chmod(0o755)

        tmux.send_keys(window_name, f"bash {shlex.quote(str(script_file))}")

        return _wait_for_result(
            result_file=result_file,
            log_file=log_file,
            exitcode_file=exitcode_file,
            timeout=timeout,
        )

    def parse_structured_output(self, result: RunResult, model: type[T]) -> T:
        """RunResult の raw_result から structured output をパースする。"""
        try:
            data = json.loads(result.raw_result)
        except json.JSONDecodeError as exc:
            raise ProviderRunError(f"JSON パースに失敗: {exc}") from exc
        return model.model_validate(data)


def _build_script(
    *,
    prompt: str,
    schema_path: Path,
    result_file: Path,
    log_file: Path,
    exitcode_file: Path,
    repo_root: Path,
    session_id: str | None,
    permission_mode: str,
) -> str:
    """codex exec 実行用のシェルスクリプトを生成する。"""
    mode_flags = _PLAN_FLAGS if permission_mode == "plan" else _IMPL_FLAGS

    if session_id is not None:
        subcmd_parts = ["codex", "exec", "resume", shlex.quote(session_id)]
    else:
        subcmd_parts = ["codex", "exec"]

    cmd_parts = (
        subcmd_parts
        + mode_flags
        + [
            "--json",
            "-q",
            "-o",
            shlex.quote(str(result_file)),
            "--output-schema",
            shlex.quote(str(schema_path)),
            "--",
            shlex.quote(prompt),
        ]
    )

    cmd = " \\\n  ".join(cmd_parts)
    log_path = shlex.quote(str(log_file))
    exitcode_path = shlex.quote(str(exitcode_file))
    repo = shlex.quote(str(repo_root))

    return f"""#!/bin/bash
cd {repo} || exit 1
{cmd} \\
  | tee {log_path}
echo ${{PIPESTATUS[0]}} > {exitcode_path}
"""


def _extract_thread_id(log_file: Path) -> str | None:
    """JSONL ログから thread_id を抽出する。"""
    if not log_file.exists():
        return None
    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for key in ("thread_id", "session_id"):
            val = event.get(key)
            if val and isinstance(val, str):
                return val
    return None


def _wait_for_result(
    *,
    result_file: Path,
    log_file: Path,
    exitcode_file: Path,
    timeout: float,
) -> RunResult:
    """exitcode ファイルが出現するまでポーリングし、結果をパースする。"""
    exitcode = wait_for_exitcode(exitcode_file=exitcode_file, timeout=timeout)

    if exitcode != 0:
        log_snippet = log_file.read_text()[:500] if log_file.exists() else ""
        raise ProviderRunError(
            f"codex が終了コード {exitcode} で失敗\n{log_snippet}"
        )

    if not result_file.exists():
        raise ProviderRunError(f"結果ファイルが見つかりません (exitcode={exitcode})")

    raw_result = result_file.read_text().strip()
    thread_id = _extract_thread_id(log_file)

    if not thread_id:
        raise ProviderRunError("codex の出力から thread_id を取得できませんでした")

    return RunResult(
        session_id=thread_id,
        is_error=False,
        raw_result=raw_result,
    )
