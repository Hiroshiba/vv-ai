"""claude -p の実行ラッパー。"""
from __future__ import annotations

import json
import shlex
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from onetask.models import ClaudeResponse
from onetask.tmux import TmuxManager

_POLL_INTERVAL = 2.0

T = TypeVar("T", bound=BaseModel)


class ClaudeRunError(Exception):
    """claude CLI の実行に失敗した。"""


class ClaudeResult(BaseModel):
    """claude 実行結果。"""

    session_id: str
    is_error: bool
    raw_result: str


def run_claude(
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
) -> ClaudeResult:
    """tmux ウィンドウ内で claude -p を実行し、結果を返す。"""
    script_file = work_dir / f"{prefix}-script.sh"
    result_file = work_dir / f"{prefix}-result.json"
    exitcode_file = work_dir / f"{prefix}-exitcode"

    result_file.unlink(missing_ok=True)
    exitcode_file.unlink(missing_ok=True)

    script = _build_script(
        prompt=prompt,
        json_schema=json_schema,
        result_file=result_file,
        exitcode_file=exitcode_file,
        repo_root=repo_root,
        session_id=session_id,
        permission_mode=permission_mode,
        settings_file=settings_file,
    )
    script_file.write_text(script)
    script_file.chmod(0o755)

    tmux.send_keys(window_name, f"bash {shlex.quote(str(script_file))}")

    return _wait_for_result(
        result_file=result_file,
        exitcode_file=exitcode_file,
        timeout=timeout,
    )


def parse_structured_output(result: ClaudeResult, model: type[T]) -> T:
    """ClaudeResult の raw_result から structured output をパースする。"""
    try:
        envelope = json.loads(result.raw_result)
    except json.JSONDecodeError as exc:
        raise ClaudeRunError(f"JSON パースに失敗: {exc}") from exc

    if isinstance(envelope, dict) and "structured_output" in envelope:
        return model.model_validate(envelope["structured_output"])

    return model.model_validate(envelope)


def _build_script(
    *,
    prompt: str,
    json_schema: str,
    result_file: Path,
    exitcode_file: Path,
    repo_root: Path,
    session_id: str | None,
    permission_mode: str,
    settings_file: Path | None,
) -> str:
    """claude 実行用のシェルスクリプトを生成する。"""
    cmd_parts = [
        "claude",
        "-p",
        shlex.quote(prompt),
        "--verbose",
        "--permission-mode",
        shlex.quote(permission_mode),
        "--output-format",
        "stream-json",
        "--json-schema",
        shlex.quote(json_schema),
    ]
    if session_id is not None:
        cmd_parts += ["--resume", shlex.quote(session_id)]
    if settings_file is not None:
        cmd_parts += ["--settings", shlex.quote(str(settings_file))]

    cmd = " \\\n  ".join(cmd_parts)
    result_path = shlex.quote(str(result_file))
    exitcode_path = shlex.quote(str(exitcode_file))
    repo = shlex.quote(str(repo_root))

    return f"""#!/bin/bash
cd {repo} || exit 1
{cmd} \\
  | tee {result_path}
echo ${{PIPESTATUS[0]}} > {exitcode_path}
"""


def _find_result_event(content: str) -> str:
    """stream-json 出力から result イベント行を抽出する。"""
    for line in reversed(content.strip().split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return line
    raise ClaudeRunError("stream-json に result イベントが見つかりません")


def _wait_for_result(
    *,
    result_file: Path,
    exitcode_file: Path,
    timeout: float,
) -> ClaudeResult:
    """exitcode ファイルが出現するまでポーリングし、結果をパースする。"""
    deadline = time.monotonic() + timeout
    while not exitcode_file.exists():
        if time.monotonic() > deadline:
            raise ClaudeRunError(f"タイムアウト ({timeout}秒)")
        time.sleep(_POLL_INTERVAL)

    exitcode = int(exitcode_file.read_text().strip())

    if not result_file.exists():
        raise ClaudeRunError(f"結果ファイルが見つかりません (exitcode={exitcode})")

    raw = result_file.read_text()
    try:
        result_line = _find_result_event(raw)
    except ClaudeRunError:
        raise ClaudeRunError(
            f"claude が終了コード {exitcode} で失敗 (result イベントなし)\n{raw[:500]}"
        )

    response = ClaudeResponse.model_validate_json(result_line)
    if response.is_error:
        raise ClaudeRunError(f"claude がエラーを返しました: {response.result}")

    return ClaudeResult(
        session_id=response.session_id,
        is_error=response.is_error,
        raw_result=result_line,
    )
