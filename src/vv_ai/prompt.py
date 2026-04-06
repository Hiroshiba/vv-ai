"""コンテキスト付きプロバイダプロンプトの構築。"""

from __future__ import annotations

from vv_ai.preflight import ReadyExecution

_COMMAND_TASK_DESCRIPTION: dict[str, str] = {
    "reply": (
        "以下の指示に対してコメントで返答してください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "plan": (
        "以下の指示に基づいて計画（タスク分解・方針）をコメントで返してください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "review": (
        "この PR をレビューし、指摘・改善提案をコメントで返してください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "implement": (
        "以下の Issue の内容を実装してください。"
        "ファイル変更のみ行ってください。git の操作は不要です。"
        "終了後にワーキングツリーの全変更が git add -A でコミットされます。"
        "一時ファイルやキャッシュは削除してから終了してください。"
    ),
    "issue": (
        "以下の指示に基づいて GitHub Issue を作成するための内容を生成してください。\n"
        "出力は以下のフォーマットに厳密に従ってください:\n"
        "1行目: TITLE: <タイトル文字列>\n"
        "2行目: BODY:\n"
        "3行目以降: Markdown 本文\n"
        "\n"
        "タイトルと本文以外の余計な出力は含めないでください。"
    ),
}

_IMPLEMENT_PR_TASK_DESCRIPTION: str = (
    "この PR の内容・コメントの指示に基づいて追加実装してください。"
    "ファイル変更のみ行ってください。git の操作は不要です。"
    "終了後にワーキングツリーの全変更が git add -A でコミットされます。"
    "一時ファイルやキャッシュは削除してから終了してください。"
)


def build_provider_prompt(
    ready_execution: ReadyExecution,
    target_context: str | None,
    past_vvai_comments: list[str],
    implement_branch_name: str | None,
) -> str:
    """コンテキストと指示を組み合わせたプロンプト文字列を返す。"""
    sections: list[str] = []

    sections.append(_build_header(ready_execution, implement_branch_name))
    if target_context is not None:
        sections.append(target_context)

    if ready_execution.resolved_session is not None:
        restore_manifest = ready_execution.resolved_session.restore_manifest
        if restore_manifest is not None:
            sections.append(
                "前回のセッション状態と未コミット差分を暗号化バンドルから復元済みです。"
            )

    command_name = ready_execution.command.command
    target = ready_execution.command.target
    if command_name == "implement" and target is not None and target.kind == "pr":
        sections.append(_IMPLEMENT_PR_TASK_DESCRIPTION)
    elif command_name in _COMMAND_TASK_DESCRIPTION:
        sections.append(_COMMAND_TASK_DESCRIPTION[command_name])

    instruction = ready_execution.command.instruction
    if instruction is not None:
        sections.append(f"指示:\n{instruction}")

    if past_vvai_comments:
        past_block = "\n---\n".join(past_vvai_comments)
        sections.append(f"過去の @vv-ai コメント（補助コンテキスト）:\n{past_block}")

    return "\n\n".join(sections)


def _build_header(
    ready_execution: ReadyExecution,
    implement_branch_name: str | None,
) -> str:
    """定型ヘッダ文字列を返す。"""
    command = ready_execution.command
    target = command.target

    repo = command.repository_full_name or "(不明)"

    if target is not None and target.number is not None:
        kind_label = "PR" if target.kind == "pr" else "Issue"
        target_label = f"{kind_label} #{target.number}"
    else:
        target_label = "(対象なし)"

    lines = [
        f"あなたは GitHub repo `{repo}` の {target_label} に対して作業中です。",
        "このリポジトリはチェックアウト済みです。",
        "git 追跡ファイルはブランチの内容が永続します（push/commit されたものが正）。",
        "未追跡ファイルは原則永続しません（毎回クリーンアップされる想定）。必要なら git 管理に入れてください。",
    ]
    if implement_branch_name is not None:
        if target is not None and target.kind == "pr":
            lines.append(
                f"現在のブランチ: `{implement_branch_name}`（PR #{target.number} の head ブランチ）。"
                "このブランチ上で作業してください。"
            )
        else:
            lines.append(
                f"現在のブランチ: `{implement_branch_name}`。このブランチ上で作業してください。"
            )
    return "\n".join(lines)
