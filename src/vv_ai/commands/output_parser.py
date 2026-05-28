"""AI 出力の解析処理。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vv_ai.value_types import validate_non_empty_string


@dataclass(frozen=True)
class ReviewThreadAction:
    """review thread への操作を表す。"""

    thread_id: str
    action: Literal["resolve", "comment"]
    body: str


@dataclass(frozen=True)
class AddressOutput:
    """address の AI 出力を表す。"""

    commit_message: str
    body: str
    review_thread_actions: list[ReviewThreadAction]


def parse_implement_issue_output(response_text: str) -> tuple[str, str, str]:
    """Issue 起点 implement の AI 出力から PR タイトル、コミットメッセージ、本文を抽出する。"""
    lines = response_text.split("\n")
    title = _parse_required_prefixed_line(lines, 0, "TITLE:", "`TITLE: <タイトル>`")
    commit_message = _parse_required_prefixed_line(
        lines,
        1,
        "COMMIT_MESSAGE:",
        "`COMMIT_MESSAGE: <コミットメッセージ>`",
    )
    _require_body_line(lines, 2)
    body = "\n".join(lines[3:])
    return title, commit_message, body


def parse_pr_change_output(response_text: str) -> tuple[str, str]:
    """PR 変更反映コマンドの AI 出力からコミットメッセージと本文を抽出する。"""
    lines = response_text.split("\n")
    commit_message = _parse_required_prefixed_line(
        lines,
        0,
        "COMMIT_MESSAGE:",
        "`COMMIT_MESSAGE: <コミットメッセージ>`",
    )
    _require_body_line(lines, 1)
    body = "\n".join(lines[2:])
    return commit_message, body


def parse_address_output(response_text: str, repo_root: Path) -> AddressOutput:
    """address の AI 出力からコミットメッセージ、本文、review thread 操作を抽出する。"""
    lines = response_text.split("\n")
    commit_message = _parse_required_prefixed_line(
        lines,
        0,
        "COMMIT_MESSAGE:",
        "`COMMIT_MESSAGE: <コミットメッセージ>`",
    )
    _require_body_line(lines, 1)

    actions_dir = _parse_final_review_thread_actions_dir(lines)
    if actions_dir is None:
        body = "\n".join(lines[2:])
        return AddressOutput(commit_message, body, [])

    body = "\n".join(lines[2 : actions_dir.line_index])
    review_thread_actions = parse_review_thread_actions_dir(actions_dir.path, repo_root)
    return AddressOutput(commit_message, body, review_thread_actions)


def parse_review_thread_actions_dir(
    actions_dir: Path,
    repo_root: Path,
) -> list[ReviewThreadAction]:
    """review thread 操作ディレクトリから操作一覧を抽出する。"""
    if not actions_dir.is_absolute():
        actions_dir = repo_root / actions_dir
    if actions_dir.is_symlink():
        raise RuntimeError(
            f"review thread 操作ディレクトリがシンボリックリンクです: {actions_dir}"
        )
    actions_dir = actions_dir.resolve()
    hiho_temp = (repo_root / "hiho_temp").resolve()
    if not actions_dir.is_relative_to(hiho_temp):
        raise RuntimeError(
            f"review thread 操作ディレクトリは hiho_temp 配下である必要があります: {actions_dir}"
        )
    if not actions_dir.is_dir():
        raise RuntimeError(
            f"review thread 操作ディレクトリが存在しません: {actions_dir}"
        )

    md_files = sorted(actions_dir.glob("*.md"))
    if len(md_files) == 0:
        raise RuntimeError(
            f"review thread 操作ディレクトリに .md ファイルがありません: {actions_dir}"
        )

    actions: list[ReviewThreadAction] = []
    thread_ids: set[str] = set()
    for md_file in md_files:
        if md_file.is_symlink():
            raise RuntimeError(
                f"review thread 操作ファイルがシンボリックリンクです: {md_file.name}"
            )
        action = _parse_single_review_thread_action_file(
            md_file.read_text(encoding="utf-8"),
            md_file.name,
        )
        if action.thread_id in thread_ids:
            raise RuntimeError(f"{md_file.name}: THREAD_ID が重複しています")
        thread_ids.add(action.thread_id)
        actions.append(action)
    return actions


def parse_title_body_output(response_text: str) -> tuple[str, str]:
    """AI の出力からタイトルと本文を抽出する。"""
    lines = response_text.split("\n")

    if len(lines) == 0 or not lines[0].startswith("TITLE:"):
        raise RuntimeError(
            "AI 出力が期待するフォーマットではありません。1行目は `TITLE: <タイトル>` である必要があります"
        )

    try:
        title = validate_non_empty_string(lines[0][len("TITLE:") :])
    except ValueError as exc:
        raise RuntimeError("AI 出力の TITLE が空です") from exc

    if len(lines) < 2 or lines[1].strip() != "BODY:":
        raise RuntimeError(
            "AI 出力が期待するフォーマットではありません。2行目は `BODY:` である必要があります"
        )

    body = "\n".join(lines[2:])
    return title, body


def parse_breakdown_dir(
    response_text: str,
    repo_root: Path,
) -> list[tuple[str, str]]:
    """AI の出力からタスクディレクトリを特定し、title と body のリストを返す。"""
    breakdown_dir: Path | None = None
    for line in response_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("BREAKDOWN_DIR:"):
            dir_path = stripped[len("BREAKDOWN_DIR:") :].strip()
            breakdown_dir = Path(dir_path)
            break
    if breakdown_dir is None:
        raise RuntimeError("AI 出力に BREAKDOWN_DIR: が含まれていません")
    if not breakdown_dir.is_absolute():
        breakdown_dir = repo_root / breakdown_dir
    breakdown_dir = breakdown_dir.resolve()
    hiho_temp = (repo_root / "hiho_temp").resolve()
    if not breakdown_dir.is_relative_to(hiho_temp):
        raise RuntimeError(
            f"タスクディレクトリは hiho_temp 配下である必要があります: {breakdown_dir}"
        )
    if not breakdown_dir.is_dir():
        raise RuntimeError(f"タスクディレクトリが存在しません: {breakdown_dir}")
    md_files = sorted(breakdown_dir.glob("*.md"))
    if len(md_files) == 0:
        raise RuntimeError(f"タスクディレクトリにファイルがありません: {breakdown_dir}")
    tasks = []
    for md_file in md_files:
        if md_file.is_symlink():
            raise RuntimeError(f"タスクファイルがシンボリックリンクです: {md_file.name}")
        content = md_file.read_text(encoding="utf-8")
        task = _parse_single_task_file(content, md_file.name)
        tasks.append(task)
    return tasks


@dataclass(frozen=True)
class _ReviewThreadActionsDir:
    line_index: int
    path: Path


def _parse_required_prefixed_line(
    lines: list[str],
    index: int,
    prefix: str,
    expected: str,
) -> str:
    if len(lines) <= index or not lines[index].startswith(prefix):
        raise RuntimeError(
            f"AI 出力が期待するフォーマットではありません。{index + 1}行目は {expected} である必要があります"
        )

    try:
        value = validate_non_empty_string(lines[index][len(prefix) :])
    except ValueError as exc:
        label = prefix.removesuffix(":")
        raise RuntimeError(f"AI 出力の {label} が空です") from exc
    return value


def _require_body_line(lines: list[str], index: int) -> None:
    if len(lines) <= index or lines[index].strip() != "BODY:":
        raise RuntimeError(
            f"AI 出力が期待するフォーマットではありません。{index + 1}行目は `BODY:` である必要があります"
        )


def _parse_final_review_thread_actions_dir(
    lines: list[str],
) -> _ReviewThreadActionsDir | None:
    last_non_empty_index: int | None = None
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip() != "":
            last_non_empty_index = index
            break
    if last_non_empty_index is None:
        return None

    line = lines[last_non_empty_index].strip()
    prefix = "REVIEW_THREAD_ACTIONS_DIR:"
    if not line.startswith(prefix):
        return None

    try:
        path_text = validate_non_empty_string(line[len(prefix) :])
    except ValueError as exc:
        raise RuntimeError("AI 出力の REVIEW_THREAD_ACTIONS_DIR が空です") from exc
    return _ReviewThreadActionsDir(last_non_empty_index, Path(path_text))


def _parse_single_review_thread_action_file(
    content: str,
    filename: str,
) -> ReviewThreadAction:
    lines = content.split("\n")
    thread_id = _parse_action_file_required_prefixed_line(
        lines,
        0,
        "THREAD_ID:",
        filename,
    )
    action_text = _parse_action_file_required_prefixed_line(
        lines,
        1,
        "ACTION:",
        filename,
    )
    if action_text == "resolve":
        action: Literal["resolve", "comment"] = "resolve"
    elif action_text == "comment":
        action = "comment"
    else:
        raise RuntimeError(
            f"{filename}: ACTION は resolve または comment である必要があります"
        )
    _require_action_file_body_line(lines, filename)
    body = "\n".join(lines[3:])
    if action == "comment" and body.strip() == "":
        raise RuntimeError(f"{filename}: ACTION が comment の場合は BODY が必要です")
    return ReviewThreadAction(thread_id, action, body)


def _parse_action_file_required_prefixed_line(
    lines: list[str],
    index: int,
    prefix: str,
    filename: str,
) -> str:
    if len(lines) <= index or not lines[index].startswith(prefix):
        raise RuntimeError(
            f"{filename}: {index + 1}行目は `{prefix} <値>` である必要があります"
        )
    try:
        return validate_non_empty_string(lines[index][len(prefix) :])
    except ValueError as exc:
        label = prefix.removesuffix(":")
        raise RuntimeError(f"{filename}: {label} が空です") from exc


def _require_action_file_body_line(lines: list[str], filename: str) -> None:
    if len(lines) <= 2 or lines[2].strip() != "BODY:":
        raise RuntimeError(f"{filename}: 3行目は `BODY:` である必要があります")


def _parse_single_task_file(content: str, filename: str) -> tuple[str, str]:
    lines = content.split("\n")
    non_empty = [ln for ln in lines if ln.strip() != ""]
    if len(non_empty) == 0:
        raise RuntimeError(f"タスクファイルが空です: {filename}")
    title_line = non_empty[0]
    if not title_line.startswith("TITLE:"):
        raise RuntimeError(
            f"{filename}: 先頭行は `TITLE: <タイトル>` である必要があります"
        )
    title = title_line[len("TITLE:") :].strip()
    if title == "":
        raise RuntimeError(f"{filename}: TITLE が空です")
    body_index = None
    for i, line in enumerate(lines):
        if line.strip() == "BODY:":
            body_index = i
            break
    if body_index is None:
        raise RuntimeError(f"{filename}: TITLE の後に `BODY:` が必要です")
    body = "\n".join(lines[body_index + 1 :])
    return title, body
