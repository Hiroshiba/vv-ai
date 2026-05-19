"""GitHub API payload 変換関数。"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from vv_ai.backends.github.models import (
    GitHubActor,
    GitHubArtifact,
    GitHubClientError,
    GitHubComment,
    GitHubIssue,
    GitHubIssueTimelineEvent,
    GitHubPullRequest,
    GitHubPullRequestSyncState,
    GitHubReaction,
    GitHubStatusCheckSummary,
    GitHubTree,
    GitHubTreeEntry,
    IssueState,
    PullRequestState,
)
from vv_ai.backends.github.paths import (
    _require_mapping,
    _require_repository_full_name,
    _require_string,
)


def _build_issue_parent_number(payload: dict[str, object]) -> int | None:
    """GraphQL Issue parent JSON から親 Issue 番号を返す。"""
    data = _require_mapping(payload.get("data"), "data")
    repository = _require_mapping(data.get("repository"), "repository")
    issue = _require_mapping(repository.get("issue"), "issue")
    if "parent" not in issue:
        raise GitHubClientError("issue.parent の取得に失敗しました")
    parent = issue["parent"]
    if parent is None:
        return None
    parent_payload = _require_mapping(parent, "issue.parent")
    parent_number = parent_payload.get("number")
    if type(parent_number) is not int:
        raise GitHubClientError("issue.parent.number が不正です")
    return parent_number


def _build_issue_timeline_event_page(
    payload: dict[str, object],
) -> tuple[list[GitHubIssueTimelineEvent], bool, str | None]:
    """GraphQL timeline page から next 履歴用 event 群を構築する。"""
    data = _require_mapping(payload.get("data"), "data")
    repository = _require_mapping(data.get("repository"), "repository")
    issue_or_pull_request = _require_mapping(
        repository.get("issueOrPullRequest"),
        "issueOrPullRequest",
    )
    timeline_items = _require_mapping(
        issue_or_pull_request.get("timelineItems"),
        "timelineItems",
    )
    raw_nodes = timeline_items.get("nodes")
    if not isinstance(raw_nodes, list):
        raise GitHubClientError("timelineItems.nodes の JSON 形式が不正です")
    page_info = _require_mapping(timeline_items.get("pageInfo"), "pageInfo")
    has_next_page = page_info.get("hasNextPage")
    if type(has_next_page) is not bool:
        raise GitHubClientError("timelineItems.pageInfo.hasNextPage が不正です")
    end_cursor = page_info.get("endCursor")
    if end_cursor is not None and not isinstance(end_cursor, str):
        raise GitHubClientError("timelineItems.pageInfo.endCursor が不正です")

    return (
        [_build_issue_timeline_event(raw_node) for raw_node in raw_nodes],
        has_next_page,
        end_cursor,
    )


def _build_artifact_page(raw_page: dict[str, object]) -> list[GitHubArtifact]:
    """artifact 一覧ページから artifact 群を構築する。"""
    raw_artifacts = raw_page.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise GitHubClientError("artifact 一覧取得結果の `artifacts` が不正です")
    return [_build_artifact(raw_artifact) for raw_artifact in raw_artifacts]


def _build_artifact(raw_artifact: object) -> GitHubArtifact:
    """artifact JSON を model へ変換する。"""
    if not isinstance(raw_artifact, dict):
        raise GitHubClientError("artifact 一覧の要素形式が不正です")
    try:
        return GitHubArtifact.model_validate(
            {
                "id": raw_artifact["id"],
                "name": raw_artifact["name"],
                "created_at": raw_artifact["created_at"],
                "archive_download_url": raw_artifact["archive_download_url"],
            }
        )
    except KeyError as exc:
        raise GitHubClientError(
            f"artifact 一覧の必須項目が不足しています: {exc.args[0]}"
        ) from exc
    except ValidationError as exc:
        raise GitHubClientError("artifact 一覧の値が不正です") from exc


def _build_tree(payload: dict[str, object]) -> GitHubTree:
    """git tree JSON を model へ変換する。"""
    raw_tree = payload.get("tree")
    if not isinstance(raw_tree, list):
        raise GitHubClientError("git tree 取得結果の `tree` が不正です")
    truncated = payload.get("truncated")
    if not isinstance(truncated, bool):
        raise GitHubClientError("git tree 取得結果の `truncated` が不正です")
    entries: list[GitHubTreeEntry] = []
    for raw_entry in raw_tree:
        if not isinstance(raw_entry, dict):
            raise GitHubClientError("git tree entry の JSON 形式が不正です")
        entries.append(
            _validate_model(
                GitHubTreeEntry,
                {
                    "path": raw_entry.get("path"),
                    "type": raw_entry.get("type"),
                    "sha": raw_entry.get("sha"),
                },
                "git tree entry",
            )
        )
    return GitHubTree(tree=entries, truncated=truncated)


def _decode_blob(payload: dict[str, object]) -> bytes:
    """git blob JSON を bytes へ変換する。"""
    encoding = payload.get("encoding")
    if encoding != "base64":
        raise GitHubClientError(f"git blob encoding が未対応です: {encoding}")
    content = payload.get("content")
    if not isinstance(content, str) or content == "":
        raise GitHubClientError("git blob content が不正です")
    try:
        normalized_content = "".join(content.split())
        return base64.b64decode(normalized_content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GitHubClientError("git blob content の base64 が不正です") from exc


def _artifact_sort_key(artifact: GitHubArtifact) -> tuple[datetime, int]:
    """artifact の新しさ比較に使うキーを返す。"""
    return (_parse_github_datetime(artifact.created_at), artifact.id)


def _parse_github_datetime(value: str) -> datetime:
    """GitHub の UTC timestamp を比較可能な datetime に直す。"""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise GitHubClientError(f"artifact の日時形式が不正です: {value}") from exc


def _build_issue_from_rest(
    repository_full_name: str,
    raw_issue: dict[str, object],
) -> GitHubIssue:
    """REST Issue JSON を model へ変換する。"""
    payload = {
        "id": raw_issue.get("id"),
        "repository_full_name": repository_full_name,
        "number": raw_issue.get("number"),
        "title": raw_issue.get("title"),
        "body": _coerce_text(raw_issue.get("body")),
        "state": _normalize_issue_state(raw_issue.get("state")),
        "author": _build_rest_user(raw_issue.get("user")),
        "url": raw_issue.get("html_url"),
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


def _build_pull_request_sync_state(
    raw_state: dict[str, object],
) -> GitHubPullRequestSyncState:
    """Pull Request sync 状態 JSON を model へ変換する。"""
    payload = {
        "mergeable": _require_string(raw_state.get("mergeable"), "mergeable"),
        "merge_state_status": _require_string(
            raw_state.get("mergeStateStatus"),
            "mergeStateStatus",
        ),
        "status_check_summary": _build_status_check_summary(
            raw_state.get("statusCheckRollup")
        ),
    }
    return _validate_model(GitHubPullRequestSyncState, payload, "Pull Request sync 状態")


def _build_status_check_summary(raw_rollup: object) -> GitHubStatusCheckSummary:
    """statusCheckRollup JSON を集計 model へ変換する。"""
    if not isinstance(raw_rollup, list):
        raise GitHubClientError("statusCheckRollup の JSON 形式が不正です")
    counts = {
        "success_count": 0,
        "failure_count": 0,
        "pending_count": 0,
        "unknown_count": 0,
    }
    for raw_check in raw_rollup:
        if not isinstance(raw_check, dict):
            raise GitHubClientError("statusCheckRollup の要素形式が不正です")
        counts[_classify_status_check(raw_check)] += 1
    return _validate_model(
        GitHubStatusCheckSummary,
        counts,
        "status check 集計",
    )


def _classify_status_check(raw_check: dict[str, object]) -> str:
    """status check JSON を集計区分へ変換する。"""
    state = raw_check.get("state")
    if isinstance(state, str):
        return _classify_status_context_state(state)
    status = raw_check.get("status")
    conclusion = raw_check.get("conclusion")
    if isinstance(status, str):
        return _classify_check_run_status(status, conclusion)
    return "unknown_count"


def _classify_status_context_state(state: str) -> str:
    """StatusContext の state を集計区分へ変換する。"""
    if state == "SUCCESS":
        return "success_count"
    if state in {"ERROR", "FAILURE"}:
        return "failure_count"
    if state in {"EXPECTED", "PENDING"}:
        return "pending_count"
    return "unknown_count"


def _classify_check_run_status(status: str, conclusion: object) -> str:
    """CheckRun の status と conclusion を集計区分へ変換する。"""
    if status != "COMPLETED":
        if status in {
            "ACTION_REQUIRED",
            "IN_PROGRESS",
            "PENDING",
            "QUEUED",
            "REQUESTED",
            "WAITING",
        }:
            return "pending_count"
        return "unknown_count"
    if not isinstance(conclusion, str):
        return "unknown_count"
    if conclusion in {"NEUTRAL", "SKIPPED", "SUCCESS"}:
        return "success_count"
    if conclusion in {
        "ACTION_REQUIRED",
        "CANCELLED",
        "FAILURE",
        "STARTUP_FAILURE",
        "TIMED_OUT",
    }:
        return "failure_count"
    return "unknown_count"


def _build_pull_request_from_rest(
    repository_full_name: str,
    raw_pr: dict[str, object],
) -> GitHubPullRequest:
    """REST Pull Request JSON を model へ変換する。"""
    head = _require_mapping(raw_pr.get("head"), "head")
    payload = {
        "repository_full_name": repository_full_name,
        "number": raw_pr.get("number"),
        "title": raw_pr.get("title"),
        "body": _coerce_text(raw_pr.get("body")),
        "state": _normalize_pull_request_state(raw_pr.get("state")),
        "author": _build_rest_user(raw_pr.get("user")),
        "url": raw_pr.get("html_url"),
        "head_ref_name": head.get("ref"),
        "base_ref_name": _build_pull_request_base_ref_name(raw_pr.get("base")),
        "head_repository_full_name": _build_pull_request_head_repository_full_name(head),
        "is_cross_repository": _build_is_cross_repository(repository_full_name, head),
        "maintainer_can_modify": raw_pr.get("maintainer_can_modify"),
    }
    return _validate_model(GitHubPullRequest, payload, "Pull Request")


def _build_comment_list(raw_comments: list[object]) -> list[GitHubComment]:
    """comment 配列 JSON を model 配列へ変換する。"""
    comments: list[GitHubComment] = []
    for raw_comment in raw_comments:
        comments.append(_build_comment(raw_comment))
    return comments


def _build_comment(raw_comment: object) -> GitHubComment:
    """comment JSON を model へ変換する。"""
    if not isinstance(raw_comment, dict):
        raise GitHubClientError("コメント要素の JSON 形式が不正です")
    payload = {
        "id": raw_comment.get("id"),
        "body": _coerce_text(raw_comment.get("body")),
        "author": _build_rest_user(raw_comment.get("user")),
        "created_at": raw_comment.get("created_at"),
        "updated_at": raw_comment.get("updated_at"),
        "url": raw_comment.get("html_url"),
    }
    return _validate_model(GitHubComment, payload, "コメント")


def _build_issue_timeline_event(raw_event: object) -> GitHubIssueTimelineEvent:
    """GraphQL timeline event JSON を next 履歴用 model へ変換する。"""
    if not isinstance(raw_event, dict):
        raise GitHubClientError("timeline event 要素の JSON 形式が不正です")
    typename = raw_event.get("__typename")
    if typename == "IssueComment":
        return _build_issue_commented_timeline_event(raw_event)
    if typename == "LabeledEvent":
        return _build_issue_labeled_timeline_event(raw_event)
    if typename == "SubIssueAddedEvent":
        return _build_sub_issue_added_timeline_event(raw_event)
    if typename == "CrossReferencedEvent":
        return _build_cross_referenced_timeline_event(raw_event)
    raise GitHubClientError(f"未対応の timeline event 種別です: {typename}")


def _build_issue_commented_timeline_event(
    raw_event: dict[str, object],
) -> GitHubIssueTimelineEvent:
    """IssueComment event JSON を next 履歴用 model へ変換する。"""
    payload = {
        "id": raw_event.get("id"),
        "event": "commented",
        "actor": _build_actor(raw_event.get("author")),
        "created_at": raw_event.get("createdAt"),
        "comment_database_id": raw_event.get("databaseId"),
        "body": _coerce_text(raw_event.get("body")),
        "label_name": None,
        "source_kind": None,
        "source_number": None,
        "source_repository_full_name": None,
    }
    return _validate_model(GitHubIssueTimelineEvent, payload, "IssueComment event")


def _build_issue_labeled_timeline_event(
    raw_event: dict[str, object],
) -> GitHubIssueTimelineEvent:
    """LabeledEvent JSON を next 履歴用 model へ変換する。"""
    label = _require_mapping(raw_event.get("label"), "label")
    payload = {
        "id": raw_event.get("id"),
        "event": "labeled",
        "actor": _build_actor(raw_event.get("actor")),
        "created_at": raw_event.get("createdAt"),
        "comment_database_id": None,
        "body": None,
        "label_name": label.get("name"),
        "source_kind": None,
        "source_number": None,
        "source_repository_full_name": None,
    }
    return _validate_model(GitHubIssueTimelineEvent, payload, "LabeledEvent")


def _build_sub_issue_added_timeline_event(
    raw_event: dict[str, object],
) -> GitHubIssueTimelineEvent:
    """SubIssueAddedEvent JSON を next 履歴用 model へ変換する。"""
    sub_issue = _require_mapping(raw_event.get("subIssue"), "subIssue")
    payload = {
        "id": raw_event.get("id"),
        "event": "sub_issue_added",
        "actor": _build_optional_actor(raw_event.get("actor")),
        "created_at": raw_event.get("createdAt"),
        "comment_database_id": None,
        "body": None,
        "label_name": None,
        "source_kind": "issue",
        "source_number": sub_issue.get("number"),
        "source_repository_full_name": None,
    }
    return _validate_model(GitHubIssueTimelineEvent, payload, "SubIssueAddedEvent")


def _build_cross_referenced_timeline_event(
    raw_event: dict[str, object],
) -> GitHubIssueTimelineEvent:
    """CrossReferencedEvent JSON を next 履歴用 model へ変換する。"""
    source = _require_mapping(raw_event.get("source"), "source")
    source_kind = _build_referenced_source_kind(source.get("__typename"))
    repository = _require_mapping(source.get("repository"), "source.repository")
    payload = {
        "id": raw_event.get("id"),
        "event": "cross_referenced",
        "actor": _build_optional_actor(raw_event.get("actor")),
        "created_at": raw_event.get("createdAt"),
        "comment_database_id": None,
        "body": None,
        "label_name": None,
        "source_kind": source_kind,
        "source_number": source.get("number"),
        "source_repository_full_name": repository.get("nameWithOwner"),
    }
    return _validate_model(GitHubIssueTimelineEvent, payload, "CrossReferencedEvent")


def _build_referenced_source_kind(typename: object) -> str:
    """ReferencedSubject の typename を source 種別へ変換する。"""
    if typename == "Issue":
        return "issue"
    if typename == "PullRequest":
        return "pull_request"
    raise GitHubClientError(f"未対応の cross reference source 種別です: {typename}")


def _build_reaction(raw_reaction: dict[str, object]) -> GitHubReaction:
    """reaction JSON を model へ変換する。"""
    payload = {
        "id": raw_reaction.get("id"),
        "content": raw_reaction.get("content"),
        "user": _build_rest_user(raw_reaction.get("user")),
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


def _build_optional_actor(raw_actor: object) -> GitHubActor | None:
    """nullable actor を変換する。"""
    if raw_actor is None:
        return None
    return _build_actor(raw_actor)


def _build_rest_user(raw_user: object) -> GitHubActor:
    """REST user を変換する。"""
    if not isinstance(raw_user, dict):
        raise GitHubClientError("REST user の JSON 形式が不正です")
    return _validate_model(
        GitHubActor,
        {"login": raw_user.get("login")},
        "REST user",
    )


def _build_pull_request_base_ref_name(raw_base: object) -> str:
    """REST Pull Request base.ref を返す。"""
    base = _require_mapping(raw_base, "base")
    return _require_string(base.get("ref"), "base.ref")


def _build_pull_request_head_repository_full_name(raw_head: dict[str, object]) -> str:
    """REST Pull Request head.repo.full_name を返す。"""
    raw_repo = raw_head.get("repo")
    repo = _require_mapping(raw_repo, "head.repo")
    return _require_string(repo.get("full_name"), "head.repo.full_name")


def _build_is_cross_repository(
    repository_full_name: str,
    raw_head: dict[str, object],
) -> bool:
    """REST Pull Request の cross repository 判定を返す。"""
    head_repository_full_name = _build_pull_request_head_repository_full_name(raw_head)
    return head_repository_full_name != _require_repository_full_name(
        repository_full_name
    )


def _normalize_issue_state(raw_state: object) -> IssueState:
    """REST Issue state を model 用の値へ変換する。"""
    normalized = _require_string(raw_state, "state").upper()
    if normalized not in {"OPEN", "CLOSED"}:
        raise GitHubClientError(f"Issue state が不正です: {raw_state}")
    return normalized


def _normalize_pull_request_state(raw_state: object) -> PullRequestState:
    """REST Pull Request state を model 用の値へ変換する。"""
    normalized = _require_string(raw_state, "state").upper()
    if normalized not in {"OPEN", "CLOSED", "MERGED"}:
        raise GitHubClientError(f"Pull Request state が不正です: {raw_state}")
    return normalized


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
