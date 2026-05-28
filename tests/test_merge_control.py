"""merge 制御の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.backends.github.models import GitHubClientError
from vv_ai.config import VVAIConfig
from vv_ai.inputs.resolve import ResolvedControlLabel
from vv_ai.merge_control import MergeControlError, run_merge_control


class FakeGitHubClient:
    """merge 制御テスト用の GitHub client。"""

    def __init__(self) -> None:
        self.merged: list[tuple[str, int, list[str]]] = []
        self.disabled: list[tuple[str, int]] = []
        self.error: GitHubClientError | None = None

    def merge_pull_request(
        self,
        repository_full_name: str,
        number: int,
        merge_args: list[str],
    ) -> None:
        """merge 操作の呼び出しを記録する。"""
        if self.error is not None:
            raise self.error
        self.merged.append((repository_full_name, number, merge_args))

    def disable_pull_request_auto_merge(
        self,
        repository_full_name: str,
        number: int,
    ) -> None:
        """auto merge 解除の呼び出しを記録する。"""
        if self.error is not None:
            raise self.error
        self.disabled.append((repository_full_name, number))


def _make_config() -> VVAIConfig:
    """テスト用設定を生成する。"""
    return VVAIConfig(allowed_users=["Hiroshiba"], merge_args=["--auto", "--squash"])


def _make_control(**overrides: object) -> ResolvedControlLabel:
    """テスト用制御ラベルを生成する。"""
    defaults: dict[str, object] = {
        "event_name": "pull_request",
        "control_label_name": "vv-ai:merge",
        "label_action": "labeled",
        "target_type": "pr",
        "target_number": 12,
        "has_target": True,
        "repository_full_name": "org/repo",
        "actor": "Hiroshiba",
        "trigger_label_name": "vv-ai:merge",
        "trigger_event_created_at": "2026-05-18T04:00:00Z",
    }
    defaults.update(overrides)
    return ResolvedControlLabel.model_validate(defaults)


def test_labeled_merges_pull_request() -> None:
    client = FakeGitHubClient()

    result = run_merge_control(client, _make_control(), _make_config())

    assert result.status == "merge_requested"
    assert client.merged == [("org/repo", 12, ["--auto", "--squash"])]
    assert client.disabled == []


def test_unlabeled_disables_auto_merge() -> None:
    client = FakeGitHubClient()

    result = run_merge_control(
        client,
        _make_control(label_action="unlabeled"),
        _make_config(),
    )

    assert result.status == "auto_disabled"
    assert client.merged == []
    assert client.disabled == [("org/repo", 12)]


def test_issue_target_raises() -> None:
    client = FakeGitHubClient()

    with pytest.raises(MergeControlError, match="PR 専用"):
        run_merge_control(
            client,
            _make_control(event_name="issues", target_type="issue"),
            _make_config(),
        )


def test_auto_control_label_raises() -> None:
    client = FakeGitHubClient()

    with pytest.raises(MergeControlError, match="vv-ai:merge"):
        run_merge_control(
            client,
            _make_control(
                control_label_name="vv-ai:auto",
                trigger_label_name="vv-ai:auto",
            ),
            _make_config(),
        )


def test_github_client_error_propagates() -> None:
    client = FakeGitHubClient()
    client.error = GitHubClientError("失敗")

    with pytest.raises(GitHubClientError, match="失敗"):
        run_merge_control(client, _make_control(), _make_config())
