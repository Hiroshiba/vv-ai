"""GitHub comment 投稿の共通処理。"""

from __future__ import annotations

import sys

from vv_ai.execution import ExecutionResult
from vv_ai.github import GitHubClient, GitHubClientError, GitHubPullRequest
from vv_ai.preflight import ReadyExecution


def post_issue_comment_safely(
    github_client: GitHubClient,
    repository_full_name: str,
    number: int,
    body: str,
    failure_label: str,
) -> bool:
    """Issue または PR への comment 投稿を試み、成否を返す。"""
    try:
        github_client.create_issue_comment(repository_full_name, number, body)
        return True
    except GitHubClientError as exc:
        print(f"{failure_label}に失敗しました: {exc}", file=sys.stderr)
        return False


def build_allow_edits_notice(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    pr_info: GitHubPullRequest,
) -> str:
    """fork PR で maintainer edits を促す案内文を返す。"""
    notice_already_posted = _get_allow_edits_notice_posted(ready_execution)
    if notice_already_posted:
        execution_result.allow_edits_notice_posted = True
    if pr_info.maintainer_can_modify:
        return ""
    if notice_already_posted:
        return ""
    return (
        "\n\n---\n"
        '**Note**: この PR で "Allow edits from maintainers" を有効にすると、'
        "次回以降 vv-ai が直接修正をプッシュできるようになります。"
        "PR の右サイドバー下部にあるチェックボックスから設定できます。"
    )


def mark_allow_edits_notice_posted(
    execution_result: ExecutionResult,
    notice: str,
) -> None:
    """allow edits 案内を実際に投稿できた場合だけ投稿済みとして記録する。"""
    if notice != "":
        execution_result.allow_edits_notice_posted = True


def _get_allow_edits_notice_posted(ready_execution: ReadyExecution) -> bool:
    """復元済み session から allow_edits_notice_posted を取得する。"""
    session = ready_execution.resolved_session
    if session is None:
        return False
    return session.allow_edits_notice_posted
