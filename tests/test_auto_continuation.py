"""自動継続計画の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.auto_continuation import (
    AUTO_LABEL_NAME,
    NEXT_LABEL_NAME,
    AutoContinuationError,
    AutoContinuationPlan,
    apply_auto_continuation_plan,
    build_move_to_sub_issue_decision,
    load_auto_continuation_plan,
    save_auto_continuation_plan,
    find_first_incomplete_sub_issue,
    continue_after_pull_request_closed,
)
from vv_ai.auto_control import AutoContinuationAction
from vv_ai.backends.github.models import (
    GitHubActor,
    GitHubArtifact,
    GitHubIssue,
    GitHubIssueLabeledEvent,
    GitHubIssueReference,
    GitHubPullRequest,
    GitHubPullRequestClosingState,
)
from vv_ai.inputs.resolve import ResolvedTarget


def _make_plan(
    target_type: str,
    action: AutoContinuationAction,
    next_label_name: str | None,
) -> AutoContinuationPlan:
    return AutoContinuationPlan(
        repository_full_name="org/repo",
        target_type=target_type,
        target_number=1,
        source_label_name="vv-ai:confirm",
        action=action,
        next_label_name=next_label_name,
        session_artifact_target_prefix="vv-ai-session__org-repo-1__",
        workflow_id="test-workflow",
    )


def _make_continue_plan(target_type: str) -> AutoContinuationPlan:
    return _make_plan(target_type, "continue", NEXT_LABEL_NAME)


def _make_issue(state: str) -> GitHubIssue:
    return GitHubIssue(
        id=1,
        repository_full_name="org/repo",
        number=1,
        title="Issue",
        body="本文",
        state=state,
        author=GitHubActor(login="Hiroshiba"),
        url="https://github.com/org/repo/issues/1",
    )


def _make_issue_number(number: int, state: str) -> GitHubIssue:
    return GitHubIssue(
        id=number,
        repository_full_name="org/repo",
        number=number,
        title=f"Issue {number}",
        body="本文",
        state=state,
        author=GitHubActor(login="Hiroshiba"),
        url=f"https://github.com/org/repo/issues/{number}",
    )


def _make_pr(state: str) -> GitHubPullRequest:
    return GitHubPullRequest(
        repository_full_name="org/repo",
        number=1,
        title="PR",
        body="本文",
        state=state,
        author=GitHubActor(login="Hiroshiba"),
        url="https://github.com/org/repo/pull/1",
        head_ref_name="feature",
        base_ref_name="main",
        head_repository_full_name="org/repo",
        is_cross_repository=False,
        maintainer_can_modify=True,
    )


def _make_labeled_event(label_name: str, created_at: str) -> GitHubIssueLabeledEvent:
    return GitHubIssueLabeledEvent(
        id=None,
        label_name=label_name,
        actor=GitHubActor(login="Hiroshiba"),
        created_at=created_at,
    )


def _make_artifact(artifact_id: int, created_at: str) -> GitHubArtifact:
    return GitHubArtifact(
        id=artifact_id,
        name=f"vv-ai-session__org-repo-1__codex__main__run-{artifact_id}",
        created_at=created_at,
        archive_download_url=f"https://example.test/{artifact_id}",
    )


class FakeGitHubClient:
    """自動継続テスト用の GitHub client。"""

    def __init__(
        self,
        labels: list[str],
        target_details: GitHubIssue | GitHubPullRequest,
        labeled_events: list[GitHubIssueLabeledEvent],
        artifacts: list[GitHubArtifact],
    ) -> None:
        self.labels = labels
        self.target_details = target_details
        self.labeled_events = labeled_events
        self.artifacts = artifacts
        self.operations: list[tuple[str, str]] = []

    def list_issue_label_names(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[str]:
        assert repository_full_name == "org/repo"
        assert number == 1
        return self.labels

    def get_target_details(self, target: ResolvedTarget) -> GitHubIssue | GitHubPullRequest:
        assert target.repository_full_name == "org/repo"
        assert target.number == 1
        return self.target_details

    def remove_issue_label(
        self,
        repository_full_name: str,
        number: int,
        label_name: str,
    ) -> None:
        assert repository_full_name == "org/repo"
        assert number == 1
        self.operations.append(("remove", label_name))

    def add_issue_label(
        self,
        repository_full_name: str,
        number: int,
        label_name: str,
    ) -> None:
        assert repository_full_name == "org/repo"
        assert number == 1
        self.operations.append(("add", label_name))

    def list_issue_labeled_events(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubIssueLabeledEvent]:
        assert repository_full_name == "org/repo"
        assert number == 1
        return self.labeled_events

    def list_repository_artifacts_by_prefix(
        self,
        repository_full_name: str,
        prefix: str,
    ) -> list[GitHubArtifact]:
        assert repository_full_name == "org/repo"
        assert prefix == "vv-ai-session__org-repo-1__"
        return self.artifacts


class CrossTargetFakeGitHubClient:
    """対象またぎ自動継続テスト用の GitHub client。"""

    def __init__(self) -> None:
        self.labels: dict[int, list[str]] = {
            1: [AUTO_LABEL_NAME, "vv-ai:breakdown"],
            10: [AUTO_LABEL_NAME],
        }
        self.target_states: dict[int, str] = {}
        self.sub_issues: list[GitHubIssue] = []
        self.merged_closing_pull_request_numbers: set[int] = set()
        self.parent_numbers: dict[int, int | None] = {}
        self.closing_state = GitHubPullRequestClosingState(
            merged=True,
            closing_issue_references=[],
        )
        self.operations: list[tuple[str, int, str]] = []

    def list_issue_label_names(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[str]:
        assert repository_full_name == "org/repo"
        return self.labels.get(number, [])

    def get_target_details(self, target: ResolvedTarget) -> GitHubIssue:
        assert target.repository_full_name == "org/repo"
        assert target.number is not None
        return _make_issue_number(
            target.number,
            self.target_states.get(target.number, "OPEN"),
        )

    def remove_issue_label(
        self,
        repository_full_name: str,
        number: int,
        label_name: str,
    ) -> None:
        assert repository_full_name == "org/repo"
        self.operations.append(("remove", number, label_name))

    def add_issue_label(
        self,
        repository_full_name: str,
        number: int,
        label_name: str,
    ) -> None:
        assert repository_full_name == "org/repo"
        self.operations.append(("add", number, label_name))

    def list_issue_labeled_events(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubIssueLabeledEvent]:
        assert repository_full_name == "org/repo"
        assert number == 1
        return [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")]

    def list_repository_artifacts_by_prefix(
        self,
        repository_full_name: str,
        prefix: str,
    ) -> list[GitHubArtifact]:
        assert repository_full_name == "org/repo"
        assert prefix == "vv-ai-session__org-repo-1__"
        return [_make_artifact(1, "2026-05-27T00:01:00Z")]

    def list_sub_issues(
        self,
        repository_full_name: str,
        parent_number: int,
    ) -> list[GitHubIssue]:
        assert repository_full_name == "org/repo"
        assert parent_number == 1
        return self.sub_issues

    def has_merged_closing_pull_request(
        self,
        repository_full_name: str,
        number: int,
    ) -> bool:
        assert repository_full_name == "org/repo"
        return number in self.merged_closing_pull_request_numbers

    def get_pull_request_closing_state(
        self,
        repository_full_name: str,
        number: int,
    ) -> GitHubPullRequestClosingState:
        assert repository_full_name == "org/repo"
        assert number == 10
        return self.closing_state

    def get_issue_parent_number(
        self,
        repository_full_name: str,
        number: int,
    ) -> int | None:
        assert repository_full_name == "org/repo"
        return self.parent_numbers[number]


def test_save_and_load_auto_continuation_plan(tmp_path: Path) -> None:
    plan = _make_continue_plan("issue")

    save_auto_continuation_plan(tmp_path, "test-workflow", plan)
    loaded = load_auto_continuation_plan(tmp_path, "test-workflow")

    assert loaded == plan


def test_load_auto_continuation_plan_returns_none_for_missing_file(
    tmp_path: Path,
) -> None:
    assert load_auto_continuation_plan(tmp_path, "test-workflow") is None


def test_apply_auto_continuation_plan_returns_no_plan(tmp_path: Path) -> None:
    client = FakeGitHubClient([], _make_issue("OPEN"), [], [])

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "no_plan"
    assert client.operations == []


def test_apply_auto_continuation_plan_continues(tmp_path: Path) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_continue_plan("issue"))
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_issue("OPEN"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "continued"
    assert client.operations == [
        ("remove", "vv-ai:confirm"),
        ("add", NEXT_LABEL_NAME),
    ]


def test_apply_auto_continuation_plan_stops(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    save_auto_continuation_plan(
        tmp_path,
        "test-workflow",
        _make_plan("issue", "stop", None),
    )
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_issue("OPEN"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "stopped"
    captured = capsys.readouterr()
    assert "自動進行を停止します" in captured.out
    assert client.operations == [
        ("remove", "vv-ai:confirm"),
        ("remove", AUTO_LABEL_NAME),
    ]


def test_apply_auto_continuation_plan_waits_for_merge(tmp_path: Path) -> None:
    save_auto_continuation_plan(
        tmp_path,
        "test-workflow",
        _make_plan("pr", "merge_wait", None),
    )
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_pr("OPEN"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "merge_waiting"
    assert client.operations == [("remove", "vv-ai:confirm")]


def test_apply_auto_continuation_plan_skips_without_auto_label(tmp_path: Path) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_continue_plan("issue"))
    client = FakeGitHubClient(
        ["vv-ai:confirm"],
        _make_issue("OPEN"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "auto_removed"
    assert client.operations == [("remove", "vv-ai:confirm")]


def test_apply_auto_continuation_plan_skips_closed_issue(tmp_path: Path) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_continue_plan("issue"))
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_issue("CLOSED"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "target_closed"
    assert client.operations == [
        ("remove", "vv-ai:confirm"),
        ("remove", AUTO_LABEL_NAME),
    ]


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_apply_auto_continuation_plan_skips_closed_pr(
    tmp_path: Path,
    state: str,
) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_continue_plan("pr"))
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_pr(state),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "target_closed"
    assert client.operations == [
        ("remove", "vv-ai:confirm"),
        ("remove", AUTO_LABEL_NAME),
    ]


def test_apply_auto_continuation_plan_skips_removed_source_label(
    tmp_path: Path,
) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_continue_plan("issue"))
    client = FakeGitHubClient(
        [],
        _make_issue("OPEN"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "auto_removed"
    assert client.operations == []


def test_apply_auto_continuation_plan_removes_auto_at_limit(tmp_path: Path) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_continue_plan("issue"))
    artifacts = [
        _make_artifact(index, "2026-05-27T00:01:00Z")
        for index in range(1, 11)
    ]
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_issue("OPEN"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        artifacts,
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "limit_reached"
    assert client.operations == [
        ("remove", "vv-ai:confirm"),
        ("remove", AUTO_LABEL_NAME),
    ]


def test_apply_auto_continuation_plan_requires_auto_label_event(
    tmp_path: Path,
) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_continue_plan("issue"))
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_issue("OPEN"),
        [],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    with pytest.raises(AutoContinuationError, match="自動継続 label"):
        apply_auto_continuation_plan(tmp_path, "test-workflow", client)


def test_apply_auto_continuation_plan_moves_to_sub_issue(tmp_path: Path) -> None:
    plan = _make_plan("issue", "move", None).model_copy(
        update={
            "source_label_name": "vv-ai:breakdown",
            "destination_target_type": "issue",
            "destination_target_number": 2,
            "destination_label_names": [AUTO_LABEL_NAME, NEXT_LABEL_NAME],
        }
    )
    save_auto_continuation_plan(tmp_path, "test-workflow", plan)
    client = CrossTargetFakeGitHubClient()

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "moved"
    assert client.operations == [
        ("remove", 1, "vv-ai:breakdown"),
        ("remove", 1, AUTO_LABEL_NAME),
        ("add", 2, AUTO_LABEL_NAME),
        ("add", 2, NEXT_LABEL_NAME),
    ]


def test_apply_auto_continuation_plan_stops_when_destination_is_closed(
    tmp_path: Path,
) -> None:
    plan = _make_plan("issue", "move", None).model_copy(
        update={
            "source_label_name": "vv-ai:breakdown",
            "destination_target_type": "issue",
            "destination_target_number": 2,
            "destination_label_names": [AUTO_LABEL_NAME, NEXT_LABEL_NAME],
        }
    )
    save_auto_continuation_plan(tmp_path, "test-workflow", plan)
    client = CrossTargetFakeGitHubClient()
    client.target_states[2] = "CLOSED"

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "target_closed"
    assert client.operations == [
        ("remove", 1, "vv-ai:breakdown"),
        ("remove", 1, AUTO_LABEL_NAME),
    ]


def test_apply_auto_continuation_plan_stops_without_destination(
    tmp_path: Path,
) -> None:
    plan = _make_plan("issue", "move", None).model_copy(
        update={
            "source_label_name": "vv-ai:breakdown",
            "destination_target_type": None,
            "destination_target_number": None,
            "destination_label_names": [],
        }
    )
    save_auto_continuation_plan(tmp_path, "test-workflow", plan)
    client = CrossTargetFakeGitHubClient()

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "no_destination"
    assert client.operations == [
        ("remove", 1, "vv-ai:breakdown"),
        ("remove", 1, AUTO_LABEL_NAME),
    ]


def test_find_first_incomplete_sub_issue_skips_completed_pr() -> None:
    client = CrossTargetFakeGitHubClient()
    client.sub_issues = [
        _make_issue_number(2, "OPEN"),
        _make_issue_number(3, "OPEN"),
    ]
    client.merged_closing_pull_request_numbers = {2}

    sub_issue = find_first_incomplete_sub_issue(client, "org/repo", 1)

    assert sub_issue is not None
    assert sub_issue.number == 3


def test_find_first_incomplete_sub_issue_ignores_cross_reference_only() -> None:
    client = CrossTargetFakeGitHubClient()
    client.sub_issues = [_make_issue_number(2, "OPEN")]

    sub_issue = find_first_incomplete_sub_issue(client, "org/repo", 1)

    assert sub_issue is not None
    assert sub_issue.number == 2


def test_build_move_to_sub_issue_decision_stops_without_incomplete_sub_issue() -> None:
    client = CrossTargetFakeGitHubClient()

    decision = build_move_to_sub_issue_decision(client, "org/repo", 1)

    assert decision.action == "stop"
    assert decision.stop_reason == "未完了サブ Issue が見つかりません"


def test_continue_after_pull_request_closed_moves_to_next_sub_issue() -> None:
    client = CrossTargetFakeGitHubClient()
    client.closing_state = GitHubPullRequestClosingState(
        merged=True,
        closing_issue_references=[
            GitHubIssueReference(repository_full_name="org/repo", number=2)
        ],
    )
    client.parent_numbers = {2: 1}
    client.sub_issues = [
        _make_issue_number(2, "CLOSED"),
        _make_issue_number(3, "OPEN"),
    ]

    result = continue_after_pull_request_closed(client, "org/repo", 10, True)

    assert result.status == "moved"
    assert client.operations == [
        ("remove", 10, AUTO_LABEL_NAME),
        ("add", 3, AUTO_LABEL_NAME),
        ("add", 3, NEXT_LABEL_NAME),
    ]


def test_continue_after_pull_request_closed_stops_without_closing_issue(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = CrossTargetFakeGitHubClient()

    result = continue_after_pull_request_closed(client, "org/repo", 10, True)

    captured = capsys.readouterr()
    assert result.status == "stopped"
    assert "close 対象 Issue がない" in captured.err
    assert client.operations == [("remove", 10, AUTO_LABEL_NAME)]


def test_continue_after_pull_request_closed_stops_without_auto_label() -> None:
    client = CrossTargetFakeGitHubClient()
    client.labels[10] = []

    result = continue_after_pull_request_closed(client, "org/repo", 10, True)

    assert result.status == "auto_removed"
    assert client.operations == []
