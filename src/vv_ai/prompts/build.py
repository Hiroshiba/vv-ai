"""コンテキスト付きプロバイダプロンプトの構築。"""

from __future__ import annotations

from vv_ai.workflow.preflight import ReadyExecution

_COMMAND_TASK_DESCRIPTION: dict[str, str] = {
    "reply": (
        "以下の指示に対してコメントで返答してください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "review": (
        "review-diff スキルを使って、この PR をレビューしてください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "confirm": (
        "confirm-intent スキルに従って要望の意図確認を行ってください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "requirements": (
        "define-requirements スキルに従って要件定義を行ってください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "arch": (
        "basic-design スキルに従って基本設計を行ってください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "detail": (
        "detailed-design スキルに従って詳細設計を行ってください。"
        "あなたの出力テキストがそのままコメントとして投稿されます。"
    ),
    "breakdown": (
        "task-breakdown スキルに従ってタスク分割を行ってください。\n"
        "結果は以下の手順でファイルに書き出してください:\n"
        "1. `mkdir -p hiho_temp && mktemp -u hiho_temp/hiho.XXXXXXXXXX` で一時パスを取得する\n"
        "2. そのパスをディレクトリとして `mkdir` で作成する\n"
        "3. ディレクトリ内に `01.md`, `02.md`, ... と連番ファイルを作成する\n"
        "4. 各ファイルは以下のフォーマットで記述する:\n"
        "   TITLE: タイトル\n"
        "   BODY:\n"
        "   本文（Markdown）...\n"
        "\n"
        "各タスクの本文には、親 Issue である breakdown 対象 Issue へ"
        "GitHub 上で辿れる参照を含めてください。\n"
        "\n"
        "最後に、作成したディレクトリの絶対パスだけを以下の形式で出力してください:\n"
        "BREAKDOWN_DIR: /絶対パス\n"
        "\n"
        "上記以外の余計な出力は含めないでください。"
        "各タスクが個別のサブ Issue として作成されます。"
    ),
    "implement": (
        "以下の Issue の内容を実装してください。\n"
        "ファイル変更のみ行ってください。git の操作は不要です。\n"
        "終了後にワーキングツリーの全変更が git add -A でコミットされます。\n"
        "一時ファイルやキャッシュは削除してから終了してください。\n"
        "出力は作成する PR のタイトル、コミットメッセージ、本文として使います。\n"
        "変更不要と判断した場合も以下のフォーマットを守り、BODY には判断理由を書いてください。\n"
        "変更コミットがない場合、BODY は対象 Issue へのコメントとして投稿されます。\n"
        "PR タイトルは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。\n"
        "コミットメッセージは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。\n"
        "PR 本文には元 Issue への参照を含めてください。\n"
        "Issue を解決する内容なら GitHub closing keyword を使っても構いません。\n"
        "以下のフォーマットに厳密に従ってください:\n"
        "1行目: TITLE: <タイトル文字列>\n"
        "2行目: COMMIT_MESSAGE: <コミットメッセージ>\n"
        "3行目: BODY:\n"
        "4行目以降: Markdown 本文\n"
        "\n"
        "タイトル、コミットメッセージ、本文以外の余計な出力は含めないでください。"
    ),
    "issue": (
        "以下の指示に基づいて、議論の出発点になる GitHub Issue を作成するための"
        "内容を生成してください。\n"
        "本文はユーザーが明示した内容を中心に短く整理してください。\n"
        "細かい仕様、受入基準、対象外、実装方式、テスト方針は、"
        "明示されている場合や Issue として自然に必要な場合だけ書いてください。\n"
        "未確定の詳細は確定した仕様のように断定せず、"
        "必要に応じて補足や確認したいこととして扱ってください。\n"
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
    "GitHub 実行時は、あなたの最終出力の本文が対象 PR にコメントとして投稿されます。"
    "fork PR で push できず patch コメントを投稿する場合、あなたの最終出力の本文は patch コメント内に含まれます。"
    "一時ファイルやキャッシュは削除してから終了してください。"
    "コミットメッセージは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
    "以下のフォーマットに厳密に従ってください:\n"
    "1行目: COMMIT_MESSAGE: <コミットメッセージ>\n"
    "2行目: BODY:\n"
    "3行目以降: Markdown の PR コメント本文\n"
    "\n"
    "コミットメッセージと本文以外の余計な出力は含めないでください。"
)

_ADDRESS_TASK_DESCRIPTION: str = (
    "address-review スキルを使って、この PR のレビュー指摘に対応してください。"
    "ファイル変更のみ行ってください。git の操作は不要です。"
    "終了後にワーキングツリーの全変更が git add -A でコミットされます。"
    "GitHub 実行時は、あなたの最終出力の本文が対象 PR にコメントとして投稿されます。"
    "fork PR で push できず patch コメントを投稿する場合、あなたの最終出力の本文は patch コメント内に含まれます。"
    "一時ファイルやキャッシュは削除してから終了してください。"
    "コミットメッセージは Conventional Commits 形式にしてください（例: fix: PRタイトルを日本語にする）。"
    "以下のフォーマットに厳密に従ってください:\n"
    "1行目: COMMIT_MESSAGE: <コミットメッセージ>\n"
    "2行目: BODY:\n"
    "3行目以降: Markdown の PR コメント本文\n"
    "\n"
    "コミットメッセージと本文以外の余計な出力は含めないでください。"
)


def build_provider_prompt(
    ready_execution: ReadyExecution,
    target_context_block: str | None,
    implement_branch_name: str | None,
    worktree_ref: str | None,
) -> str:
    """コンテキストと指示を組み合わせたプロンプト文字列を返す。"""
    sections: list[str] = []

    sections.append(_build_header(ready_execution, implement_branch_name, worktree_ref))

    if ready_execution.resolved_session is not None:
        restore_manifest = ready_execution.resolved_session.restore_manifest
        if restore_manifest is not None:
            sections.append(
                "前回のセッション状態と未コミット差分を暗号化バンドルから復元済みです。"
            )

    command_name = ready_execution.command.command
    target = ready_execution.command.target
    if command_name == "address":
        sections.append(_ADDRESS_TASK_DESCRIPTION)
    elif command_name == "implement" and target is not None and target.kind == "pr":
        sections.append(_IMPLEMENT_PR_TASK_DESCRIPTION)
    elif command_name in _COMMAND_TASK_DESCRIPTION:
        sections.append(_COMMAND_TASK_DESCRIPTION[command_name])

    instruction = ready_execution.command.instruction
    if instruction is not None:
        sections.append(f"指示:\n{instruction}")

    if target_context_block is not None:
        sections.append(f"対象の Issue / PR コンテキスト:\n{target_context_block}")

    return "\n\n".join(sections)


def _build_header(
    ready_execution: ReadyExecution,
    implement_branch_name: str | None,
    worktree_ref: str | None,
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
    elif worktree_ref is not None:
        lines.append(
            f"現在の参照: `{worktree_ref}`。この内容を前提に作業してください。"
        )
    return "\n".join(lines)
