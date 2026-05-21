"""sync コマンド用 prompt の構築。"""

from __future__ import annotations


def build_sync_conflict_prompt(conflict_files: list[str]) -> str:
    """conflict 解消専用 prompt を返す。"""
    files = "\n".join(f"- {path}" for path in conflict_files)
    return (
        "conflict の解消だけを行ってください。\n"
        "対象ファイル以外は変更しないでください。\n"
        "commit と stage は行わないでください。\n"
        "対象ファイルを merge 後に残すべき状態へ整えてください。\n\n"
        f"対象ファイル:\n{files}"
    )


def build_sync_consistency_prompt(head_ref_name: str, base_ref_name: str) -> str:
    """merge 後の整合性確認 prompt を返す。"""
    return (
        "sync コマンドの整合性確認を行ってください。\n"
        "必要最小限の修正だけをファイルへ反映してください。\n"
        "commit と stage は行わないでください。\n\n"
        "最後に GitHub PR へ投稿する本文を出力してください。\n"
        "出力には BODY: 行を必ず含め、その次の行から投稿本文だけを書いてください。\n"
        "push 結果や push 後の GitHub PR 状態は本文に含めないでください。\n\n"
        f"head branch: {head_ref_name}\n"
        f"base branch: {base_ref_name}"
    )
