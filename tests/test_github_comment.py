"""GitHub comment helper の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.github import GitHubActor, GitHubClientError, GitHubPullRequest
from vv_ai.github_comment import (
    build_allow_edits_notice,
    build_fork_push_failure_comment,
    mark_allow_edits_notice_posted,
    post_issue_comment_safely,
)


class FakeGitHubClient:
    """テスト用 GitHub client。"""

    def __init__(self, error: GitHubClientError | None) -> None:
        self.error = error
        self.comments: list[tuple[str, int, str]] = []

    def create_issue_comment(
        self,
        repository_full_name: str,
        number: int,
        body: str,
    ) -> None:
        """Issue または PR へ comment を投稿する。"""
        if self.error is not None:
            raise self.error
        self.comments.append((repository_full_name, number, body))


def test_post_issue_comment_safely_returns_true_when_comment_succeeds() -> None:
    """post_issue_comment_safely はコメント投稿成功時に True を返す。"""
    github_client = FakeGitHubClient(None)

    posted = post_issue_comment_safely(
        github_client,
        "org/repo",
        12,
        "本文",
        "テストコメント投稿",
    )

    assert posted is True
    assert github_client.comments == [("org/repo", 12, "本文")]


def test_post_issue_comment_safely_returns_false_when_github_client_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """post_issue_comment_safely は GitHubClientError を stderr に出して False を返す。"""
    github_client = FakeGitHubClient(GitHubClientError("失敗しました"))

    posted = post_issue_comment_safely(
        github_client,
        "org/repo",
        12,
        "本文",
        "テストコメント投稿",
    )

    captured = capsys.readouterr()
    assert posted is False
    assert github_client.comments == []
    assert captured.err == "テストコメント投稿に失敗しました: 失敗しました\n"


def test_build_allow_edits_notice_returns_empty_when_maintainer_edits_enabled() -> None:
    """build_allow_edits_notice は maintainer edits が有効なら空文字を返す。"""
    pr_info = _make_pr_info(maintainer_can_modify=True)

    assert build_allow_edits_notice(False, pr_info) == ""


def test_build_allow_edits_notice_returns_notice_only_when_not_posted() -> None:
    """build_allow_edits_notice は maintainer edits が無効で未案内なら案内文を返す。"""
    pr_info = _make_pr_info(maintainer_can_modify=False)

    notice = build_allow_edits_notice(False, pr_info)

    assert 'Allow edits from maintainers' in notice
    assert "次回以降 vv-ai が直接修正をプッシュできるようになります" in notice


def test_build_allow_edits_notice_returns_empty_when_notice_already_posted() -> None:
    """build_allow_edits_notice は復元済み案内済み状態なら空文字を返す。"""
    pr_info = _make_pr_info(maintainer_can_modify=False)

    assert build_allow_edits_notice(True, pr_info) == ""


def test_mark_allow_edits_notice_posted_stays_false_when_comment_fails() -> None:
    """mark_allow_edits_notice_posted は案内文投稿失敗時に False のままにする。"""
    assert mark_allow_edits_notice_posted(False, "案内文", False) is False


def test_mark_allow_edits_notice_posted_returns_true_when_notice_was_posted() -> None:
    """mark_allow_edits_notice_posted は案内文投稿成功時だけ True を返す。"""
    assert mark_allow_edits_notice_posted(False, "案内文", True) is True


def test_mark_allow_edits_notice_posted_keeps_true_when_already_posted() -> None:
    """mark_allow_edits_notice_posted は案内済み状態を True のまま維持する。"""
    assert mark_allow_edits_notice_posted(True, "", False) is True


def test_mark_allow_edits_notice_posted_stays_false_without_notice() -> None:
    """mark_allow_edits_notice_posted は案内文がなければ投稿成功でも False のままにする。"""
    assert mark_allow_edits_notice_posted(False, "", True) is False


def test_build_fork_push_failure_comment_uses_steps_when_patch_is_large() -> None:
    """build_fork_push_failure_comment は大きい patch を途中で切らない。"""
    body = build_fork_push_failure_comment("", "a" * 60001, "")

    assert "```diff" not in body
    assert "patch が大きいためコメント本文には含めません" in body


def _make_pr_info(maintainer_can_modify: bool) -> GitHubPullRequest:
    """テスト用 Pull Request 情報を生成する。"""
    return GitHubPullRequest(
        repository_full_name="org/repo",
        number=1,
        title="テスト PR",
        body="テスト本文",
        state="OPEN",
        author=GitHubActor(login="Hiroshiba"),
        url="https://github.com/org/repo/pull/1",
        head_ref_name="feature",
        base_ref_name="main",
        head_repository_full_name="fork/repo",
        is_cross_repository=True,
        maintainer_can_modify=maintainer_can_modify,
    )
