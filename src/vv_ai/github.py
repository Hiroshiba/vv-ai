"""`gh` を使う GitHub 操作 API。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from vv_ai.resolve import ResolvedTarget

IssueState = Literal["OPEN", "CLOSED"]
PullRequestState = Literal["OPEN", "CLOSED", "MERGED"]
GitHubReactionContent = Literal["eyes", "confused"]
GhTextRunner = Callable[[Sequence[str]], str]


class GitHubClientError(Exception):
    """GitHub 操作に失敗したことを表す例外。"""


class GitHubActor(BaseModel):
    """GitHub 上の user を表す。"""

    model_config = ConfigDict(extra="forbid")

    login: str


class GitHubComment(BaseModel):
    """Issue comment を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    body: str
    author: GitHubActor
    created_at: str
    updated_at: str
    url: str


class GitHubReaction(BaseModel):
    """Issue comment reaction を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    content: GitHubReactionContent
    user: GitHubActor


class GitHubIssue(BaseModel):
    """GitHub Issue を表す。"""

    model_config = ConfigDict(extra="forbid")

    repository_full_name: str
    number: int
    title: str
    body: str
    state: IssueState
    author: GitHubActor
    url: str


class GitHubPullRequest(BaseModel):
    """GitHub Pull Request を表す。"""

    model_config = ConfigDict(extra="forbid")

    repository_full_name: str
    number: int
    title: str
    body: str
    state: PullRequestState
    author: GitHubActor
    url: str
    head_ref_name: str
    base_ref_name: str
    head_repository_full_name: str | None
    is_cross_repository: bool
    maintainer_can_modify: bool


type GitHubTargetDetails = GitHubIssue | GitHubPullRequest


class GitHubClient:
    """`gh` を使って GitHub 情報を操作する。"""

    def __init__(self, text_runner: GhTextRunner) -> None:
        self._text_runner = text_runner

    def get_issue(self, repository_full_name: str, number: int) -> GitHubIssue:
        """Issue を取得する。"""
        raw_issue = self._run_json(
            [
                "issue",
                "view",
                str(number),
                "--repo",
                repository_full_name,
                "--json",
                "number,title,body,state,author,url",
            ]
        )
        if not isinstance(raw_issue, dict):
            raise GitHubClientError("Issue 取得結果の JSON 形式が不正です")
        return _build_issue(repository_full_name, raw_issue)

    def get_pull_request(
        self,
        repository_full_name: str,
        number: int,
    ) -> GitHubPullRequest:
        """Pull Request を取得する。"""
        raw_pr = self._run_json(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repository_full_name,
                "--json",
                (
                    "number,title,body,state,author,url,headRefName,"
                    "baseRefName,headRepository,isCrossRepository,"
                    "maintainerCanModify"
                ),
            ]
        )
        if not isinstance(raw_pr, dict):
            raise GitHubClientError("Pull Request 取得結果の JSON 形式が不正です")
        return _build_pull_request(repository_full_name, raw_pr)

    def list_issue_comments(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubComment]:
        """Issue comment 一覧を取得する。"""
        payload = self._run_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository_full_name}/issues/{number}/comments",
            ]
        )
        if not isinstance(payload, list):
            raise GitHubClientError("コメント取得結果の JSON 形式が不正です")

        comments: list[GitHubComment] = []
        for page in payload:
            if not isinstance(page, list):
                raise GitHubClientError("コメント取得結果のページ形式が不正です")
            comments.extend(_build_comment_list(page))
        return comments

    def add_issue_comment_reaction(
        self,
        repository_full_name: str,
        comment_id: int,
        content: GitHubReactionContent,
    ) -> GitHubReaction:
        """Issue comment へ reaction を付与する。"""
        payload = self._run_json(
            [
                "api",
                "--method",
                "POST",
                _build_issue_comment_reactions_path(repository_full_name, comment_id),
                "-f",
                f"content={content}",
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("reaction 付与結果の JSON 形式が不正です")
        return _build_reaction(payload)

    def remove_issue_comment_reaction(
        self,
        repository_full_name: str,
        comment_id: int,
        reaction_id: int,
    ) -> None:
        """Issue comment から reaction を解除する。"""
        self._text_runner(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                _build_issue_comment_reaction_path(
                    repository_full_name,
                    comment_id,
                    reaction_id,
                ),
            ]
        )

    def get_target_details(self, target: ResolvedTarget) -> GitHubTargetDetails:
        """GitHub target に対応する本体を取得する。"""
        repository_full_name, number = _require_github_target(target)
        if target.kind == "issue":
            return self.get_issue(repository_full_name, number)
        if target.kind == "pr":
            return self.get_pull_request(repository_full_name, number)
        raise AssertionError(f"未対応の target kind です: {target.kind}")

    def _run_json(self, args: Sequence[str]) -> object:
        """`gh` の JSON 出力を読み込む。"""
        output = self._text_runner(["gh", *args])
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise GitHubClientError("`gh` の出力を JSON として解釈できませんでした") from exc


def build_github_client() -> GitHubClient:
    """標準の `gh` 実行器を使う client を返す。"""
    return GitHubClient(run_gh_text)


def run_gh_text(args: Sequence[str]) -> str:
    """`gh` を実行して標準出力を返す。"""
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise GitHubClientError(f"`gh` の実行に失敗しました: {exc}") from exc

    if completed.returncode == 0:
        return completed.stdout

    stderr = completed.stderr.strip()
    if stderr:
        raise GitHubClientError(f"`gh` の実行に失敗しました: {stderr}")
    raise GitHubClientError(
        f"`gh` の実行に失敗しました。終了コード: {completed.returncode}"
    )


def _require_github_target(target: ResolvedTarget) -> tuple[str, int]:
    """GitHub target に必要な識別子を返す。"""
    if target.backend != "github":
        raise GitHubClientError("GitHub client は GitHub target のみ扱えます")
    if target.repository_full_name is None:
        raise GitHubClientError("GitHub target に repository がありません")
    if target.number is None:
        raise GitHubClientError("GitHub target に番号がありません")
    return target.repository_full_name, target.number


def _build_issue(repository_full_name: str, raw_issue: dict[str, object]) -> GitHubIssue:
    """Issue JSON を model へ変換する。"""
    payload = {
        "repository_full_name": repository_full_name,
        "number": raw_issue.get("number"),
        "title": raw_issue.get("title"),
        "body": _coerce_text(raw_issue.get("body")),
        "state": raw_issue.get("state"),
        "author": _build_actor(raw_issue.get("author")),
        "url": raw_issue.get("url"),
    }
    return _validate_model(GitHubIssue, payload, "Issue")


def _build_pull_request(
    repository_full_name: str,
    raw_pr: dict[str, object],
) -> GitHubPullRequest:
    """Pull Request JSON を model へ変換する。"""
    payload = {
        "repository_full_name": repository_full_name,
        "number": raw_pr.get("number"),
        "title": raw_pr.get("title"),
        "body": _coerce_text(raw_pr.get("body")),
        "state": raw_pr.get("state"),
        "author": _build_actor(raw_pr.get("author")),
        "url": raw_pr.get("url"),
        "head_ref_name": raw_pr.get("headRefName"),
        "base_ref_name": raw_pr.get("baseRefName"),
        "head_repository_full_name": _build_head_repository_full_name(
            raw_pr.get("headRepository")
        ),
        "is_cross_repository": raw_pr.get("isCrossRepository"),
        "maintainer_can_modify": raw_pr.get("maintainerCanModify"),
    }
    return _validate_model(GitHubPullRequest, payload, "Pull Request")


def _build_comment_list(raw_comments: list[object]) -> list[GitHubComment]:
    """comment 配列 JSON を model 配列へ変換する。"""
    comments: list[GitHubComment] = []
    for raw_comment in raw_comments:
        if not isinstance(raw_comment, dict):
            raise GitHubClientError("コメント要素の JSON 形式が不正です")
        payload = {
            "id": raw_comment.get("id"),
            "body": _coerce_text(raw_comment.get("body")),
            "author": _build_rest_comment_author(raw_comment.get("user")),
            "created_at": raw_comment.get("created_at"),
            "updated_at": raw_comment.get("updated_at"),
            "url": raw_comment.get("html_url"),
        }
        comments.append(_validate_model(GitHubComment, payload, "コメント"))
    return comments


def _build_reaction(raw_reaction: dict[str, object]) -> GitHubReaction:
    """reaction JSON を model へ変換する。"""
    payload = {
        "id": raw_reaction.get("id"),
        "content": raw_reaction.get("content"),
        "user": _build_rest_comment_author(raw_reaction.get("user")),
    }
    return _validate_model(GitHubReaction, payload, "reaction")


def _build_actor(raw_actor: object) -> GitHubActor:
    """`gh issue view` 系の actor を変換する。"""
    if not isinstance(raw_actor, dict):
        raise GitHubClientError("author の JSON 形式が不正です")
    return _validate_model(
        GitHubActor,
        {"login": raw_actor.get("login")},
        "author",
    )


def _build_rest_comment_author(raw_user: object) -> GitHubActor:
    """REST comment user を変換する。"""
    if not isinstance(raw_user, dict):
        raise GitHubClientError("comment user の JSON 形式が不正です")
    return _validate_model(
        GitHubActor,
        {"login": raw_user.get("login")},
        "comment user",
    )


def _build_head_repository_full_name(raw_repo: object) -> str | None:
    """head repository 名を返す。"""
    if raw_repo is None:
        return None
    if not isinstance(raw_repo, dict):
        raise GitHubClientError("headRepository の JSON 形式が不正です")
    name_with_owner = raw_repo.get("nameWithOwner")
    if not isinstance(name_with_owner, str) or not name_with_owner:
        raise GitHubClientError("headRepository.nameWithOwner が不正です")
    return name_with_owner


def _build_issue_comment_reactions_path(
    repository_full_name: str,
    comment_id: int,
) -> str:
    """Issue comment reactions endpoint を返す。"""
    return (
        f"repos/{_require_repository_full_name(repository_full_name)}"
        f"/issues/comments/{_require_positive_id(comment_id, 'comment_id')}/reactions"
    )


def _build_issue_comment_reaction_path(
    repository_full_name: str,
    comment_id: int,
    reaction_id: int,
) -> str:
    """Issue comment reaction endpoint を返す。"""
    return (
        f"{_build_issue_comment_reactions_path(repository_full_name, comment_id)}"
        f"/{_require_positive_id(reaction_id, 'reaction_id')}"
    )


def _require_repository_full_name(repository_full_name: str) -> str:
    """org/repo 形式の repository 名を返す。"""
    if repository_full_name.count("/") != 1:
        raise GitHubClientError("repository_full_name は `org/repo` 形式で指定してください")
    owner, repo = repository_full_name.split("/")
    if owner == "" or repo == "":
        raise GitHubClientError("repository_full_name は `org/repo` 形式で指定してください")
    return repository_full_name


def _require_positive_id(value: int, field_name: str) -> int:
    """正の整数 ID を返す。"""
    if value <= 0:
        raise GitHubClientError(f"`{field_name}` は 1 以上である必要があります")
    return value


def _coerce_text(value: object) -> str:
    """nullable 文字列を通常文字列へそろえる。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raise GitHubClientError("文字列項目の JSON 形式が不正です")


def _validate_model[T: BaseModel](
    model_type: type[T],
    payload: dict[str, object],
    subject: str,
) -> T:
    """payload を Pydantic model として検証する。"""
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise GitHubClientError(f"{subject} 取得結果の値が不正です") from exc
