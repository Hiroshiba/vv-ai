"""GitHub backend のモデル。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

IssueState = Literal["OPEN", "CLOSED"]
PullRequestState = Literal["OPEN", "CLOSED", "MERGED"]
GitHubReactionContent = Literal["eyes", "confused"]
GitHubIssueTimelineEventName = Literal[
    "commented",
    "labeled",
    "sub_issue_added",
    "cross_referenced",
]
GitHubIssueTimelineSourceKind = Literal["issue", "pull_request"]


class GitHubClientError(Exception):
    """GitHub 操作に失敗したことを表す例外。"""


class RepoInfo:
    """リポジトリの基本情報。"""

    def __init__(
        self,
        is_fork: bool,
        parent_full_name: str | None,
        parent_default_branch: str | None,
    ) -> None:
        self.is_fork = is_fork
        self.parent_full_name = parent_full_name
        self.parent_default_branch = parent_default_branch


class GitHubActor(BaseModel):
    """GitHub 上の user を表す。"""

    model_config = ConfigDict(extra="forbid")

    login: str
    database_id: int | None = None
    actor_type: str | None = None


class GitHubComment(BaseModel):
    """Issue comment を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    body: str
    author: GitHubActor
    created_at: str
    updated_at: str
    url: str


class GitHubPullRequestReview(BaseModel):
    """Pull Request review submission を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    body: str
    author: GitHubActor
    created_at: str
    url: str


class GitHubIssueLabeledEvent(BaseModel):
    """Issue timeline の labeled event を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int | None
    label_name: str
    actor: GitHubActor
    created_at: str


class GitHubIssueTimelineEvent(BaseModel):
    """Issue timeline の next 履歴用 event を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int | None
    event: GitHubIssueTimelineEventName
    actor: GitHubActor
    created_at: str
    body: str | None = None
    label_name: str | None = None
    comment_database_id: int | None = None
    source_kind: GitHubIssueTimelineSourceKind | None = None
    source_number: int | None = None
    source_repository_full_name: str | None = None


class GitHubReaction(BaseModel):
    """Issue comment reaction を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    content: GitHubReactionContent
    user: GitHubActor


class GitHubIssue(BaseModel):
    """GitHub Issue を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    repository_full_name: str
    number: int
    title: str
    body: str
    state: IssueState
    author: GitHubActor
    url: str


class GitHubIssueReference(BaseModel):
    """GitHub Issue 参照を表す。"""

    model_config = ConfigDict(extra="forbid")

    repository_full_name: str
    number: int


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


class GitHubPullRequestClosingState(BaseModel):
    """Pull Request の merge 状態と close 対象 Issue を表す。"""

    model_config = ConfigDict(extra="forbid")

    merged: bool
    closing_issue_references: list[GitHubIssueReference]


class GitHubStatusCheckSummary(BaseModel):
    """PR status check の集計を表す。"""

    model_config = ConfigDict(extra="forbid")

    success_count: int
    failure_count: int
    pending_count: int
    unknown_count: int


class GitHubPullRequestSyncState(BaseModel):
    """sync が参照する Pull Request 状態を表す。"""

    model_config = ConfigDict(extra="forbid")

    mergeable: str
    merge_state_status: str
    status_check_summary: GitHubStatusCheckSummary


class GitHubArtifact(BaseModel):
    """GitHub Actions artifact を表す。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    created_at: str
    archive_download_url: str


class GitHubTreeEntry(BaseModel):
    """GitHub git tree の entry を表す。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    type: str
    sha: str


class GitHubTree(BaseModel):
    """GitHub git tree の取得結果を表す。"""

    model_config = ConfigDict(extra="forbid")

    tree: list[GitHubTreeEntry]
    truncated: bool


type GitHubTargetDetails = GitHubIssue | GitHubPullRequest
