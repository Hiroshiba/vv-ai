"""`gh` を使う GitHub 操作 API。"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

from vv_ai.backends.github.models import (
    GitHubArtifact,
    GitHubClientError,
    GitHubComment,
    GitHubIssue,
    GitHubIssueLabeledEvent,
    GitHubIssueTimelineEvent,
    GitHubPullRequest,
    GitHubPullRequestSyncState,
    GitHubReaction,
    GitHubReactionContent,
    GitHubTargetDetails,
    GitHubTree,
    RepoInfo,
)
from vv_ai.backends.github.paths import (
    _build_issue_comment_reaction_path,
    _build_issue_comment_reactions_path,
    _build_issue_comments_path,
    _build_issue_label_path,
    _build_issues_path,
    _build_pulls_path,
    _build_repository_path,
    _require_mapping,
    _require_non_empty_optional_text,
    _require_non_empty_text,
    _require_positive_id,
    _require_repository_full_name,
    _require_string,
)
from vv_ai.backends.github.payload import (
    _artifact_sort_key,
    _build_artifact_page,
    _build_comment,
    _build_comment_list,
    _build_issue_from_rest,
    _build_issue_parent_number,
    _build_issue_timeline_event_list,
    _build_pull_request,
    _build_pull_request_from_rest,
    _build_pull_request_sync_state,
    _build_reaction,
    _build_tree,
    _decode_blob,
)
from vv_ai.backends.github.runner import (
    GhBinaryRunner,
    GhTextRunner,
    run_gh_binary,
    run_gh_binary_with_env,
    run_gh_text,
    run_gh_text_with_env,
)
from vv_ai.inputs.resolve import ResolvedTarget


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

    def get_pull_request_sync_state(
        self,
        repository_full_name: str,
        number: int,
    ) -> GitHubPullRequestSyncState:
        """sync が参照する Pull Request 状態を取得する。"""
        payload = self._run_json(
            [
                "pr",
                "view",
                str(_require_positive_id(number, "number")),
                "--repo",
                _require_repository_full_name(repository_full_name),
                "--json",
                "mergeable,mergeStateStatus,statusCheckRollup",
            ]
        )
        if not isinstance(payload, dict):
            raise GitHubClientError("Pull Request sync 状態の JSON 形式が不正です")
        return _build_pull_request_sync_state(payload)

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

    def list_issue_labeled_events(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubIssueLabeledEvent]:
        """Issue timeline の labeled event 一覧を取得する。"""
        return [
            GitHubIssueLabeledEvent(
                id=event.id,
                label_name=_require_non_empty_optional_text(
                    event.label_name,
                    "label_name",
                ),
                actor=event.actor,
                created_at=event.created_at,
            )
            for event in self.list_issue_timeline_events(repository_full_name, number)
            if event.event == "labeled"
        ]

    def list_issue_timeline_events(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubIssueTimelineEvent]:
        """Issue timeline の next 履歴用 event 一覧を取得する。"""
        owner, repo = _require_repository_full_name(repository_full_name).split("/")
        query = """
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    issueOrPullRequest(number: $number) {
      timelineItems(
        first: 100
        after: $endCursor
        itemTypes: [
          ISSUE_COMMENT
          LABELED_EVENT
          SUB_ISSUE_ADDED_EVENT
          CROSS_REFERENCED_EVENT
        ]
      ) {
        nodes {
          __typename
          ... on IssueComment {
            databaseId
            author {
              login
            }
            createdAt
            body
          }
          ... on LabeledEvent {
            databaseId
            actor {
              login
            }
            createdAt
            label {
              name
            }
          }
          ... on SubIssueAddedEvent {
            databaseId
            actor {
              login
            }
            createdAt
            subIssue {
              number
              repository {
                nameWithOwner
              }
            }
          }
          ... on CrossReferencedEvent {
            databaseId
            actor {
              login
            }
            createdAt
            source {
              __typename
              ... on Issue {
                number
                repository {
                  nameWithOwner
                }
              }
              ... on PullRequest {
                number
                repository {
                  nameWithOwner
                }
              }
            }
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
""".strip()
        payload = self._run_json(
            [
                "api",
                "graphql",
                "--paginate",
                "--slurp",
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
        if not isinstance(payload, list):
            raise GitHubClientError("timeline 取得結果の JSON 形式が不正です")

        return _build_issue_timeline_event_list(payload)

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

    def list_repository_artifacts_by_prefix(
        self,
        repository_full_name: str,
        prefix: str,
    ) -> list[GitHubArtifact]:
        """prefix に一致する artifact 一覧を新しい順で返す。"""
        matches = [
            artifact
            for artifact in self.list_repository_artifacts(repository_full_name)
            if artifact.name.startswith(prefix)
        ]
        matches.sort(key=_artifact_sort_key, reverse=True)
        return matches

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
        if is_fork is False:
            return RepoInfo(
                is_fork=False,
                parent_full_name=None,
                parent_default_branch=None,
            )
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


def _require_github_target(target: ResolvedTarget) -> tuple[str, int]:
    """GitHub target に必要な識別子を返す。"""
    if target.backend != "github":
        raise GitHubClientError("GitHub client は GitHub target のみ扱えます")
    if target.repository_full_name is None:
        raise GitHubClientError("GitHub target に repository がありません")
    if target.number is None:
        raise GitHubClientError("GitHub target に番号がありません")
    return target.repository_full_name, target.number
