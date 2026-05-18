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


def test_get_issue_parent_number_returns_parent_number() -> None:
    """get_issue_parent_number は親 Issue 番号を返す。"""
    captured_args: list[str] = []

    def fake_run(args: Sequence[str]) -> str:
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

    client = GitHubClient(fake_run, lambda args: b"")

    assert client.get_issue_parent_number("org/repo", 34) == 12
    assert captured_args[0:3] == ["gh", "api", "graphql"]
    assert "owner=org" in captured_args
    assert "repo=repo" in captured_args
    assert "number=34" in captured_args


def test_get_issue_parent_number_returns_none_without_parent() -> None:
    """get_issue_parent_number は親 Issue がない場合 None を返す。"""
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


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"data": {"repository": None}}, "repository"),
        ({"data": {"repository": {"issue": None}}}, "issue"),
        (
            {
                "data": {
                    "repository": {
                        "issue": {
                            "parent": {
                                "number": "12",
                            },
                        },
                    },
                },
            },
            "number",
        ),
    ],
)
def test_get_issue_parent_number_rejects_invalid_payload(
    payload: object,
    message: str,
) -> None:
    """get_issue_parent_number は不正な payload を拒否する。"""
    client = GitHubClient(
        lambda args: json.dumps(payload),
        lambda args: b"",
    )

    with pytest.raises(GitHubClientError, match=message):
        client.get_issue_parent_number("org/repo", 34)


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


def test_list_issue_labeled_events_builds_models() -> None:
    """list_issue_labeled_events は labeled event を model に変換する。"""
    captured_args: list[str] = []

    def fake_run(args: Sequence[str]) -> str:
        captured_args.extend(args)
        return json.dumps(
            [
                [
                    {
                        "id": 101,
                        "event": "labeled",
                        "label": {"name": "vv-ai:requirements"},
                        "actor": {"login": "Hiroshiba"},
                        "created_at": "2026-05-17T16:00:00Z",
                    },
                    {
                        "id": 102,
                        "event": "renamed",
                        "actor": {"login": "Hiroshiba"},
                        "created_at": "2026-05-17T16:01:00Z",
                    },
                ],
                [
                    {
                        "id": 103,
                        "event": "labeled",
                        "label": {"name": "bug"},
                        "actor": {"login": "other-user"},
                        "created_at": "2026-05-17T16:02:00Z",
                    },
                ],
            ]
        )

    client = GitHubClient(fake_run, lambda args: b"")

    events = client.list_issue_labeled_events("org/repo", 1)

    assert captured_args == [
        "gh",
        "api",
        "--paginate",
        "--slurp",
        "repos/org/repo/issues/1/timeline",
    ]
    assert len(events) == 2
    assert events[0].id == 101
    assert events[0].label_name == "vv-ai:requirements"
    assert events[0].actor.login == "Hiroshiba"
    assert events[0].created_at == "2026-05-17T16:00:00Z"
    assert events[1].id == 103
    assert events[1].label_name == "bug"


def test_list_issue_timeline_events_builds_comment_and_label_models() -> None:
    """list_issue_timeline_events は comment と label を返却順で model 化する。"""
    client = GitHubClient(
        lambda args: json.dumps(
            [
                [
                    {
                        "id": 201,
                        "event": "commented",
                        "body": "@vv-ai requirements",
                        "user": {"login": "Hiroshiba"},
                        "created_at": "2026-05-17T16:00:00Z",
                    },
                    {
                        "id": 202,
                        "event": "labeled",
                        "label": {"name": "vv-ai:next"},
                        "actor": {"login": "Hiroshiba"},
                        "created_at": "2026-05-17T16:00:00Z",
                    },
                ]
            ]
        ),
        lambda args: b"",
    )

    events = client.list_issue_timeline_events("org/repo", 1)

    assert [event.event for event in events] == ["commented", "labeled"]
    assert events[0].body == "@vv-ai requirements"
    assert events[1].label_name == "vv-ai:next"


def test_list_issue_labeled_events_rejects_invalid_page() -> None:
    """list_issue_labeled_events は不正なページ形式を拒否する。"""
    client = GitHubClient(lambda args: json.dumps([{"event": "labeled"}]), lambda args: b"")

    with pytest.raises(GitHubClientError, match="ページ形式"):
        client.list_issue_labeled_events("org/repo", 1)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            [[{"id": 101, "event": "labeled", "actor": {"login": "Hiroshiba"}}]],
            "label",
        ),
        (
            [
                [
                    {
                        "id": 101,
                        "event": "labeled",
                        "label": {"name": "vv-ai:next"},
                    }
                ]
            ],
            "REST user",
        ),
    ],
)
def test_list_issue_labeled_events_rejects_invalid_event(
    payload: object,
    message: str,
) -> None:
    """list_issue_labeled_events は不正な event 要素を拒否する。"""
    client = GitHubClient(lambda args: json.dumps(payload), lambda args: b"")

    with pytest.raises(GitHubClientError, match=message):
        client.list_issue_labeled_events("org/repo", 1)


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


