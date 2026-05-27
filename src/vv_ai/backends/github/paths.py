"""GitHub API path 構築と入力検証。"""

from __future__ import annotations

from urllib.parse import quote

from vv_ai.backends.github.models import GitHubClientError


def _build_issue_comment_reactions_path(
    repository_full_name: str,
    comment_id: int,
) -> str:
    """Issue comment reactions endpoint を返す。"""
    return (
        f"repos/{_require_repository_full_name(repository_full_name)}"
        f"/issues/comments/{_require_positive_id(comment_id, 'comment_id')}/reactions"
    )


def _build_issue_comments_path(repository_full_name: str, number: int) -> str:
    """Issue comments endpoint を返す。"""
    return (
        f"repos/{_require_repository_full_name(repository_full_name)}"
        f"/issues/{_require_positive_id(number, 'number')}/comments"
    )


def _build_issue_timeline_path(repository_full_name: str, number: int) -> str:
    """Issue timeline endpoint を返す。"""
    return (
        f"repos/{_require_repository_full_name(repository_full_name)}"
        f"/issues/{_require_positive_id(number, 'number')}/timeline"
    )


def _build_issues_path(repository_full_name: str) -> str:
    """Issues endpoint を返す。"""
    return f"repos/{_require_repository_full_name(repository_full_name)}/issues"


def _build_pulls_path(repository_full_name: str) -> str:
    """Pulls endpoint を返す。"""
    return f"repos/{_require_repository_full_name(repository_full_name)}/pulls"


def _build_repository_path(repository_full_name: str) -> str:
    """Repository endpoint を返す。"""
    return f"repos/{_require_repository_full_name(repository_full_name)}"


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


def _build_issue_label_path(
    repository_full_name: str,
    number: int,
    label_name: str,
) -> str:
    """Issue label endpoint を返す。"""
    encoded_label_name = quote(
        _require_non_empty_text(label_name, "label_name"),
        safe="",
    )
    return (
        f"{_build_issues_path(repository_full_name)}"
        f"/{_require_positive_id(number, 'number')}/labels/{encoded_label_name}"
    )


def _build_issue_labels_path(repository_full_name: str, number: int) -> str:
    """Issue labels endpoint を返す。"""
    return (
        f"{_build_issues_path(repository_full_name)}"
        f"/{_require_positive_id(number, 'number')}/labels"
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


def _require_non_empty_text(value: str, field_name: str) -> str:
    """空でない文字列を返す。"""
    if value.strip() == "":
        raise GitHubClientError(f"`{field_name}` は空文字にできません")
    return value


def _require_non_empty_optional_text(value: str | None, field_name: str) -> str:
    """None でない空でない文字列を返す。"""
    if value is None:
        raise GitHubClientError(f"`{field_name}` が見つかりません")
    return _require_non_empty_text(value, field_name)


def _require_mapping(value: object, field_name: str) -> dict[str, object]:
    """dict 形式の JSON object を返す。"""
    if not isinstance(value, dict):
        raise GitHubClientError(f"{field_name} の JSON 形式が不正です")
    return value


def _require_string(value: object, field_name: str) -> str:
    """空でない文字列を返す。"""
    if not isinstance(value, str) or value == "":
        raise GitHubClientError(f"{field_name} が不正です")
    return value
