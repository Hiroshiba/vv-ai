"""next コマンド解決の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.github import GitHubActor, GitHubComment
from vv_ai.next_command import NextResolutionError, resolve_next_command
from vv_ai.resolve import ResolvedCommand, ResolvedTarget


class _FakeGitHubClient:
    """next コマンドテスト用 GitHub client。"""

    def __init__(
        self,
        comments: list[GitHubComment],
        parent_number: int | None,
    ) -> None:
        self.comments = comments
        self.parent_number = parent_number

    def list_issue_comments(
        self,
        repository_full_name: str,
        number: int,
    ) -> list[GitHubComment]:
        """Issue comment 一覧を返す。"""
        return self.comments

    def get_issue_parent_number(
        self,
        repository_full_name: str,
        number: int,
    ) -> int | None:
        """Issue の親 Issue 番号を返す。"""
        return self.parent_number


def _make_config() -> VVAIConfig:
    """テスト用 config を返す。"""
    return VVAIConfig(allowed_users=["Hiroshiba"])


def _make_command(
    target: ResolvedTarget,
    comment_id: int | None,
) -> ResolvedCommand:
    """next 解決用の ResolvedCommand を返す。"""
    return ResolvedCommand.model_validate(
        {
            "event_name": "local",
            "command": "next",
            "has_target": True,
            "target": target,
            "comment_id": comment_id,
        }
    )


def _make_github_target(kind: str) -> ResolvedTarget:
    """GitHub target を返す。"""
    return ResolvedTarget.model_validate(
        {
            "backend": "github",
            "kind": kind,
            "canonical_id": "org/repo#1",
            "repository_full_name": "org/repo",
            "number": 1,
        }
    )


def _make_local_issue_target(target_dir: Path) -> ResolvedTarget:
    """local Issue target を返す。"""
    return ResolvedTarget(
        backend="local",
        kind="issue",
        canonical_id="issue:test",
        local_id="test",
        path=str(target_dir),
    )


def _make_comment(
    comment_id: int,
    body: str,
    author: str,
) -> GitHubComment:
    """GitHubComment を返す。"""
    return GitHubComment(
        id=comment_id,
        body=body,
        author=GitHubActor(login=author),
        created_at=f"2026-05-08T00:00:{comment_id:02d}Z",
        updated_at=f"2026-05-08T00:00:{comment_id:02d}Z",
        url=f"https://github.com/org/repo/issues/1#issuecomment-{comment_id}",
    )


def _patch_github_client(
    monkeypatch: pytest.MonkeyPatch,
    comments: list[GitHubComment],
    parent_number: int | None,
) -> None:
    """next_command の GitHub client を差し替える。"""
    client = _FakeGitHubClient(comments, parent_number)
    monkeypatch.setattr("vv_ai.next_command.build_github_client", lambda: client)


def test_github_issue_without_history_resolves_confirm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_github_client(monkeypatch, [], None)
    command = _make_command(_make_github_target("issue"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "confirm"


def test_github_sub_issue_without_history_resolves_implement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_github_client(monkeypatch, [], 5)
    command = _make_command(_make_github_target("issue"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "implement"


@pytest.mark.parametrize(
    ("history_command", "expected_command"),
    [
        ("confirm", "requirements"),
        ("requirements", "arch"),
        ("arch", "detail"),
        ("detail", "breakdown"),
    ],
)
def test_github_issue_history_resolves_next_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    history_command: str,
    expected_command: str,
) -> None:
    comments = [_make_comment(1, f"@vv-ai {history_command}", "Hiroshiba")]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("issue"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == expected_command


def test_github_issue_breakdown_history_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comments = [_make_comment(1, "@vv-ai breakdown", "Hiroshiba")]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("issue"), 100)

    with pytest.raises(NextResolutionError, match="breakdown"):
        resolve_next_command(tmp_path, command, _make_config())


def test_github_issue_implement_history_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comments = [_make_comment(1, "@vv-ai implement", "Hiroshiba")]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("issue"), 100)

    with pytest.raises(NextResolutionError, match="implement"):
        resolve_next_command(tmp_path, command, _make_config())


def test_github_pr_without_history_resolves_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_github_client(monkeypatch, [], None)
    command = _make_command(_make_github_target("pr"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "review"


@pytest.mark.parametrize(
    ("history_command", "expected_command"),
    [
        ("review", "implement"),
        ("implement", "review"),
    ],
)
def test_github_pr_history_resolves_next_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    history_command: str,
    expected_command: str,
) -> None:
    comments = [_make_comment(1, f"@vv-ai {history_command}", "Hiroshiba")]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("pr"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == expected_command


def test_ignores_unauthorized_comments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comments = [_make_comment(1, "@vv-ai detail", "unknown-user")]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("issue"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "confirm"


def test_ignores_current_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comments = [_make_comment(100, "@vv-ai detail", "Hiroshiba")]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("issue"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "confirm"


def test_replays_previous_next_comments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comments = [
        _make_comment(1, "@vv-ai next", "Hiroshiba"),
        _make_comment(2, "@vv-ai next", "Hiroshiba"),
    ]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("issue"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "arch"


def test_ignores_reply_and_issue_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    comments = [
        _make_comment(1, "@vv-ai reply confirm について教えて", "Hiroshiba"),
        _make_comment(2, "@vv-ai issue 不具合を起票して", "Hiroshiba"),
    ]
    _patch_github_client(monkeypatch, comments, None)
    command = _make_command(_make_github_target("issue"), 100)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "confirm"


def test_local_issue_history_reads_comment_files_in_name_order(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / ".vv-ai" / "issues" / "test"
    comments_dir = target_dir / "comments"
    comments_dir.mkdir(parents=True)
    (comments_dir / "20260508-000002-next.md").write_text("@vv-ai next", encoding="utf-8")
    (comments_dir / "20260508-000001-confirm.md").write_text("@vv-ai confirm", encoding="utf-8")
    command = _make_command(_make_local_issue_target(target_dir), None)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "arch"


def test_local_issue_without_history_resolves_confirm(tmp_path: Path) -> None:
    target_dir = tmp_path / ".vv-ai" / "issues" / "test"
    (target_dir / "comments").mkdir(parents=True)
    command = _make_command(_make_local_issue_target(target_dir), None)

    resolved = resolve_next_command(tmp_path, command, _make_config())

    assert resolved.command == "confirm"
