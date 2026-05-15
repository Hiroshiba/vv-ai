"""`gh` を使う GitHub 操作 API。"""

from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, ValidationError

from vv_ai.resolve import ResolvedTarget

IssueState = Literal["OPEN", "CLOSED"]
PullRequestState = Literal["OPEN", "CLOSED", "MERGED"]
GitHubReactionContent = Literal["eyes", "confused"]
GhTextRunner = Callable[[Sequence[str]], str]
GhBinaryRunner = Callable[[Sequence[str]], bytes]


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

    id: int
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


class GitHubClient:
    """`gh` を使って GitHub 情報を操作する。"""

    def __init__(
        self,
        text_runner: GhTextRunner,
        binary_runner: GhBinaryRunner,
    ) -> None:
        self._text_runner = text_runner
        self._binary_runner = binary_runner

    def get_issue(self, repository_full_name: str, number: int) -> GitHubIssue:
        """Issue を取得する。"""
        raw_issue = self._run_json(
            [
                "api",
                f"{_build_issues_path(repository_full_name)}/{_require_positive_id(number, 'number')}",
            ]
        )
        if not isinstance(raw_issue, dict):
            raise GitHubClientError("Issue 取得結果の JSON 形式が不正です")
        return _build_issue_from_rest(repository_full_name, raw_issue)

    def get_issue_parent_number(
        self,
        repository_full_name: str,
        number: int,
    ) -> int | None:
        """Issue の親 Issue 番号を返す。"""
        owner, repo = _require_repository_full_name(repository_full_name).split("/")
        query = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      parent {
        number
      }
    }
  }
}
""".strip()
        payload = self._run_json(
            [
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={repo}",
                "-F",
                f"number={_require_positive_id(number, 'number')}",
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("Issue 親番号取得結果の JSON 形式が不正です")
        return _build_issue_parent_number(payload)

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

    def create_issue_comment(
        self,
        repository_full_name: str,
        number: int,
        body: str,
    ) -> GitHubComment:
        """Issue または PR へ comment を投稿する。"""
        payload = self._run_json(
            [
                "api",
                "--method",
                "POST",
                _build_issue_comments_path(repository_full_name, number),
                "-f",
                f"body={_require_non_empty_text(body, 'body')}",
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("コメント作成結果の JSON 形式が不正です")
        return _build_comment(payload)

    def create_issue(
        self,
        repository_full_name: str,
        title: str,
        body: str,
    ) -> GitHubIssue:
        """Issue を作成する。"""
        payload = self._run_json(
            [
                "api",
                "--method",
                "POST",
                _build_issues_path(repository_full_name),
                "-f",
                f"title={_require_non_empty_text(title, 'title')}",
                "-f",
                f"body={body}",
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("Issue 作成結果の JSON 形式が不正です")
        return _build_issue_from_rest(repository_full_name, payload)

    def add_sub_issue(
        self,
        repository_full_name: str,
        parent_number: int,
        child_issue_id: int,
    ) -> None:
        """親 Issue にサブ Issue を紐付ける。"""
        self._run_json(
            [
                "api",
                "--method",
                "POST",
                (
                    f"repos/{_require_repository_full_name(repository_full_name)}"
                    f"/issues/{_require_positive_id(parent_number, 'parent_number')}/sub_issues"
                ),
                "-F",
                f"sub_issue_id={_require_positive_id(child_issue_id, 'child_issue_id')}",
            ]
        )

    def create_pull_request(
        self,
        repository_full_name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        maintainer_can_modify: bool,
    ) -> GitHubPullRequest:
        """Pull Request を作成する。"""
        payload = self._run_json(
            [
                "api",
                "--method",
                "POST",
                _build_pulls_path(repository_full_name),
                "-f",
                f"title={_require_non_empty_text(title, 'title')}",
                "-f",
                f"body={body}",
                "-f",
                f"head={_require_non_empty_text(head_branch, 'head_branch')}",
                "-f",
                f"base={_require_non_empty_text(base_branch, 'base_branch')}",
                "-F",
                f"maintainer_can_modify={'true' if maintainer_can_modify else 'false'}",
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("Pull Request 作成結果の JSON 形式が不正です")
        return _build_pull_request_from_rest(repository_full_name, payload)

    def list_repository_artifacts(
        self,
        repository_full_name: str,
    ) -> list[GitHubArtifact]:
        """repository 全体の artifact 一覧を取得する。"""
        payload = self._run_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository_full_name}/actions/artifacts?per_page=100",
            ]
        )
        if not isinstance(payload, list):
            raise GitHubClientError("artifact 一覧取得結果の JSON 形式が不正です")

        artifacts: list[GitHubArtifact] = []
        for page in payload:
            if not isinstance(page, dict):
                raise GitHubClientError("artifact 一覧取得結果のページ形式が不正です")
            artifacts.extend(_build_artifact_page(page))
        return artifacts

    def find_latest_repository_artifact_by_prefix(
        self,
        repository_full_name: str,
        prefix: str,
    ) -> GitHubArtifact | None:
        """prefix に一致する最新 artifact を返す。"""
        matches = [
            artifact
            for artifact in self.list_repository_artifacts(repository_full_name)
            if artifact.name.startswith(prefix)
        ]
        if not matches:
            return None
        matches.sort(key=_artifact_sort_key, reverse=True)
        return matches[0]

    def download_repository_artifact(
        self,
        repository_full_name: str,
        artifact_id: int,
        destination_path: Path,
    ) -> None:
        """artifact zip を file として保存する。"""
        if artifact_id <= 0:
            raise GitHubClientError("artifact_id は 1 以上である必要があります")
        if destination_path.exists():
            raise GitHubClientError(f"`{destination_path}` は既に存在します")
        try:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._binary_runner(
                [
                    "gh",
                    "api",
                    f"repos/{repository_full_name}/actions/artifacts/{artifact_id}/zip",
                ]
            )
            destination_path.write_bytes(payload)
        except OSError as exc:
            raise GitHubClientError(
                f"`{destination_path}` への artifact 保存に失敗しました"
            ) from exc

    def get_repository_tree(
        self,
        repository_full_name: str,
        ref: str,
    ) -> GitHubTree:
        """repository の recursive git tree を取得する。"""
        payload = self._run_json(
            [
                "api",
                (
                    f"repos/{_require_repository_full_name(repository_full_name)}"
                    f"/git/trees/{_require_non_empty_text(ref, 'ref')}?recursive=1"
                ),
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("git tree 取得結果の JSON 形式が不正です")
        return _build_tree(payload)

    def get_repository_blob(
        self,
        repository_full_name: str,
        sha: str,
    ) -> bytes:
        """repository の git blob を bytes で取得する。"""
        payload = self._run_json(
            [
                "api",
                (
                    f"repos/{_require_repository_full_name(repository_full_name)}"
                    f"/git/blobs/{_require_non_empty_text(sha, 'sha')}"
                ),
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("git blob 取得結果の JSON 形式が不正です")
        return _decode_blob(payload)

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

    def remove_issue_label(
        self,
        repository_full_name: str,
        number: int,
        label_name: str,
    ) -> None:
        """Issue または PR から label を削除する。"""
        self._text_runner(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                _build_issue_label_path(repository_full_name, number, label_name),
            ]
        )

    def get_default_branch(self, repository_full_name: str) -> str:
        """リポジトリのデフォルトブランチ名を返す。"""
        raw = self._run_json(
            [
                "repo",
                "view",
                repository_full_name,
                "--json",
                "defaultBranchRef",
            ]
        )
        if not isinstance(raw, dict):
            raise GitHubClientError("リポジトリ情報の JSON 形式が不正です")
        ref = raw.get("defaultBranchRef")
        if not isinstance(ref, dict):
            raise GitHubClientError("defaultBranchRef の取得に失敗しました")
        name = ref.get("name")
        if not isinstance(name, str) or not name:
            raise GitHubClientError("デフォルトブランチ名の取得に失敗しました")
        return name

    def get_repo_info(self, repository_full_name: str) -> RepoInfo:
        """リポジトリの fork 情報を返す。"""
        raw = self._run_json(["api", _build_repository_path(repository_full_name)])
        if not isinstance(raw, dict):
            raise GitHubClientError("リポジトリ情報の JSON 形式が不正です")
        is_fork = raw.get("fork")
        if not isinstance(is_fork, bool):
            raise GitHubClientError("fork の取得に失敗しました")
        if not is_fork:
            return RepoInfo(is_fork=False, parent_full_name=None, parent_default_branch=None)
        parent = _require_mapping(raw.get("parent"), "parent")
        parent_full_name = _require_string(parent.get("full_name"), "parent.full_name")
        parent_default_branch = _require_string(
            parent.get("default_branch"), "parent.default_branch"
        )
        return RepoInfo(
            is_fork=True,
            parent_full_name=parent_full_name,
            parent_default_branch=parent_default_branch,
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
    return GitHubClient(run_gh_text, run_gh_binary)


def build_github_client_with_token(token: str) -> GitHubClient:
    """指定 token を GH_TOKEN として使う client を返す。"""
    if token.strip() == "":
        raise GitHubClientError("GitHub token が空です")
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    env.pop("GITHUB_TOKEN", None)
    return GitHubClient(
        lambda args: run_gh_text_with_env(args, env),
        lambda args: run_gh_binary_with_env(args, env),
    )


def run_gh_text(args: Sequence[str]) -> str:
    """`gh` を実行して標準出力を返す。"""
    return run_gh_text_with_env(args, os.environ)


def run_gh_text_with_env(args: Sequence[str], env: Mapping[str, str]) -> str:
    """指定 env で `gh` を実行して標準出力を返す。"""
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
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


def run_gh_binary(args: Sequence[str]) -> bytes:
    """`gh` を実行して標準出力 bytes を返す。"""
    return run_gh_binary_with_env(args, os.environ)


def run_gh_binary_with_env(args: Sequence[str], env: Mapping[str, str]) -> bytes:
    """指定 env で `gh` を実行して標準出力 bytes を返す。"""
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=False,
            env=env,
        )
    except OSError as exc:
        raise GitHubClientError(f"`gh` の実行に失敗しました: {exc}") from exc

    if completed.returncode == 0:
        return completed.stdout

    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
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
