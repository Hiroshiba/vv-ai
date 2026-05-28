"""verify サブコマンド判定の単体テスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.workflow.verify import run_verify


def _write_payload(tmp_path: Path, payload: dict[str, object]) -> Path:
    """テスト用 event payload を JSON で保存する。"""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    return event_file


def _make_config() -> VVAIConfig:
    """テスト用設定を生成する。"""
    return VVAIConfig(allowed_users=["Hiroshiba"])


def _make_issue_labeled_payload(
    label_name: str,
    actor: str,
    actor_id: int | None = 1,
) -> dict[str, object]:
    """issues labeled payload を生成する。"""
    sender: dict[str, object] = {"login": actor}
    if actor_id is not None:
        sender["id"] = actor_id
    return {
        "action": "labeled",
        "issue": {"number": 42, "updated_at": "2026-05-18T04:00:00Z"},
        "label": {"name": label_name},
        "repository": {"full_name": "org/repo"},
        "sender": sender,
    }


def _make_pull_request_labeled_payload(
    label_name: str,
    actor: str,
    actor_id: int | None = 1,
) -> dict[str, object]:
    """pull_request labeled payload を生成する。"""
    sender: dict[str, object] = {"login": actor}
    if actor_id is not None:
        sender["id"] = actor_id
    return {
        "action": "labeled",
        "pull_request": {"number": 43, "updated_at": "2026-05-18T04:00:00Z"},
        "label": {"name": label_name},
        "repository": {"full_name": "org/repo"},
        "sender": sender,
    }


def _make_pull_request_closed_payload(
    label_names: list[str],
    actor: str,
    actor_id: int | None = 1,
) -> dict[str, object]:
    """pull_request closed payload を生成する。"""
    sender: dict[str, object] = {"login": actor}
    if actor_id is not None:
        sender["id"] = actor_id
    return {
        "action": "closed",
        "pull_request": {
            "number": 43,
            "merged": True,
            "labels": [{"name": label_name} for label_name in label_names],
        },
        "repository": {"full_name": "org/repo"},
        "sender": sender,
    }


def test_issue_comment_vv_ai_prefix_should_run(tmp_path: Path) -> None:
    payload = {
        "comment": {
            "id": 100,
            "body": "@vv-ai reply",
            "user": {"login": "Hiroshiba"},
        },
        "issue": {"number": 42},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "Hiroshiba"},
    }

    result = run_verify(
        "issue_comment", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is True
    assert result.actor == "Hiroshiba"


def test_issue_labeled_confirm_should_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:confirm", "Hiroshiba")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "issues"


def test_issue_labeled_next_should_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:next", "Hiroshiba")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "issues"


def test_issue_labeled_auto_should_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:auto", "Hiroshiba")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "issues"


def test_pull_request_labeled_review_should_run(tmp_path: Path) -> None:
    payload = _make_pull_request_labeled_payload("vv-ai:review", "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "pull_request"


def test_pull_request_labeled_address_should_run(tmp_path: Path) -> None:
    payload = _make_pull_request_labeled_payload("vv-ai:address", "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "pull_request"


def test_pull_request_labeled_next_should_run(tmp_path: Path) -> None:
    payload = _make_pull_request_labeled_payload("vv-ai:next", "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "pull_request"


def test_pull_request_labeled_auto_should_run(tmp_path: Path) -> None:
    payload = _make_pull_request_labeled_payload("vv-ai:auto", "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "pull_request"


def test_pull_request_labeled_sync_should_run(tmp_path: Path) -> None:
    payload = _make_pull_request_labeled_payload("vv-ai:sync", "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "pull_request"


def test_pull_request_closed_auto_should_run(tmp_path: Path) -> None:
    payload = _make_pull_request_closed_payload(["vv-ai:auto"], "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "pull_request"


def test_pull_request_closed_without_auto_should_not_run(tmp_path: Path) -> None:
    payload = _make_pull_request_closed_payload(["bug"], "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is False
    assert result.reason == "not_vv_ai_label"


def test_pull_request_closed_unauthorized_user_should_not_run(
    tmp_path: Path,
) -> None:
    payload = _make_pull_request_closed_payload(["vv-ai:auto"], "unknown-user")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is False
    assert result.reason == "unauthorized"


@pytest.mark.parametrize(
    "label_name",
    ["bug", "vv-ai", "vv-ai:", "vv-ai:unknown"],
)
def test_issue_labeled_invalid_label_should_not_run(
    tmp_path: Path, label_name: str
) -> None:
    payload = _make_issue_labeled_payload(label_name, "Hiroshiba")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False


def test_issue_labeled_review_should_not_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:review", "Hiroshiba")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False


def test_issue_labeled_address_should_not_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:address", "Hiroshiba")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False


def test_issue_labeled_sync_should_not_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:sync", "Hiroshiba")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False


def test_pull_request_labeled_breakdown_should_not_run(tmp_path: Path) -> None:
    payload = _make_pull_request_labeled_payload("vv-ai:breakdown", "Hiroshiba")

    result = run_verify(
        "pull_request", _write_payload(tmp_path, payload), _make_config()
    )

    assert result.should_run is False


def test_issue_labeled_unauthorized_user_should_not_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:confirm", "unknown-user")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False
    assert result.reason == "unauthorized"


def test_issue_labeled_auto_unauthorized_user_should_not_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload("vv-ai:auto", "unknown-user")

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False
    assert result.reason == "unauthorized"


def test_issue_labeled_internal_bot_next_should_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload(
        "vv-ai:next",
        "vv-ai-public-read-github-app[bot]",
        actor_id=274163862,
    )

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is True
    assert result.actor == "vv-ai-public-read-github-app[bot]"


def test_issue_labeled_other_bot_next_should_not_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload(
        "vv-ai:next",
        "vv-ai-public-read-github-app[bot]",
        actor_id=999,
    )

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False
    assert result.reason == "unauthorized"


def test_issue_labeled_internal_bot_auto_should_not_run(tmp_path: Path) -> None:
    payload = _make_issue_labeled_payload(
        "vv-ai:auto",
        "vv-ai-public-read-github-app[bot]",
        actor_id=274163862,
    )

    result = run_verify("issues", _write_payload(tmp_path, payload), _make_config())

    assert result.should_run is False
    assert result.reason == "unauthorized"
