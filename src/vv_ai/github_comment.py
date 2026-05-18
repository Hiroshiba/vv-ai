"""GitHub comment 投稿と fork PR 案内の helper。"""

from __future__ import annotations

import sys

from vv_ai.github import GitHubClient, GitHubClientError, GitHubPullRequest

_ALLOW_EDITS_NOTICE = (
    "\n\n---\n"
    '**Note**: この PR で "Allow edits from maintainers" を有効にすると、'
    "次回以降 vv-ai が直接修正をプッシュできるようになります。"
    "PR の右サイドバー下部にあるチェックボックスから設定できます。"
)


def post_issue_comment_safely(
    github_client: GitHubClient,
    repository_full_name: str,
    number: int,
    body: str,
    failure_label: str,
) -> bool:
    """Issue または PR へ comment を投稿し、失敗時は stderr に出して False を返す。"""
    try:
        github_client.create_issue_comment(repository_full_name, number, body)
    except GitHubClientError as exc:
        print(f"{failure_label}に失敗しました: {exc}", file=sys.stderr)
        return False
    return True


def build_allow_edits_notice(
    allow_edits_notice_posted: bool,
    pr_info: GitHubPullRequest,
) -> str:
    """fork PR の maintainer edits 案内文を返す。"""
    if pr_info.maintainer_can_modify is True:
        return ""
    if allow_edits_notice_posted is True:
        return ""
    return _ALLOW_EDITS_NOTICE


def mark_allow_edits_notice_posted(
    current_value: bool,
    notice: str,
    posted: bool,
) -> bool:
    """maintainer edits 案内済み状態を投稿結果から更新する。"""
    if current_value is True:
        return True
    return notice != "" and posted is True
