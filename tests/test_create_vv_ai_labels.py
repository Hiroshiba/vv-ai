"""vv-ai ラベル作成ツールの単体テスト。"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from tools.create_vv_ai_labels import (
    VV_AI_LABELS,
    _list_existing_label_names,
    _resolve_repository_full_name,
    _sync_labels,
)
from vv_ai.input import _LABEL_COMMANDS


def test_label_names_match_label_invocation() -> None:
    """作成対象ラベル名はラベル起動対象と一致する。"""
    assert {label.name for label in VV_AI_LABELS} == set(_LABEL_COMMANDS)


def test_label_names_include_next() -> None:
    """作成対象ラベル名に next ラベルを含む。"""
    assert "vv-ai:next" in {label.name for label in VV_AI_LABELS}


def test_label_names_include_address() -> None:
    """作成対象ラベル名に address ラベルを含む。"""
    assert "vv-ai:address" in {label.name for label in VV_AI_LABELS}


def test_label_names_include_sync() -> None:
    """作成対象ラベル名に sync ラベルを含む。"""
    assert "vv-ai:sync" in {label.name for label in VV_AI_LABELS}


def test_sync_labels_creates_missing_and_edits_existing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_sync_labels は既存ラベルを更新し、不足ラベルを作成する。"""
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str],
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[1] == "api":
            return subprocess.CompletedProcess(cmd, 0, stdout="vv-ai:reply\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _sync_labels("org/repo", False)

    output = capsys.readouterr().out
    assert calls[0] == [
        "gh",
        "api",
        "repos/org/repo/labels?per_page=100",
        "--paginate",
        "--jq",
        ".[].name",
    ]
    assert calls[1][0:4] == ["gh", "label", "edit", "vv-ai:reply"]
    assert calls[1][-2:] == ["--repo", "org/repo"]
    assert calls[2][0:4] == ["gh", "label", "create", "vv-ai:confirm"]
    assert calls[2][-2:] == ["--repo", "org/repo"]
    assert len(calls) == len(VV_AI_LABELS) + 1
    assert "更新しました: vv-ai:reply" in output
    assert "作成しました: vv-ai:confirm" in output
    assert "org/repo の vv-ai ラベルを同期しました" in output


def test_sync_labels_dry_run_skips_create_and_edit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dry-run はラベル一覧取得だけを行う。"""
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str],
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[1:3] == ["repo", "view"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="org/repo\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="vv-ai:reply\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _sync_labels(None, True)

    output = capsys.readouterr().out
    assert calls == [
        [
            "gh",
            "repo",
            "view",
            "--json",
            "nameWithOwner",
            "--jq",
            ".nameWithOwner",
        ],
        [
            "gh",
            "api",
            "repos/org/repo/labels?per_page=100",
            "--paginate",
            "--jq",
            ".[].name",
        ],
    ]
    assert "更新予定: vv-ai:reply" in output
    assert "作成予定: vv-ai:confirm" in output
    assert "--dry-run: GitHub ラベルは変更していません。対象: 現在のリポジトリ" in output


def test_list_existing_label_names_uses_paginated_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_list_existing_label_names は paginated API からラベル名を取得する。"""
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str],
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="vv-ai:reply\nvv-ai:confirm\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _list_existing_label_names("org/repo") == {
        "vv-ai:reply",
        "vv-ai:confirm",
    }
    assert calls == [
        [
            "gh",
            "api",
            "repos/org/repo/labels?per_page=100",
            "--paginate",
            "--jq",
            ".[].name",
        ]
    ]


def test_resolve_repository_full_name_rejects_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_repository_full_name は空のリポジトリ名を拒否する。"""

    def fake_run(
        cmd: Sequence[str],
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="リポジトリ名"):
        _resolve_repository_full_name(None)
