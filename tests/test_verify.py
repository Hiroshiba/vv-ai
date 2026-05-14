"""verify サブコマンド用の単体テスト。"""

from __future__ import annotations

import json
from pathlib import Path

from vv_ai.config import VVAIConfig
from vv_ai.verify import run_verify


def _write_event(tmp_path: Path, payload: dict[str, object]) -> Path:
    """event payload をファイルに書き出す。"""
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    return event_path


def _make_config() -> VVAIConfig:
    """テスト用の設定を返す。"""
    return VVAIConfig(allowed_users=["Hiroshiba"])


def _make_issue_labeled_payload(label_name: str, sender: str) -> dict[str, object]:
    """issues labeled payload を返す。"""
    return {
        "action": "labeled",
        "issue": {"number": 42},
        "label": {"name": label_name},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": sender},
    }


def _make_pull_request_labeled_payload(
    label_name: str,
    sender: str,
) -> dict[str, object]:
    """pull_request labeled payload を返す。"""
    return {
        "action": "labeled",
        "pull_request": {"number": 5},
        "label": {"name": label_name},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": sender},
    }


def test_issue_labeled_allowed_user_should_run(tmp_path: Path) -> None:
    event_path = _write_event(
        tmp_path,
        _make_issue_labeled_payload("vv-ai:confirm", "Hiroshiba"),
    )

    result = run_verify("issues", event_path, _make_config())

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "issues"


def test_pull_request_labeled_allowed_user_should_run(tmp_path: Path) -> None:
    event_path = _write_event(
        tmp_path,
        _make_pull_request_labeled_payload("vv-ai:review", "Hiroshiba"),
    )

    result = run_verify("pull_request", event_path, _make_config())

    assert result.should_run is True
    assert result.actor == "Hiroshiba"
    assert result.event == "pull_request"


def test_non_vv_ai_label_should_not_run(tmp_path: Path) -> None:
    event_path = _write_event(
        tmp_path,
        _make_issue_labeled_payload("bug", "Hiroshiba"),
    )

    result = run_verify("issues", event_path, _make_config())

    assert result.should_run is False
    assert result.reason == "not_vv_ai_label"


def test_unknown_vv_ai_label_should_not_run(tmp_path: Path) -> None:
    event_path = _write_event(
        tmp_path,
        _make_issue_labeled_payload("vv-ai:unknown", "Hiroshiba"),
    )

    result = run_verify("issues", event_path, _make_config())

    assert result.should_run is False
    assert result.reason == "unknown_label_command"


def test_unauthorized_label_should_not_run(tmp_path: Path) -> None:
    event_path = _write_event(
        tmp_path,
        _make_issue_labeled_payload("vv-ai:confirm", "unknown-user"),
    )

    result = run_verify("issues", event_path, _make_config())

    assert result.should_run is False
    assert result.reason == "unauthorized"
