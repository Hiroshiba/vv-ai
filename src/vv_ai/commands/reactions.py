"""GitHub reaction の付与と終了処理。"""

from __future__ import annotations

import sys

from vv_ai.backends.github.client import GitHubClient
from vv_ai.backends.github.models import GitHubClientError, GitHubReactionContent


def add_reaction_safely(
    github_client: GitHubClient,
    repository_full_name: str,
    comment_id: int,
    content: GitHubReactionContent,
) -> int | None:
    """reaction を付与し、reaction_id を返す。失敗時は None を返す。"""
    try:
        reaction = github_client.add_issue_comment_reaction(
            repository_full_name, comment_id, content
        )
        return reaction.id
    except GitHubClientError as exc:
        print(f"reaction 付与に失敗しました: {exc}", file=sys.stderr)
        return None


def finalize_reactions(
    github_client: GitHubClient,
    repository_full_name: str,
    comment_id: int,
    eyes_reaction_id: int | None,
    status: str,
) -> None:
    """eyes を除去し、失敗時は confused を付与する。"""
    if eyes_reaction_id is not None:
        try:
            github_client.remove_issue_comment_reaction(
                repository_full_name, comment_id, eyes_reaction_id
            )
        except GitHubClientError as exc:
            print(f"eyes reaction 除去に失敗しました: {exc}", file=sys.stderr)

    if status != "success":
        add_reaction_safely(github_client, repository_full_name, comment_id, "confused")
