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


def test_get_issue_parent_number_returns_parent() -> None:
    """get_issue_parent_number は親 Issue 番号を返す。"""
    captured_args: list[str] = []

    def fake_runner(args: Sequence[str]) -> str:
        captured_args.extend(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "parent": {
                                "number": 12,
                            },
                        },
                    },
                },
            }
        )

    client = GitHubClient(fake_runner, lambda args: b"")

    assert client.get_issue_parent_number("org/repo", 34) == 12
    assert "graphql" in captured_args
    assert "owner=org" in captured_args
    assert "name=repo" in captured_args
    assert "number=34" in captured_args


def test_get_issue_parent_number_returns_none() -> None:
    """get_issue_parent_number は親 Issue がなければ None を返す。"""
    client = GitHubClient(
        lambda args: json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "parent": None,
                        },
                    },
                },
            }
        ),
        lambda args: b"",
    )

    assert client.get_issue_parent_number("org/repo", 34) is None


def test_get_issue_parent_number_rejects_invalid_payload() -> None:
    """get_issue_parent_number は不正な payload を拒否する。"""
    client = GitHubClient(
        lambda args: json.dumps({"data": {"repository": {"issue": {"parent": {}}}}}),
        lambda args: b"",
    )

    with pytest.raises(GitHubClientError, match="親 Issue 番号"):
        client.get_issue_parent_number("org/repo", 34)


def test_build_github_client_with_token_uses_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
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
