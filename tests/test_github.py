"""GitHub client の単体テスト。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from vv_ai.backends.github.client import (
    GitHubClient,
    build_github_client_with_token,
)
from vv_ai.backends.github.models import GitHubClientError


def _make_timeline_page(nodes: list[object]) -> dict[str, object]:
    """GraphQL timelineItems page payload を生成する。"""
    return {
        "data": {
            "repository": {
                "issueOrPullRequest": {
                    "timelineItems": {
                        "nodes": nodes,
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    },
                },
            },
        },
    }


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
                _make_timeline_page(
                    [
                        {
                            "__typename": "LabeledEvent",
                            "databaseId": 101,
                            "label": {"name": "vv-ai:requirements"},
                            "actor": {"login": "Hiroshiba"},
                            "createdAt": "2026-05-17T16:00:00Z",
                        },
                        {
                            "__typename": "IssueComment",
                            "databaseId": 102,
                            "body": "@vv-ai confirm",
                            "author": {"login": "Hiroshiba"},
                            "createdAt": "2026-05-17T16:01:00Z",
                        },
                    ]
                ),
                _make_timeline_page(
                    [
                        {
                            "__typename": "LabeledEvent",
                            "databaseId": 103,
                            "label": {"name": "bug"},
                            "actor": {"login": "other-user"},
                            "createdAt": "2026-05-17T16:02:00Z",
                        }
                    ]
                ),
            ]
        )

    client = GitHubClient(fake_run, lambda args: b"")

    events = client.list_issue_labeled_events("org/repo", 1)

    assert captured_args == [
        "gh",
        "api",
        "graphql",
        "--paginate",
        "--slurp",
        "-f",
        captured_args[6],
        "-f",
        "owner=org",
        "-f",
        "repo=repo",
        "-F",
        "number=1",
    ]
    query = captured_args[6]
    assert "issueOrPullRequest(number: $number)" in query
    assert "ISSUE_COMMENT" in query
    assert "LABELED_EVENT" in query
    assert "SUB_ISSUE_ADDED_EVENT" in query
    assert "CROSS_REFERENCED_EVENT" in query
    assert "willCloseTarget" not in query
    assert len(events) == 2
    assert events[0].id == 101
    assert events[0].label_name == "vv-ai:requirements"
    assert events[0].actor.login == "Hiroshiba"
    assert events[0].created_at == "2026-05-17T16:00:00Z"
    assert events[1].id == 103
    assert events[1].label_name == "bug"


def test_list_issue_timeline_events_builds_graphql_models() -> None:
    """list_issue_timeline_events は GraphQL timeline を返却順で model 化する。"""
    client = GitHubClient(
        lambda args: json.dumps(
            [
                _make_timeline_page(
                    [
                        {
                            "__typename": "IssueComment",
                            "databaseId": 201,
                            "body": "@vv-ai requirements",
                            "author": {"login": "Hiroshiba"},
                            "createdAt": "2026-05-17T16:00:00Z",
                        },
                        {
                            "__typename": "LabeledEvent",
                            "databaseId": 202,
                            "label": {"name": "vv-ai:next"},
                            "actor": {"login": "Hiroshiba"},
                            "createdAt": "2026-05-17T16:01:00Z",
                        },
                    ]
                ),
                _make_timeline_page(
                    [
                        {
                            "__typename": "SubIssueAddedEvent",
                            "databaseId": 203,
                            "actor": {"login": "Hiroshiba"},
                            "createdAt": "2026-05-17T16:02:00Z",
                            "subIssue": {
                                "number": 34,
                                "repository": {"nameWithOwner": "org/repo"},
                            },
                        },
                        {
                            "__typename": "CrossReferencedEvent",
                            "databaseId": 204,
                            "actor": {"login": "other-user"},
                            "createdAt": "2026-05-17T16:03:00Z",
                            "source": {
                                "__typename": "PullRequest",
                                "number": 56,
                                "repository": {"nameWithOwner": "other/repo"},
                            },
                        },
                    ]
                ),
            ]
        ),
        lambda args: b"",
    )

    events = client.list_issue_timeline_events("org/repo", 1)

    assert [event.event for event in events] == [
        "commented",
        "labeled",
        "sub_issue_added",
        "cross_referenced",
    ]
    assert events[0].id == 201
    assert events[0].comment_database_id == 201
    assert events[0].body == "@vv-ai requirements"
    assert events[1].label_name == "vv-ai:next"
    assert events[2].source_kind == "issue"
    assert events[2].source_number == 34
    assert events[2].source_repository_full_name == "org/repo"
    assert events[3].source_kind == "pull_request"
    assert events[3].source_number == 56
    assert events[3].source_repository_full_name == "other/repo"


def test_list_issue_timeline_events_builds_issue_cross_reference_source() -> None:
    """list_issue_timeline_events は Issue の cross reference source を model 化する。"""
    client = GitHubClient(
        lambda args: json.dumps(
            [
                _make_timeline_page(
                    [
                        {
                            "__typename": "CrossReferencedEvent",
                            "databaseId": 301,
                            "actor": {"login": "Hiroshiba"},
                            "createdAt": "2026-05-17T16:00:00Z",
                            "source": {
                                "__typename": "Issue",
                                "number": 78,
                                "repository": {"nameWithOwner": "org/repo"},
                            },
                        }
                    ]
                )
            ]
        ),
        lambda args: b"",
    )

    events = client.list_issue_timeline_events("org/repo", 1)

    assert events[0].event == "cross_referenced"
    assert events[0].source_kind == "issue"
    assert events[0].source_number == 78
    assert events[0].source_repository_full_name == "org/repo"


def test_list_issue_timeline_events_query_uses_issue_or_pull_request() -> None:
    """list_issue_timeline_events は Issue と PR の番号を同じ query で取得する。"""
    captured_args: list[str] = []

    def fake_run(args: Sequence[str]) -> str:
        captured_args.extend(args)
        return json.dumps([_make_timeline_page([])])

    client = GitHubClient(fake_run, lambda args: b"")

    assert client.list_issue_timeline_events("org/repo", 123) == []
    assert captured_args[0:5] == ["gh", "api", "graphql", "--paginate", "--slurp"]
    assert "issueOrPullRequest(number: $number)" in captured_args[6]
    assert "number=123" in captured_args
    assert "repos/org/repo/issues/123/timeline" not in captured_args


def test_list_issue_timeline_events_rejects_invalid_source() -> None:
    """list_issue_timeline_events は不正な cross reference source を拒否する。"""
    client = GitHubClient(
        lambda args: json.dumps(
            [
                _make_timeline_page(
                    [
                        {
                            "__typename": "CrossReferencedEvent",
                            "databaseId": 301,
                            "actor": {"login": "Hiroshiba"},
                            "createdAt": "2026-05-17T16:00:00Z",
                            "source": {
                                "__typename": "Commit",
                            },
                        }
                    ]
                )
            ]
        ),
        lambda args: b"",
    )

    with pytest.raises(GitHubClientError, match="source"):
        client.list_issue_timeline_events("org/repo", 1)


def test_list_issue_timeline_events_rejects_invalid_page() -> None:
    """list_issue_timeline_events は不正なページ形式を拒否する。"""
    client = GitHubClient(lambda args: json.dumps([[]]), lambda args: b"")

    with pytest.raises(GitHubClientError, match="ページ"):
        client.list_issue_timeline_events("org/repo", 1)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _make_timeline_page(
                [
                    {
                        "__typename": "LabeledEvent",
                        "databaseId": 101,
                        "actor": {"login": "Hiroshiba"},
                        "createdAt": "2026-05-17T16:00:00Z",
                    }
                ]
            ),
            "label",
        ),
        (
            _make_timeline_page(
                [
                    {
                        "__typename": "LabeledEvent",
                        "databaseId": 101,
                        "label": {"name": "vv-ai:next"},
                        "createdAt": "2026-05-17T16:00:00Z",
                    }
                ]
            ),
            "author",
        ),
    ],
)
def test_list_issue_timeline_events_rejects_invalid_event(
    payload: object,
    message: str,
) -> None:
    """list_issue_timeline_events は不正な event 要素を拒否する。"""
    client = GitHubClient(lambda args: json.dumps([payload]), lambda args: b"")

    with pytest.raises(GitHubClientError, match=message):
        client.list_issue_timeline_events("org/repo", 1)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _make_timeline_page(
                [
                    {
                        "__typename": "LabeledEvent",
                        "databaseId": 101,
                        "actor": {"login": "Hiroshiba"},
                        "createdAt": "2026-05-17T16:00:00Z",
                    }
                ]
            ),
            "label",
        ),
        (
            _make_timeline_page(
                [
                    {
                        "__typename": "LabeledEvent",
                        "databaseId": 101,
                        "label": {"name": "vv-ai:next"},
                        "createdAt": "2026-05-17T16:00:00Z",
                    }
                ]
            ),
            "author",
        ),
    ],
)
def test_list_issue_labeled_events_rejects_invalid_event(
    payload: object,
    message: str,
) -> None:
    """list_issue_labeled_events は不正な event 要素を拒否する。"""
    client = GitHubClient(lambda args: json.dumps([payload]), lambda args: b"")

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
