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
    load_auto_continuation_plan,
    save_auto_continuation_plan,
)
from vv_ai.backends.github.models import (
    GitHubActor,
    GitHubArtifact,
    GitHubIssue,
    GitHubIssueLabeledEvent,
    GitHubPullRequest,
)
from vv_ai.inputs.resolve import ResolvedTarget


def _make_plan(target_type: str) -> AutoContinuationPlan:
    return AutoContinuationPlan(
        repository_full_name="org/repo",
        target_type=target_type,
        target_number=1,
        source_label_name="vv-ai:confirm",
        next_label_name=NEXT_LABEL_NAME,
        session_artifact_target_prefix="vv-ai-session__org-repo-1__",
        workflow_id="test-workflow",
    )


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


def test_save_and_load_auto_continuation_plan(tmp_path: Path) -> None:
    plan = _make_plan("issue")

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
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_plan("issue"))
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


def test_apply_auto_continuation_plan_skips_without_auto_label(tmp_path: Path) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_plan("issue"))
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
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_plan("issue"))
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_issue("CLOSED"),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "target_closed"
    assert client.operations == [("remove", "vv-ai:confirm")]


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_apply_auto_continuation_plan_skips_closed_pr(
    tmp_path: Path,
    state: str,
) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_plan("pr"))
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_pr(state),
        [_make_labeled_event(AUTO_LABEL_NAME, "2026-05-27T00:00:00Z")],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    result = apply_auto_continuation_plan(tmp_path, "test-workflow", client)

    assert result.status == "target_closed"
    assert client.operations == [("remove", "vv-ai:confirm")]


def test_apply_auto_continuation_plan_skips_removed_source_label(
    tmp_path: Path,
) -> None:
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_plan("issue"))
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
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_plan("issue"))
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
    save_auto_continuation_plan(tmp_path, "test-workflow", _make_plan("issue"))
    client = FakeGitHubClient(
        [AUTO_LABEL_NAME, "vv-ai:confirm"],
        _make_issue("OPEN"),
        [],
        [_make_artifact(1, "2026-05-27T00:01:00Z")],
    )

    with pytest.raises(AutoContinuationError, match="自動継続 label"):
        apply_auto_continuation_plan(tmp_path, "test-workflow", client)
