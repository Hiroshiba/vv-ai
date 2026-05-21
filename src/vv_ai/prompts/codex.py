"""Codex provider 用 prompt の構築。"""

from __future__ import annotations

from pathlib import Path


def build_codex_provider_prompt(provider_prompt: str, work_dir: Path) -> str:
    """Codex provider 固有の作業用ディレクトリ指示を追加する。"""
    work_dir_text = work_dir.as_posix()
    return "\n\n".join(
        [
            provider_prompt,
            "\n".join(
                [
                    "Codex provider asset の編集指示",
                    "",
                    "- `.codex/` は直接編集しないでください。",
                    "- Codex 用 provider asset を変更する場合は "
                    f"`{work_dir_text}/AGENTS.md`、`{work_dir_text}/skills/`、"
                    f"`{work_dir_text}/agents/` を編集してください。",
                    f"- `{work_dir_text}/` は作業用 mirror です。"
                    "この配下のファイルは git に追加しなくて大丈夫です。",
                    f"- 実行後に vv-ai が `{work_dir_text}/` から `.codex/` へ同期します。",
                ]
            ),
        ]
    )
