"""onetask のメインルーターフロー。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from onetask.claude import (
    ClaudeRunError,
    parse_structured_output,
    run_claude,
)
from onetask.models import (
    ImplementerFinalResult,
    ImplementerPlanResult,
    ImplementerTaskResult,
    ImplementerTriageResult,
    ReviewerResult,
    schema_json,
)
from onetask.tmux import TmuxManager

_SESSION_NAME = "onetask"
_IMPLEMENTER_TIMEOUT = 1200.0
_REVIEWER_TIMEOUT = 600.0


class RouterError(Exception):
    """ルーター処理の失敗。"""


def run_task(repo_root: Path, *, max_review_loops: int) -> None:
    """team-task.md のフローを Python で再現する。"""
    work_dir = Path(tempfile.mkdtemp(prefix=f"onetask-{os.getpid()}-"))
    print(f"ワークディレクトリ: {work_dir}")

    tmux = TmuxManager(_SESSION_NAME)
    if tmux.session_exists():
        raise RouterError(
            f"tmux セッション '{_SESSION_NAME}' が既に存在します。"
            " 既存セッションを終了してから再実行してください"
        )

    tmux.create_session()
    print(f"tmux セッション '{_SESSION_NAME}' を作成しました")
    print(f"監視: tmux attach -t {_SESSION_NAME}")

    try:
        _run_flow(
            tmux=tmux,
            work_dir=work_dir,
            repo_root=repo_root,
            max_review_loops=max_review_loops,
        )
    except ClaudeRunError as exc:
        raise RouterError(str(exc)) from exc
    finally:
        print(f"tmux セッション '{_SESSION_NAME}' は残しています。不要なら: tmux kill-session -t {_SESSION_NAME}")


def _run_flow(
    *,
    tmux: TmuxManager,
    work_dir: Path,
    repo_root: Path,
    max_review_loops: int,
) -> None:
    """ルーターのメインフロー。"""
    settings_file = work_dir / "sandbox-settings.json"
    settings_file.write_text(
        json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}})
    )

    implementer_prompt = _build_implementer_prompt(repo_root)
    impl_session_id = _run_implementer_plan(
        tmux=tmux,
        work_dir=work_dir,
        repo_root=repo_root,
        prompt=implementer_prompt,
        settings_file=settings_file,
    )
    _run_implementer_implement(
        tmux=tmux,
        work_dir=work_dir,
        repo_root=repo_root,
        session_id=impl_session_id,
        settings_file=settings_file,
    )

    review_count = 0
    latest_review_file = ""
    while True:
        review_count += 1
        latest_review_file = _run_reviewer(
            tmux=tmux,
            work_dir=work_dir,
            repo_root=repo_root,
            review_number=review_count,
            settings_file=settings_file,
        )

        changes_made = _run_implementer_triage(
            tmux=tmux,
            work_dir=work_dir,
            repo_root=repo_root,
            session_id=impl_session_id,
            review_file_path=latest_review_file,
            review_number=review_count,
            settings_file=settings_file,
        )

        if not changes_made:
            break
        if review_count >= max_review_loops:
            print(f"レビューループ上限 ({max_review_loops}) に達したため次に進みます")
            break

    while True:
        user_feedback = _user_confirmation(latest_review_file)
        if user_feedback is None:
            print("中断しました")
            return
        if user_feedback == "":
            break

        _run_implementer_fix(
            tmux=tmux,
            work_dir=work_dir,
            repo_root=repo_root,
            session_id=impl_session_id,
            feedback=user_feedback,
            fix_number=review_count,
            settings_file=settings_file,
        )
        review_count += 1
        latest_review_file = _run_reviewer(
            tmux=tmux,
            work_dir=work_dir,
            repo_root=repo_root,
            review_number=review_count,
            settings_file=settings_file,
        )

        changes_made = _run_implementer_triage(
            tmux=tmux,
            work_dir=work_dir,
            repo_root=repo_root,
            session_id=impl_session_id,
            review_file_path=latest_review_file,
            review_number=review_count,
            settings_file=settings_file,
        )

        if not changes_made:
            continue
        if review_count >= max_review_loops:
            print(f"レビューループ上限 ({max_review_loops}) に達したため次に進みます")
            break

    _run_implementer_final(
        tmux=tmux,
        work_dir=work_dir,
        repo_root=repo_root,
        session_id=impl_session_id,
        settings_file=settings_file,
    )

    print("タスク完了")


def _strip_frontmatter(content: str) -> str:
    """YAML frontmatter を除去して本文を返す。"""
    lines = content.split("\n")
    body_lines: list[str] = []
    frontmatter_count = 0
    in_frontmatter = False
    for line in lines:
        if line.strip() == "---":
            frontmatter_count += 1
            if frontmatter_count == 1:
                in_frontmatter = True
                continue
            if frontmatter_count == 2:
                in_frontmatter = False
                continue
        if not in_frontmatter and frontmatter_count >= 2:
            body_lines.append(line)
    return "\n".join(body_lines).strip()


def _build_implementer_prompt(repo_root: Path) -> str:
    """implementer のプラン作成プロンプトを組み立てる。"""
    agent_file = repo_root / ".claude" / "agents" / "implementer.md"

    parts: list[str] = []

    if agent_file.exists():
        parts.append(_strip_frontmatter(agent_file.read_text()))

    parts.append(
        "タスクのプランを作成してください。"
        " プラン完了後、結果を JSON で返してください。"
        " status, summary, message の3フィールドです。"
    )

    return "\n\n".join(parts)


def _build_reviewer_prompt(repo_root: Path) -> str:
    """reviewer のプロンプトを組み立てる。"""
    agent_file = repo_root / ".claude" / "agents" / "reviewer.md"

    parts: list[str] = []

    if agent_file.exists():
        parts.append(_strip_frontmatter(agent_file.read_text()))

    parts.append(
        "review-diff スキルを実行してください。"
        " 結果を /tmp/review-<ランダム文字列>.md に保存してください。"
        " 完了後、結果を JSON で返してください。"
        " status, review_file_path, message の3フィールドです。"
    )

    return "\n\n".join(parts)


def _run_implementer_plan(
    *,
    tmux: TmuxManager,
    work_dir: Path,
    repo_root: Path,
    prompt: str,
    settings_file: Path,
) -> str:
    """implementer のプランフェーズを実行し、session_id を返す。"""
    tmux.create_window("implementer")
    print("implementer プラン作成中...")

    result = run_claude(
        tmux=tmux,
        window_name="implementer",
        prompt=prompt,
        json_schema=schema_json(ImplementerPlanResult),
        work_dir=work_dir,
        prefix="impl-plan",
        repo_root=repo_root,
        session_id=None,
        permission_mode="plan",
        timeout=_IMPLEMENTER_TIMEOUT,
        settings_file=settings_file,
    )

    parsed = parse_structured_output(result, ImplementerPlanResult)
    print(f"implementer プラン完了: {parsed.summary}")

    if parsed.status == "error":
        raise ClaudeRunError(f"implementer プランエラー: {parsed.message}")

    return result.session_id


def _run_implementer_implement(
    *,
    tmux: TmuxManager,
    work_dir: Path,
    repo_root: Path,
    session_id: str,
    settings_file: Path,
) -> None:
    """implementer の実装フェーズを実行する。"""
    prompt = (
        "プランは承認されました。タスクを実行してください。"
        " 完了後、結果を JSON で返してください。"
        " status, changes_made, summary, message の4フィールドです。"
    )

    print("implementer 実装中...")

    result = run_claude(
        tmux=tmux,
        window_name="implementer",
        prompt=prompt,
        json_schema=schema_json(ImplementerTaskResult),
        work_dir=work_dir,
        prefix="impl-task",
        repo_root=repo_root,
        session_id=session_id,
        permission_mode="acceptEdits",
        timeout=_IMPLEMENTER_TIMEOUT,
        settings_file=settings_file,
    )

    parsed = parse_structured_output(result, ImplementerTaskResult)
    print(f"implementer 実装完了: {parsed.summary}")

    if parsed.status == "error":
        raise ClaudeRunError(f"implementer 実装エラー: {parsed.message}")


def _run_reviewer(
    *,
    tmux: TmuxManager,
    work_dir: Path,
    repo_root: Path,
    review_number: int,
    settings_file: Path,
) -> str:
    """reviewer を起動し、レビュー結果ファイルパスを返す。"""
    window_name = f"reviewer-{review_number}"
    tmux.create_window(window_name)
    print(f"reviewer-{review_number} を起動しています...")

    prompt = _build_reviewer_prompt(repo_root)
    result = run_claude(
        tmux=tmux,
        window_name=window_name,
        prompt=prompt,
        json_schema=schema_json(ReviewerResult),
        work_dir=work_dir,
        prefix=f"rev-{review_number}",
        repo_root=repo_root,
        session_id=None,
        permission_mode="acceptEdits",
        timeout=_REVIEWER_TIMEOUT,
        settings_file=settings_file,
    )

    parsed = parse_structured_output(result, ReviewerResult)
    print(f"reviewer-{review_number} 完了: {parsed.message}")

    if parsed.status == "error":
        raise ClaudeRunError(f"reviewer エラー: {parsed.message}")

    return parsed.review_file_path


def _run_implementer_triage(
    *,
    tmux: TmuxManager,
    work_dir: Path,
    repo_root: Path,
    session_id: str,
    review_file_path: str,
    review_number: int,
    settings_file: Path,
) -> bool:
    """implementer にレビュー結果を渡して triage を実行し、changes_made を返す。"""
    prompt = (
        f"レビュー結果ファイル {review_file_path} を Read で確認し、"
        " review-triage スキルを実行してください。"
        " 修正するかどうかはあなたが判断してください。"
        " 完了後、結果を JSON で返してください。"
        " status, changes_made, message の3フィールドです。"
    )

    print("implementer に triage を指示しています...")

    result = run_claude(
        tmux=tmux,
        window_name="implementer",
        prompt=prompt,
        json_schema=schema_json(ImplementerTriageResult),
        work_dir=work_dir,
        prefix=f"impl-triage-{review_number}",
        repo_root=repo_root,
        session_id=session_id,
        permission_mode="acceptEdits",
        timeout=_IMPLEMENTER_TIMEOUT,
        settings_file=settings_file,
    )

    parsed = parse_structured_output(result, ImplementerTriageResult)
    print(f"implementer triage 完了: changes_made={parsed.changes_made}")

    if parsed.status == "error":
        raise ClaudeRunError(f"implementer triage エラー: {parsed.message}")

    return parsed.changes_made


def _run_implementer_fix(
    *,
    tmux: TmuxManager,
    work_dir: Path,
    repo_root: Path,
    session_id: str,
    feedback: str,
    fix_number: int,
    settings_file: Path,
) -> None:
    """ユーザーのフィードバックに基づいて implementer に追加修正を指示する。"""
    prompt = (
        f"ユーザーから以下のフィードバックがありました:\n{feedback}\n\n"
        "上記の問題を修正してください。"
        " 完了後、結果を JSON で返してください。"
        " status, changes_made, summary, message の4フィールドです。"
    )

    print("implementer にユーザーフィードバックの修正を指示しています...")

    result = run_claude(
        tmux=tmux,
        window_name="implementer",
        prompt=prompt,
        json_schema=schema_json(ImplementerTaskResult),
        work_dir=work_dir,
        prefix=f"impl-fix-{fix_number}",
        repo_root=repo_root,
        session_id=session_id,
        permission_mode="acceptEdits",
        timeout=_IMPLEMENTER_TIMEOUT,
        settings_file=settings_file,
    )

    parsed = parse_structured_output(result, ImplementerTaskResult)
    print(f"implementer 修正完了: {parsed.summary}")

    if parsed.status == "error":
        raise ClaudeRunError(f"implementer 修正エラー: {parsed.message}")


def _run_implementer_final(
    *,
    tmux: TmuxManager,
    work_dir: Path,
    repo_root: Path,
    session_id: str,
    settings_file: Path,
) -> None:
    """implementer に日誌作成と git commit を指示する。"""
    prompt = (
        "日誌を diary/ ディレクトリに作成してください。"
        " 日誌にはレビュー内容、見逃しの考察、手こずったことを書いてください。"
        " その後、変更ファイルを全て git add && git commit してください。"
        " 完了後、結果を JSON で返してください。"
        " status, message の2フィールドです。"
    )

    print("implementer に日誌作成とコミットを指示しています...")

    result = run_claude(
        tmux=tmux,
        window_name="implementer",
        prompt=prompt,
        json_schema=schema_json(ImplementerFinalResult),
        work_dir=work_dir,
        prefix="impl-final",
        repo_root=repo_root,
        session_id=session_id,
        permission_mode="acceptEdits",
        timeout=_IMPLEMENTER_TIMEOUT,
        settings_file=settings_file,
    )

    parsed = parse_structured_output(result, ImplementerFinalResult)
    print(f"implementer 最終処理完了: {parsed.message}")

    if parsed.status == "error":
        raise ClaudeRunError(f"implementer 最終処理エラー: {parsed.message}")


def _user_confirmation(review_file_path: str) -> str | None:
    """ユーザーに最終確認を求める。空文字で OK、文字列でフィードバック、None で中断。"""
    print(f"\nレビュー結果: {review_file_path}")
    response = input("OK なら Enter、問題があれば内容を入力、中断は q: ").strip()
    if response.lower() == "q":
        return None
    return response
