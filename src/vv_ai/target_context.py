"""GitHub target context の差分生成。"""

from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel, ConfigDict

from vv_ai.github import (
    GitHubClient,
    GitHubClientError,
    GitHubComment,
    GitHubIssue,
    GitHubPullRequest,
)
from vv_ai.resolve import ResolvedTarget
from vv_ai.session import SessionStateRef, TargetContextState


class TargetContextError(Exception):
    """target context の取得や構築に失敗したことを表す例外。"""


class TargetContextBuildResult(BaseModel):
    """今回 provider に渡す target context と保存すべき state。"""

    model_config = ConfigDict(extra="forbid")

    prompt_block: str | None
    state: TargetContextState


def empty_target_context_state() -> TargetContextState:
    """未投入の target context state を返す。"""
    return TargetContextState(
        title_hash=None,
        description_hash=None,
        comment_hashes={},
    )


def merge_target_context_state(
    state_ref: SessionStateRef,
    target_context_state: TargetContextState,
) -> SessionStateRef:
    """SessionStateRef に target context state を反映した値を返す。"""
    return state_ref.model_copy(
        update={"target_context_state": target_context_state},
        deep=True,
    )


def build_target_context(
    github_client: GitHubClient | None,
    target: ResolvedTarget | None,
    current_comment_id: int | None,
    previous_state: TargetContextState | None,
    preloaded_target: GitHubIssue | GitHubPullRequest | None,
) -> TargetContextBuildResult:
    """target context の未投入分を prompt block として返す。"""
    if target is None or target.backend != "github":
        state = previous_state
        if state is None:
            state = empty_target_context_state()
        return TargetContextBuildResult(
            prompt_block=None,
            state=state,
        )
    if github_client is None:
        raise TargetContextError("GitHub target context の取得に client が必要です")
    if target.repository_full_name is None or target.number is None:
        raise TargetContextError(
            "GitHub target context の取得に repository と番号が必要です"
        )

    state = previous_state
    if state is None:
        state = empty_target_context_state()
    next_state = state.model_copy(deep=True)

    try:
        title, description = _fetch_target_title_and_description(
            github_client,
            target,
            preloaded_target,
        )
        comments = github_client.list_issue_comments(
            target.repository_full_name,
            target.number,
        )
    except GitHubClientError as exc:
        raise TargetContextError(f"target context の取得に失敗しました: {exc}") from exc

    sections: list[str] = []
    title_hash = _hash_text(title)
    if state.title_hash != title_hash:
        sections.append(_format_title(title))
        next_state.title_hash = title_hash

    description_hash = _hash_text(description)
    if state.description_hash != description_hash:
        sections.append(_format_description(description))
        next_state.description_hash = description_hash

    for comment in _sort_comments(comments):
        comment_key = str(comment.id)
        comment_hash = _hash_comment(comment)
        if current_comment_id is not None and comment.id == current_comment_id:
            next_state.comment_hashes[comment_key] = comment_hash
            continue
        if state.comment_hashes.get(comment_key) == comment_hash:
            continue
        sections.append(_format_comment(comment))
        next_state.comment_hashes[comment_key] = comment_hash

    prompt_block = "\n\n---\n\n".join(sections) if len(sections) > 0 else None
    return TargetContextBuildResult(prompt_block=prompt_block, state=next_state)


def _fetch_target_title_and_description(
    github_client: GitHubClient,
    target: ResolvedTarget,
    preloaded_target: GitHubIssue | GitHubPullRequest | None,
) -> tuple[str, str]:
    """target の title と description を返す。"""
    if preloaded_target is not None:
        return preloaded_target.title, preloaded_target.body

    if target.repository_full_name is None or target.number is None:
        raise TargetContextError("target の取得に repository と番号が必要です")
    if target.kind == "issue":
        issue = github_client.get_issue(target.repository_full_name, target.number)
        return issue.title, issue.body
    if target.kind == "pr":
        pull_request = github_client.get_pull_request(
            target.repository_full_name,
            target.number,
        )
        return pull_request.title, pull_request.body
    raise TargetContextError("target kind が不正です")


def _sort_comments(comments: list[GitHubComment]) -> list[GitHubComment]:
    """comment を作成順で返す。"""
    return sorted(comments, key=lambda comment: (comment.created_at, comment.id))


def _hash_comment(comment: GitHubComment) -> str:
    """comment の投入済み判定に使う hash を返す。"""
    return _hash_text(f"{comment.updated_at}\n{comment.body}")


def _hash_text(value: str) -> str:
    """文字列の SHA-256 hash を返す。"""
    return sha256(value.encode("utf-8")).hexdigest()


def _format_title(title: str) -> str:
    """title 用の prompt section を返す。"""
    return f"## タイトル\n\n{title}"


def _format_description(description: str) -> str:
    """description 用の prompt section を返す。"""
    return f"## Description\n\n{description}"


def _format_comment(comment: GitHubComment) -> str:
    """comment 用の prompt section を返す。"""
    return (
        f"## コメント {comment.id}\n\n"
        f"- 作成者: {comment.author.login}\n"
        f"- 作成日時: {comment.created_at}\n"
        f"- 更新日時: {comment.updated_at}\n"
        f"- URL: {comment.url}\n\n"
        f"{comment.body}"
    )
