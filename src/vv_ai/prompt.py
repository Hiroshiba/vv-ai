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
}


def build_provider_prompt(
    ready_execution: ReadyExecution,
    past_vvai_comments: list[str],
) -> str:
    """コンテキストと指示を組み合わせたプロンプト文字列を返す。"""
    sections: list[str] = []

    sections.append(_build_header(ready_execution))

    if ready_execution.resolved_session is not None:
        restore_manifest = ready_execution.resolved_session.restore_manifest
        if restore_manifest is not None:
            sections.append(
                "前回のセッション状態と未コミット差分を暗号化バンドルから復元済みです。"
            )

    command_name = ready_execution.command.command
    if command_name in _COMMAND_TASK_DESCRIPTION:
        sections.append(_COMMAND_TASK_DESCRIPTION[command_name])

    instruction = ready_execution.command.instruction
    if instruction is not None:
        sections.append(f"指示:\n{instruction}")

    if past_vvai_comments:
        past_block = "\n---\n".join(past_vvai_comments)
        sections.append(f"過去の @vv-ai コメント（補助コンテキスト）:\n{past_block}")

    return "\n\n".join(sections)


def _build_header(ready_execution: ReadyExecution) -> str:
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
    return "\n".join(lines)
