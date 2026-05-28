"""target context 差分生成のテスト。"""

from __future__ import annotations

from vv_ai.backends.github.models import (
    GitHubActor,
    GitHubComment,
    GitHubIssue,
    GitHubPullRequest,
    GitHubPullRequestReview,
)
from vv_ai.inputs.resolve import ResolvedTarget
from vv_ai.prompts.target_context import build_target_context, empty_target_context_state


class _GitHubClient:
    """target context テスト用 GitHub client。"""

    def __init__(
        self,
        issue: GitHubIssue,
        pull_request: GitHubPullRequest | None,
        comments: list[GitHubComment],
        reviews: list[GitHubPullRequestReview],
    ) -> None:
        self.issue = issue
        self.pull_request = pull_request
        self.comments = comments
        self.reviews = reviews
        self.review_call_count = 0

    def get_issue(self, repository_full_name: str, number: int) -> GitHubIssue:
        """Issue を返す。"""
        return self.issue

    def get_pull_request(
        self,
        repository_full_name: str,
        number: int,
    ) -> GitHubPullRequest:
        """Pull Request を返す。"""
        if self.pull_request is None:
            raise RuntimeError("Pull Request が設定されていません")
        return self.pull_request

    def list_issue_comments(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubComment]:
        """Issue comment 一覧を返す。"""
        return self.comments

    def list_pull_request_reviews(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubPullRequestReview]:
        """Pull Request review submission 一覧を返す。"""
        self.review_call_count += 1
        return self.reviews


def _make_target() -> ResolvedTarget:
    """GitHub Issue target を返す。"""
    return ResolvedTarget(
        backend="github",
        kind="issue",
        canonical_id="org/repo#1",
        repository_full_name="org/repo",
        number=1,
    )


def _make_pr_target() -> ResolvedTarget:
    """GitHub Pull Request target を返す。"""
    return ResolvedTarget(
        backend="github",
        kind="pr",
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


def _make_pull_request(title: str, body: str) -> GitHubPullRequest:
    """GitHubPullRequest を返す。"""
    return GitHubPullRequest(
        repository_full_name="org/repo",
        number=1,
        title=title,
        body=body,
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url="https://github.com/org/repo/pull/1",
        head_ref_name="feature",
        base_ref_name="main",
        head_repository_full_name="org/repo",
        is_cross_repository=False,
        maintainer_can_modify=True,
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


def _make_review(
    review_id: int,
    body: str,
    created_at: str,
) -> GitHubPullRequestReview:
    """GitHubPullRequestReview を返す。"""
    return GitHubPullRequestReview(
        id=review_id,
        body=body,
        author=GitHubActor(login="reviewer"),
        created_at=created_at,
        url=f"https://github.com/org/repo/pull/1#pullrequestreview-{review_id}",
    )


def test_new_session_includes_title_description_and_comments() -> None:
    client = _GitHubClient(
        _make_issue("タイトル", "本文"),
        None,
        [
            _make_comment(1, "過去コメント", "2026-05-08T00:00:01Z"),
            _make_comment(2, "@vv-ai implement", "2026-05-08T00:00:02Z"),
        ],
        [],
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
        None,
        [_make_comment(1, "過去コメント", "2026-05-08T00:00:01Z")],
        [],
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
        None,
        [_make_comment(1, "過去コメント", "2026-05-08T00:00:01Z")],
        [],
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
        None,
        [_make_comment(1, "編集後コメント", "2026-05-08T00:00:03Z")],
        [],
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


def test_pr_target_includes_pull_request_reviews_in_created_order() -> None:
    client = _GitHubClient(
        _make_issue("未使用", "未使用"),
        _make_pull_request("PR タイトル", "PR 本文"),
        [_make_comment(2, "通常コメント", "2026-05-08T00:00:02Z")],
        [_make_review(10, "レビュー本文", "2026-05-08T00:00:01Z")],
    )

    result = build_target_context(
        client,
        _make_pr_target(),
        None,
        empty_target_context_state(),
        None,
    )

    assert result.prompt_block is not None
    assert "PR タイトル" in result.prompt_block
    assert "PR 本文" in result.prompt_block
    assert "## PR review submission 10" in result.prompt_block
    assert "- 作成者: reviewer" in result.prompt_block
    assert "- 作成日時: 2026-05-08T00:00:01Z" in result.prompt_block
    assert (
        "- URL: https://github.com/org/repo/pull/1#pullrequestreview-10"
        in result.prompt_block
    )
    assert result.prompt_block.index("レビュー本文") < result.prompt_block.index(
        "通常コメント"
    )


def test_pr_target_omits_empty_pull_request_reviews() -> None:
    client = _GitHubClient(
        _make_issue("未使用", "未使用"),
        _make_pull_request("PR タイトル", "PR 本文"),
        [],
        [_make_review(10, "  \n", "2026-05-08T00:00:01Z")],
    )

    result = build_target_context(
        client,
        _make_pr_target(),
        None,
        empty_target_context_state(),
        None,
    )

    assert result.prompt_block is not None
    assert "PR review submission" not in result.prompt_block
    assert "review:10" not in result.state.comment_hashes


def test_inherited_session_omits_unchanged_pull_request_review() -> None:
    client = _GitHubClient(
        _make_issue("未使用", "未使用"),
        _make_pull_request("PR タイトル", "PR 本文"),
        [],
        [_make_review(10, "レビュー本文", "2026-05-08T00:00:01Z")],
    )
    first = build_target_context(
        client,
        _make_pr_target(),
        None,
        empty_target_context_state(),
        None,
    )

    second = build_target_context(
        client,
        _make_pr_target(),
        None,
        first.state,
        None,
    )

    assert second.prompt_block is None
    assert second.state == first.state


def test_issue_target_does_not_fetch_pull_request_reviews() -> None:
    client = _GitHubClient(
        _make_issue("タイトル", "本文"),
        None,
        [],
        [_make_review(10, "レビュー本文", "2026-05-08T00:00:01Z")],
    )

    build_target_context(
        client,
        _make_target(),
        None,
        empty_target_context_state(),
        None,
    )

    assert client.review_call_count == 0


def test_comment_and_pull_request_review_state_keys_do_not_conflict() -> None:
    client = _GitHubClient(
        _make_issue("未使用", "未使用"),
        _make_pull_request("PR タイトル", "PR 本文"),
        [_make_comment(1, "通常コメント", "2026-05-08T00:00:02Z")],
        [_make_review(1, "レビュー本文", "2026-05-08T00:00:01Z")],
    )

    result = build_target_context(
        client,
        _make_pr_target(),
        None,
        empty_target_context_state(),
        None,
    )

    assert result.prompt_block is not None
    assert "通常コメント" in result.prompt_block
    assert "レビュー本文" in result.prompt_block
    assert "1" in result.state.comment_hashes
    assert "review:1" in result.state.comment_hashes
