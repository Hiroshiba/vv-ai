"""target context 差分生成のテスト。"""

from __future__ import annotations

from vv_ai.github import GitHubActor, GitHubComment, GitHubIssue
from vv_ai.resolve import ResolvedTarget
from vv_ai.target_context import build_target_context, empty_target_context_state


class _GitHubClient:
    """target context テスト用 GitHub client。"""

    def __init__(
        self,
        issue: GitHubIssue,
        comments: list[GitHubComment],
    ) -> None:
        self.issue = issue
        self.comments = comments

    def get_issue(self, repository_full_name: str, number: int) -> GitHubIssue:
        """Issue を返す。"""
        return self.issue

    def list_issue_comments(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubComment]:
        """Issue comment 一覧を返す。"""
        return self.comments


def _make_target() -> ResolvedTarget:
    """GitHub Issue target を返す。"""
    return ResolvedTarget(
        backend="github",
        kind="issue",
        canonical_id="org/repo#1",
        repository_full_name="org/repo",
        number=1,
    )


def _make_issue(title: str, body: str) -> GitHubIssue:
    """GitHubIssue を返す。"""
    return GitHubIssue(
        id=10,
        repository_full_name="org/repo",
        number=1,
        title=title,
        body=body,
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url="https://github.com/org/repo/issues/1",
    )


def _make_comment(comment_id: int, body: str, updated_at: str) -> GitHubComment:
    """GitHubComment を返す。"""
    return GitHubComment(
        id=comment_id,
        body=body,
        author=GitHubActor(login="Hiroshiba"),
        created_at=f"2026-05-08T00:00:0{comment_id}Z",
        updated_at=updated_at,
        url=f"https://github.com/org/repo/issues/1#issuecomment-{comment_id}",
    )


def test_new_session_includes_title_description_and_comments() -> None:
    client = _GitHubClient(
        _make_issue("タイトル", "本文"),
        [
            _make_comment(1, "過去コメント", "2026-05-08T00:00:01Z"),
            _make_comment(2, "@vv-ai implement", "2026-05-08T00:00:02Z"),
        ],
    )

    result = build_target_context(
        client,
        _make_target(),
        2,
        empty_target_context_state(),
        None,
    )

    assert result.prompt_block is not None
    assert "タイトル" in result.prompt_block
    assert "本文" in result.prompt_block
    assert "過去コメント" in result.prompt_block
    assert "@vv-ai implement" not in result.prompt_block
    assert "1" in result.state.comment_hashes
    assert "2" in result.state.comment_hashes


def test_inherited_session_omits_unchanged_target_context() -> None:
    client = _GitHubClient(
        _make_issue("タイトル", "本文"),
        [_make_comment(1, "過去コメント", "2026-05-08T00:00:01Z")],
    )
    first = build_target_context(
        client,
        _make_target(),
        None,
        empty_target_context_state(),
        None,
    )

    second = build_target_context(
        client,
        _make_target(),
        None,
        first.state,
        None,
    )

    assert second.prompt_block is None
    assert second.state == first.state


def test_inherited_session_includes_edited_comment_only() -> None:
    first_client = _GitHubClient(
        _make_issue("タイトル", "本文"),
        [_make_comment(1, "過去コメント", "2026-05-08T00:00:01Z")],
    )
    first = build_target_context(
        first_client,
        _make_target(),
        None,
        empty_target_context_state(),
        None,
    )
    edited_client = _GitHubClient(
        _make_issue("タイトル", "本文"),
        [_make_comment(1, "編集後コメント", "2026-05-08T00:00:03Z")],
    )

    result = build_target_context(
        edited_client,
        _make_target(),
        None,
        first.state,
        None,
    )

    assert result.prompt_block is not None
    assert "編集後コメント" in result.prompt_block
    assert "## タイトル" not in result.prompt_block
    assert "## Description" not in result.prompt_block
