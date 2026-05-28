"""AI 出力解析の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.commands.output_parser import (
    ReviewThreadAction,
    parse_address_output,
    parse_review_thread_actions_dir,
)


def _make_actions_dir(repo_root: Path) -> Path:
    actions_dir = repo_root / "hiho_temp" / "actions"
    actions_dir.mkdir(parents=True)
    return actions_dir


def test_parse_address_output_returns_body_without_actions() -> None:
    output = parse_address_output(
        "COMMIT_MESSAGE: fix: review\nBODY:\n対応しました",
        Path("/repo"),
    )

    assert output.commit_message == "fix: review"
    assert output.body == "対応しました"
    assert output.review_thread_actions == []


def test_parse_address_output_excludes_final_actions_dir(tmp_path: Path) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    (actions_dir / "01.md").write_text(
        "THREAD_ID: PRRT_1\nACTION: resolve\nBODY:\n",
        encoding="utf-8",
    )

    output = parse_address_output(
        "\n".join(
            [
                "COMMIT_MESSAGE: fix: review",
                "BODY:",
                "対応しました",
                f"REVIEW_THREAD_ACTIONS_DIR: {actions_dir}",
                "",
            ]
        ),
        tmp_path,
    )

    assert output.body == "対応しました"
    assert output.review_thread_actions == [
        ReviewThreadAction("PRRT_1", "resolve", "")
    ]


def test_parse_address_output_keeps_non_final_actions_dir_in_body(tmp_path: Path) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    response_text = "\n".join(
        [
            "COMMIT_MESSAGE: fix: review",
            "BODY:",
            f"REVIEW_THREAD_ACTIONS_DIR: {actions_dir}",
            "対応しました",
        ]
    )

    output = parse_address_output(response_text, tmp_path)

    assert output.body == "\n".join(
        [
            f"REVIEW_THREAD_ACTIONS_DIR: {actions_dir}",
            "対応しました",
        ]
    )
    assert output.review_thread_actions == []


def test_parse_review_thread_actions_dir_reads_md_files_only(tmp_path: Path) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    (actions_dir / "01.md").write_text(
        "THREAD_ID: PRRT_1\nACTION: comment\nBODY:\n返信本文",
        encoding="utf-8",
    )
    (actions_dir / "02.md").write_text(
        "THREAD_ID: PRRT_2\nACTION: resolve\nBODY:\n",
        encoding="utf-8",
    )
    (actions_dir / "ignore.txt").write_text(
        "THREAD_ID: PRRT_3\nACTION: comment\nBODY:\n対象外",
        encoding="utf-8",
    )

    actions = parse_review_thread_actions_dir(actions_dir, tmp_path)

    assert actions == [
        ReviewThreadAction("PRRT_1", "comment", "返信本文"),
        ReviewThreadAction("PRRT_2", "resolve", ""),
    ]


def test_parse_review_thread_actions_dir_rejects_outside_dir(tmp_path: Path) -> None:
    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()

    with pytest.raises(RuntimeError, match="hiho_temp 配下"):
        parse_review_thread_actions_dir(actions_dir, tmp_path)


def test_parse_review_thread_actions_dir_rejects_symlink_dir(tmp_path: Path) -> None:
    real_dir = _make_actions_dir(tmp_path)
    symlink_dir = tmp_path / "hiho_temp" / "linked"
    symlink_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="シンボリックリンク"):
        parse_review_thread_actions_dir(symlink_dir, tmp_path)


def test_parse_review_thread_actions_dir_rejects_symlink_file(tmp_path: Path) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    real_file = tmp_path / "real.md"
    real_file.write_text("THREAD_ID: PRRT_1\nACTION: resolve\nBODY:\n", encoding="utf-8")
    (actions_dir / "01.md").symlink_to(real_file)

    with pytest.raises(RuntimeError, match="シンボリックリンク"):
        parse_review_thread_actions_dir(actions_dir, tmp_path)


def test_parse_review_thread_actions_dir_rejects_duplicate_thread_id(
    tmp_path: Path,
) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    (actions_dir / "01.md").write_text(
        "THREAD_ID: PRRT_1\nACTION: resolve\nBODY:\n",
        encoding="utf-8",
    )
    (actions_dir / "02.md").write_text(
        "THREAD_ID: PRRT_1\nACTION: comment\nBODY:\n返信本文",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="THREAD_ID が重複"):
        parse_review_thread_actions_dir(actions_dir, tmp_path)


def test_parse_review_thread_actions_dir_rejects_invalid_action(
    tmp_path: Path,
) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    (actions_dir / "01.md").write_text(
        "THREAD_ID: PRRT_1\nACTION: close\nBODY:\n返信本文",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="resolve または comment"):
        parse_review_thread_actions_dir(actions_dir, tmp_path)


def test_parse_review_thread_actions_dir_rejects_empty_comment_body(
    tmp_path: Path,
) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    (actions_dir / "01.md").write_text(
        "THREAD_ID: PRRT_1\nACTION: comment\nBODY:\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="BODY が必要"):
        parse_review_thread_actions_dir(actions_dir, tmp_path)


def test_parse_review_thread_actions_dir_rejects_missing_body_line(
    tmp_path: Path,
) -> None:
    actions_dir = _make_actions_dir(tmp_path)
    (actions_dir / "01.md").write_text(
        "THREAD_ID: PRRT_1\nACTION: resolve\n本文",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="3行目は `BODY:`"):
        parse_review_thread_actions_dir(actions_dir, tmp_path)
