"""GitHub client の単体テスト。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from vv_ai.github import (
    GitHubClient,
    GitHubClientError,
    build_github_client_with_token,
)


def test_get_repository_tree_builds_model() -> None:
    """get_repository_tree は git tree response を model に変換する。"""
    client = GitHubClient(
        lambda args: json.dumps(
            {
                "tree": [
                    {
                        "path": ".codex/skills/detailed-design/SKILL.md",
                        "type": "blob",
                        "sha": "abc123",
                    }
                ],
                "truncated": False,
            }
        ),
        lambda args: b"",
    )

    tree = client.get_repository_tree("org/repo", "main")

    assert tree.truncated is False
    assert len(tree.tree) == 1
    assert tree.tree[0].path == ".codex/skills/detailed-design/SKILL.md"
    assert tree.tree[0].sha == "abc123"


def test_get_repository_blob_decodes_base64() -> None:
    """get_repository_blob は base64 content を bytes に変換する。"""
    client = GitHubClient(
        lambda args: json.dumps({"encoding": "base64", "content": "44OG44K544OI"}),
        lambda args: b"",
    )

    assert client.get_repository_blob("org/repo", "abc123") == "テスト".encode()


def test_get_repository_blob_rejects_unknown_encoding() -> None:
    """get_repository_blob は base64 以外の encoding を拒否する。"""
    client = GitHubClient(
        lambda args: json.dumps({"encoding": "utf-8", "content": "text"}),
        lambda args: b"",
    )

    with pytest.raises(GitHubClientError, match="encoding"):
        client.get_repository_blob("org/repo", "abc123")


def test_remove_issue_label_uses_delete_method_with_encoded_label() -> None:
    """remove_issue_label は encode 済み label path を DELETE する。"""
    captured_args: list[Sequence[str]] = []
    client = GitHubClient(
        lambda args: captured_args.append(args) or "",
        lambda args: b"",
    )

    client.remove_issue_label("org/repo", 1, "vv-ai:confirm")

    assert captured_args == [
        [
            "gh",
            "api",
            "--method",
            "DELETE",
            "repos/org/repo/issues/1/labels/vv-ai%3Aconfirm",
        ]
    ]


def test_build_github_client_with_token_uses_gh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_github_client_with_token は GH_TOKEN を明示した env で gh を実行する。"""
    captured_env: dict[str, str] = {}

    def fake_run(
        args: Sequence[str],
        check: bool,
        capture_output: bool,
        text: bool,
        encoding: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        captured_env.update(env)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "defaultBranchRef": {
                        "name": "main",
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("GITHUB_TOKEN", "write-token")

    client = build_github_client_with_token("read-token")

    assert client.get_default_branch("org/repo") == "main"
    assert captured_env["GH_TOKEN"] == "read-token"
    assert "GITHUB_TOKEN" not in captured_env