def test_get_pull_request_sync_state_builds_model() -> None:
    """get_pull_request_sync_state は PR 状態を model に変換する。"""
    captured_args: list[str] = []

    def fake_run(args: Sequence[str]) -> str:
        captured_args.extend(args)
        return json.dumps(
            {
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "UNSTABLE",
                "statusCheckRollup": [
                    {"state": "SUCCESS"},
                    {"state": "FAILURE"},
                    {"state": "PENDING"},
                    {"status": "COMPLETED", "conclusion": "SUCCESS"},
                    {"status": "IN_PROGRESS", "conclusion": None},
                    {"status": "COMPLETED", "conclusion": "TIMED_OUT"},
                    {"status": "COMPLETED", "conclusion": "UNKNOWN_VALUE"},
                ],
            }
        )

    client = GitHubClient(fake_run, lambda args: b"")

    state = client.get_pull_request_sync_state("org/repo", 34)

    assert captured_args == [
        "gh",
        "pr",
        "view",
        "34",
        "--repo",
        "org/repo",
        "--json",
        "mergeable,mergeStateStatus,statusCheckRollup",
    ]
    assert state.mergeable == "MERGEABLE"
    assert state.merge_state_status == "UNSTABLE"
    assert state.status_check_summary.success_count == 2
    assert state.status_check_summary.failure_count == 2
    assert state.status_check_summary.pending_count == 2
    assert state.status_check_summary.unknown_count == 1


def test_get_pull_request_sync_state_allows_empty_status_checks() -> None:
    """get_pull_request_sync_state は status checks が空でも成功する。"""
    client = GitHubClient(
        lambda args: json.dumps(
            {
                "mergeable": "UNKNOWN",
                "mergeStateStatus": "UNKNOWN",
                "statusCheckRollup": [],
            }
        ),
        lambda args: b"",
    )

    state = client.get_pull_request_sync_state("org/repo", 34)

    assert state.mergeable == "UNKNOWN"
    assert state.merge_state_status == "UNKNOWN"
    assert state.status_check_summary.success_count == 0
    assert state.status_check_summary.failure_count == 0
    assert state.status_check_summary.pending_count == 0
    assert state.status_check_summary.unknown_count == 0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON 形式"),
        (
            {
                "mergeable": None,
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [],
            },
            "mergeable",
        ),
        (
            {
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": {},
            },
            "statusCheckRollup",
        ),
        (
            {
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": ["bad"],
            },
            "statusCheckRollup",
        ),
    ],
)
def test_get_pull_request_sync_state_rejects_invalid_payload(
    payload: object,
    message: str,
) -> None:
    """get_pull_request_sync_state は不正な payload を拒否する。"""
    client = GitHubClient(lambda args: json.dumps(payload), lambda args: b"")

    with pytest.raises(GitHubClientError, match=message):
        client.get_pull_request_sync_state("org/repo", 34)


def test_list_repository_artifacts_by_prefix_filters_and_sorts() -> None:
    """list_repository_artifacts_by_prefix は prefix 一致 artifact を新しい順で返す。"""
    client = GitHubClient(
        lambda args: json.dumps(
            [
                {
                    "artifacts": [
                        {
                            "id": 1,
                            "name": "vv-ai-session__target__codex__main__old",
                            "created_at": "2026-01-01T00:00:00Z",
                            "archive_download_url": "https://example.test/1",
                        },
                        {
                            "id": 3,
                            "name": "vv-ai-session__target__codex__main__same-newer-id",
                            "created_at": "2026-01-02T00:00:00Z",
                            "archive_download_url": "https://example.test/3",
                        },
                        {
                            "id": 2,
                            "name": "vv-ai-session__target__codex__main__same-older-id",
                            "created_at": "2026-01-02T00:00:00Z",
                            "archive_download_url": "https://example.test/2",
                        },
                        {
                            "id": 4,
                            "name": "vv-ai-report__target__codex__main__new",
                            "created_at": "2026-01-03T00:00:00Z",
                            "archive_download_url": "https://example.test/4",
                        },
                    ]
                }
            ]
        ),
        lambda args: b"",
    )

    artifacts = client.list_repository_artifacts_by_prefix(
        "org/repo",
        "vv-ai-session__target__codex__main__",
    )

    assert [artifact.id for artifact in artifacts] == [3, 2, 1]


def test_list_repository_artifacts_by_prefix_returns_empty() -> None:
    """list_repository_artifacts_by_prefix は一致なしなら空 list を返す。"""
    client = GitHubClient(
        lambda args: json.dumps(
            [
                {
                    "artifacts": [
                        {
                            "id": 1,
                            "name": "vv-ai-report__target__codex__main__old",
                            "created_at": "2026-01-01T00:00:00Z",
                            "archive_download_url": "https://example.test/1",
                        }
                    ]
                }
            ]
        ),
        lambda args: b"",
    )

    artifacts = client.list_repository_artifacts_by_prefix(
        "org/repo",
        "vv-ai-session__target__codex__main__",
    )

    assert artifacts == []


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
