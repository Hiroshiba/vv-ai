"""CLI と GitHub event payload を受ける入力モデル。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from vv_ai.config import ProviderName

CommandName = Literal[
    "confirm", "reply", "implement", "address", "review", "issue", "next",
    "requirements", "arch", "detail", "breakdown", "sync",
]
ControlLabelName = Literal["vv-ai:auto", "vv-ai:merge"]
EventName = Literal[
    "issue_comment", "workflow_dispatch", "issues", "pull_request", "local",
]
LabelAction = Literal["labeled", "unlabeled"]
SessionMode = Literal["inherit", "inherit_or_new", "compact", "new"]
TargetType = Literal["issue", "pr"]

_COMMAND_NAMES: set[str] = {
    "confirm", "reply", "implement", "address", "review", "issue", "next",
    "requirements", "arch", "detail", "breakdown", "sync",
}
_LABEL_COMMANDS: dict[str, CommandName] = {
    "vv-ai:reply": "reply",
    "vv-ai:confirm": "confirm",
    "vv-ai:requirements": "requirements",
    "vv-ai:arch": "arch",
    "vv-ai:detail": "detail",
    "vv-ai:breakdown": "breakdown",
    "vv-ai:implement": "implement",
    "vv-ai:address": "address",
    "vv-ai:review": "review",
    "vv-ai:issue": "issue",
    "vv-ai:next": "next",
    "vv-ai:sync": "sync",
}
_CONTROL_LABELS: set[ControlLabelName] = {"vv-ai:auto", "vv-ai:merge"}
_ISSUE_LABEL_COMMANDS: set[CommandName] = {
    "reply", "confirm", "requirements", "arch", "detail",
    "breakdown", "implement", "issue", "next",
}
_PULL_REQUEST_LABEL_COMMANDS: set[CommandName] = {
    "reply", "confirm", "requirements", "arch", "detail",
    "implement", "address", "review", "issue", "next", "sync",
}


class InputError(Exception):
    """入力の解釈に失敗したことを表す例外。"""


class CLIInput(BaseModel):
    """CLI から受ける生入力。"""

    model_config = ConfigDict(extra="forbid")

    event: EventName = "local"
    event_file: Path | None = None
    command: CommandName | None = None
    instruction: str | None = None
    target_url: str | None = None
    target_type: TargetType | None = None
    target_number: int | None = None
    provider: ProviderName | None = None
    session_mode: SessionMode | None = None
    dry_run: bool | None = None
    repo: str | None = None
    skip_api_key_check: bool = False


class GitHubRepository(BaseModel):
    """GitHub repository の最小表現。"""

    model_config = ConfigDict(extra="ignore")

    full_name: str


class GitHubUser(BaseModel):
    """GitHub user の最小表現。"""

    model_config = ConfigDict(extra="ignore")

    login: str
    id: int | None = None


class GitHubLabel(BaseModel):
    """GitHub label の最小表現。"""

    model_config = ConfigDict(extra="ignore")

    name: str


class IssueCommentTarget(BaseModel):
    """Issue comment event の対象。"""

    model_config = ConfigDict(extra="ignore")

    number: int
    updated_at: str | None = None
    pull_request: dict[str, Any] | None = None


class IssueCommentBody(BaseModel):
    """Issue comment の本文情報。"""

    model_config = ConfigDict(extra="ignore")

    id: int
    body: str
    user: GitHubUser


class IssueCommentEvent(BaseModel):
    """`issue_comment` event payload の必要最小限。"""

    model_config = ConfigDict(extra="ignore")

    action: str | None = None
    comment: IssueCommentBody
    issue: IssueCommentTarget
    repository: GitHubRepository
    sender: GitHubUser


class WorkflowDispatchEvent(BaseModel):
    """`workflow_dispatch` event payload の必要最小限。"""

    model_config = ConfigDict(extra="ignore")

    inputs: dict[str, Any] | None = None
    repository: GitHubRepository
    sender: GitHubUser


class IssueLabeledEvent(BaseModel):
    """`issues` labeled event payload の必要最小限。"""

    model_config = ConfigDict(extra="ignore")

    action: str | None = None
    issue: IssueCommentTarget
    label: GitHubLabel
    repository: GitHubRepository
    sender: GitHubUser


class PullRequestTarget(BaseModel):
    """Pull request event の対象。"""

    model_config = ConfigDict(extra="ignore")

    number: int
    updated_at: str | None = None
    merged: bool | None = None
    labels: list[GitHubLabel] = Field(default_factory=list)


class PullRequestLabelEvent(BaseModel):
    """`pull_request` label event payload の必要最小限。"""

    model_config = ConfigDict(extra="ignore")

    action: str | None = None
    pull_request: PullRequestTarget
    label: GitHubLabel
    repository: GitHubRepository
    sender: GitHubUser


class PullRequestEvent(BaseModel):
    """`pull_request` event payload の必要最小限。"""

    model_config = ConfigDict(extra="ignore")

    action: str | None = None
    pull_request: PullRequestTarget
    repository: GitHubRepository
    sender: GitHubUser


class RawInput(BaseModel):
    """後続の正規化処理に渡す共通の生入力。"""

    model_config = ConfigDict(extra="forbid")

    event_name: EventName
    command: CommandName | None = None
    control_label_name: ControlLabelName | None = None
    label_action: LabelAction | None = None
    instruction: str | None = None
    target_url: str | None = None
    target_type: TargetType | None = None
    target_number: int | None = None
    provider: ProviderName | None = None
    session_mode: SessionMode | None = None
    dry_run: bool = False
    repo: str | None = None
    skip_api_key_check: bool = False
    repository_full_name: str | None = None
    actor: str | None = None
    actor_id: int | None = None
    comment_id: int | None = None
    comment_author: str | None = None
    comment_body: str | None = None
    trigger_label_name: str | None = None
    trigger_event_created_at: str | None = None
    pull_request_merged: bool | None = None


class CommentInvocation(BaseModel):
    """`@vv-ai` コメント本文から抽出した生入力。"""

    model_config = ConfigDict(extra="forbid")

    command: CommandName = "reply"
    instruction: str | None = None
    provider: ProviderName | None = None
    session_mode: SessionMode | None = None
    dry_run: bool = False
    repo: str | None = None
