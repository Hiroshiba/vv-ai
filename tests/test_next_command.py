"""next コマンド解決の単体テスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.github import GitHubActor, GitHubComment
from vv_ai.input import CommandName, TargetType
from vv_ai.next_command import NextResolutionError, resolve_next_command
from vv_ai.resolve import ResolvedCommand, ResolvedTarget


def _config() -> VVAIConfig:
    """テスト用 config を返す。"""
    return VVAIConfig(allowed_users=["Hiroshiba"])


def _command(command_name: CommandName, target: ResolvedTarget) -> ResolvedCommand:
    """テスト用 ResolvedCommand を返す。"""
    return ResolvedCommand.model_validate(
        {
            "event_name": "issue_comment",
            "command": command_name,
            "has_target": True,
            "comment_id": 999,
            "target": target,
        }
    )


def _github_target(kind: TargetType) -> ResolvedTarget:
    """テスト用 GitHub target を返す。"""
    return ResolvedTarget.model_validate(
        {
            "backend": "github",
            "kind": kind,
            "canonical_id": f"org/repo#1:{kind}",
            "repository_full_name": "org/repo",
            "number": 1,
        }
    )


def _local_target(tmp_path: Path, kind: TargetType) -> ResolvedTarget:
    """テスト用 local target を返す。"""
    target_dir = tmp_path / ".vv-ai" / f"{kind}s" / "target-1"
    target_dir.mkdir(parents=True)
    return ResolvedTarget.model_validate(
        {
            "backend": "local",
            "kind": kind,
            "canonical_id": f"{kind}:target-1",
            "path": str(target_dir),
        }
    )


def _comment(
    comment_id: int,
    body: str,
    author: str,
    created_at: str,
) -> GitHubComment:
    """テスト用 GitHubComment を返す。"""
    return GitHubComment(
        id=comment_id,
        body=body,
        author=GitHubActor(login=author),
        created_at=created_at,
        updated_at=created_at,
        url=f"https://github.com/org/repo/issues/1#issuecomment-{comment_id}",
    )


def test_non_next_returns_same_command(tmp_path: Path) -> None:
    """next 以外は同じ ResolvedCommand を返す。"""
    command = _command("confirm", _github_target("issue"))

    assert resolve_next_command(tmp_path, command, _config()) is command


def test_github_issue_without_history_resolves_confirm(tmp_path: Path) -> None:
    """通常 Issue の履歴なし next は confirm に解決される。"""
    target = _github_target("issue")
    command = _command("next", target)
    client = MagicMock()
    client.get_issue_parent_number.return_value = None
    client.list_issue_comments.return_value = []

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "confirm"
    client.get_issue_parent_number.assert_called_once_with("org/repo", 1)
    client.list_issue_comments.assert_called_once_with("org/repo", 1)


def test_github_sub_issue_without_history_resolves_implement(tmp_path: Path) -> None:
    """サブ Issue の履歴なし next は implement に解決される。"""
    target = _github_target("issue")
    command = _command("next", target)
    client = MagicMock()
    client.get_issue_parent_number.return_value = 10
    client.list_issue_comments.return_value = []

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "implement"


def test_issue_history_replays_continuous_next(tmp_path: Path) -> None:
    """Issue 履歴の連続 next はその時点の履歴状態から再生される。"""
    target = _github_target("issue")
    command = _command("next", target)
    client = MagicMock()
    client.get_issue_parent_number.return_value = None
    client.list_issue_comments.return_value = [
        _comment(1, "@vv-ai confirm", "Hiroshiba", "2026-01-01T00:00:00Z"),
        _comment(2, "@vv-ai next", "Hiroshiba", "2026-01-01T00:01:00Z"),
        _comment(3, "@vv-ai next", "Hiroshiba", "2026-01-01T00:02:00Z"),
        _comment(4, "@vv-ai next", "Hiroshiba", "2026-01-01T00:03:00Z"),
    ]

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "breakdown"


def test_issue_after_breakdown_raises(tmp_path: Path) -> None:
    """Issue の breakdown 後 next は例外になる。"""
    target = _github_target("issue")
    command = _command("next", target)
    client = MagicMock()
    client.get_issue_parent_number.return_value = None
    client.list_issue_comments.return_value = [
        _comment(1, "@vv-ai breakdown", "Hiroshiba", "2026-01-01T00:00:00Z"),
    ]

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        with pytest.raises(NextResolutionError, match="breakdown"):
            resolve_next_command(tmp_path, command, _config())


def test_issue_history_ignores_unusable_comments(tmp_path: Path) -> None:
    """Issue 履歴では除外対象コメントを履歴に入れない。"""
    target = _github_target("issue")
    command = _command("next", target)
    client = MagicMock()
    client.get_issue_parent_number.return_value = None
    client.list_issue_comments.return_value = [
        _comment(1, "@vv-ai confirm", "other", "2026-01-01T00:00:00Z"),
        _comment(999, "@vv-ai confirm", "Hiroshiba", "2026-01-01T00:01:00Z"),
        _comment(2, "hello", "Hiroshiba", "2026-01-01T00:02:00Z"),
        _comment(3, "@vv-ai reply", "Hiroshiba", "2026-01-01T00:03:00Z"),
        _comment(4, "@vv-ai issue foo", "Hiroshiba", "2026-01-01T00:04:00Z"),
        _comment(5, "@vv-ai review", "Hiroshiba", "2026-01-01T00:05:00Z"),
    ]

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "confirm"


def test_pr_without_history_resolves_review(tmp_path: Path) -> None:
    """PR の履歴なし next は review に解決される。"""
    target = _github_target("pr")
    command = _command("next", target)
    client = MagicMock()
    client.list_issue_comments.return_value = []

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "review"
    client.get_issue_parent_number.assert_not_called()


def test_pr_history_loops_between_review_and_implement(tmp_path: Path) -> None:
    """PR 履歴では review と implement が交互に解決される。"""
    target = _github_target("pr")
    command = _command("next", target)
    client = MagicMock()
    client.list_issue_comments.return_value = [
        _comment(1, "@vv-ai review", "Hiroshiba", "2026-01-01T00:00:00Z"),
        _comment(2, "@vv-ai next", "Hiroshiba", "2026-01-01T00:01:00Z"),
    ]

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "review"


def test_pr_history_ignores_issue_planning_commands(tmp_path: Path) -> None:
    """PR 履歴では Issue 向け設計コマンドを履歴に入れない。"""
    target = _github_target("pr")
    command = _command("next", target)
    client = MagicMock()
    client.list_issue_comments.return_value = [
        _comment(1, "@vv-ai confirm", "Hiroshiba", "2026-01-01T00:00:00Z"),
        _comment(2, "@vv-ai requirements", "Hiroshiba", "2026-01-01T00:01:00Z"),
        _comment(3, "@vv-ai arch", "Hiroshiba", "2026-01-01T00:02:00Z"),
        _comment(4, "@vv-ai detail", "Hiroshiba", "2026-01-01T00:03:00Z"),
        _comment(5, "@vv-ai breakdown", "Hiroshiba", "2026-01-01T00:04:00Z"),
    ]

    with patch("vv_ai.next_command.build_github_client", return_value=client):
        result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "review"


def test_local_history_reads_comment_files_by_filename(tmp_path: Path) -> None:
    """local target は comments 配下の Markdown をファイル名順に読む。"""
    target = _local_target(tmp_path, "issue")
    assert target.path is not None
    comments_dir = Path(target.path) / "comments"
    comments_dir.mkdir()
    (comments_dir / "20260101-000200-next.md").write_text(
        "@vv-ai next",
        encoding="utf-8",
    )
    (comments_dir / "20260101-000000-confirm.md").write_text(
        "@vv-ai confirm",
        encoding="utf-8",
    )
    (comments_dir / "20260101-000100-next.md").write_text(
        "@vv-ai next",
        encoding="utf-8",
    )
    command = _command("next", target)

    result = resolve_next_command(tmp_path, command, _config())

    assert result.command == "detail"


def test_local_missing_comments_dir_raises(tmp_path: Path) -> None:
    """local target の comments ディレクトリがない場合は例外になる。"""
    target = _local_target(tmp_path, "issue")
    command = _command("next", target)

    with pytest.raises(NextResolutionError, match="comments"):
        resolve_next_command(tmp_path, command, _config())
