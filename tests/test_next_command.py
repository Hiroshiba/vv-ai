"""next コマンド解決の単体テスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.github import GitHubActor, GitHubComment
from vv_ai.next_command import NextResolutionError, resolve_next_command
from vv_ai.resolve import ResolvedCommand, ResolvedTarget


def _make_config() -> VVAIConfig:
    return VVAIConfig(allowed_users=["Hiroshiba"])


def _make_target(kind: str, backend: str) -> ResolvedTarget:
    if backend == "github":
        return ResolvedTarget(
            backend="github",
            kind=kind,
            canonical_id="org/repo#1",
            repository_full_name="org/repo",
            number=1,
            url=f"https://github.com/org/repo/{'issues' if kind == 'issue' else 'pull'}/1",
        )
    return ResolvedTarget(
        backend="local",
        kind=kind,
        canonical_id=f"{kind}:test",
        local_id="test",
        path=".vv-ai/issues/test" if kind == "issue" else ".vv-ai/prs/test",
    )


def _make_command(
    command: str,
    target: ResolvedTarget | None,
    comment_id: int | None,
) -> ResolvedCommand:
    return ResolvedCommand(
        event_name="issue_comment",
        command=command,
        has_target=target is not None,
        comment_id=comment_id,
        target=target,
    )


def _make_next_command(
    target: ResolvedTarget,
    comment_id: int | None,
) -> ResolvedCommand:
    return _make_command("next", target, comment_id)


def _make_comment(
    comment_id: int,
    body: str,
    author: str,
    created_at: str,
) -> GitHubComment:
    return GitHubComment(
        id=comment_id,
        body=body,
        author=GitHubActor(login=author),
        created_at=created_at,
        updated_at=created_at,
        url=f"https://github.com/org/repo/issues/1#issuecomment-{comment_id}",
    )


def _make_comments(commands: list[str]) -> list[GitHubComment]:
    return [
        _make_comment(
            comment_id=index,
            body=f"@vv-ai {command}",
            author="Hiroshiba",
            created_at=f"2026-05-15T00:{index:02}:00Z",
        )
        for index, command in enumerate(commands, start=1)
    ]


def _resolve_github(
    target: ResolvedTarget,
    comments: list[GitHubComment],
    parent_number: int | None,
    comment_id: int | None,
) -> ResolvedCommand:
    github_client = MagicMock()
    github_client.list_issue_comments.return_value = comments
    github_client.get_issue_parent_number.return_value = parent_number
    command = _make_next_command(target, comment_id)
    with patch("vv_ai.next_command.build_github_client", return_value=github_client):
        return resolve_next_command(Path("/dummy"), command, _make_config())


def test_next以外は同じresolved_commandを返す() -> None:
    command = _make_command("confirm", _make_target("issue", "github"), None)

    result = resolve_next_command(Path("/dummy"), command, _make_config())

    assert result is command


def test_github通常issueの履歴なしnextはconfirmに解決される() -> None:
    result = _resolve_github(_make_target("issue", "github"), [], None, None)

    assert result.command == "confirm"


def test_githubサブissueの履歴なしnextはimplementに解決される() -> None:
    result = _resolve_github(_make_target("issue", "github"), [], 10, None)

    assert result.command == "implement"


def test_issueの設計工程を履歴から再生する() -> None:
    target = _make_target("issue", "github")

    result = _resolve_github(
        target,
        _make_comments(["confirm", "requirements", "arch", "detail"]),
        None,
        None,
    )

    assert result.command == "breakdown"


def test_issueのbreakdown後のnextは失敗する() -> None:
    with pytest.raises(NextResolutionError):
        _resolve_github(
            _make_target("issue", "github"),
            _make_comments(["confirm", "requirements", "arch", "detail", "breakdown"]),
            None,
            None,
        )


def test_issueのimplement後のnextは失敗する() -> None:
    with pytest.raises(NextResolutionError):
        _resolve_github(
            _make_target("issue", "github"),
            _make_comments(["implement"]),
            None,
            None,
        )


def test_prの履歴なしnextはreviewに解決される() -> None:
    result = _resolve_github(_make_target("pr", "github"), [], None, None)

    assert result.command == "review"


def test_prのreviewとimplementを履歴から再生する() -> None:
    result = _resolve_github(
        _make_target("pr", "github"),
        _make_comments(["review", "implement"]),
        None,
        None,
    )

    assert result.command == "review"


def test_過去の連続nextはその時点の履歴状態から実コマンド化される() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["next", "next", "next"]),
        None,
        None,
    )

    assert result.command == "detail"


def test_解決できない過去nextは履歴状態を更新しない() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(
            [
                "confirm",
                "requirements",
                "arch",
                "detail",
                "breakdown",
                "next",
                "confirm",
            ]
        ),
        None,
        None,
    )

    assert result.command == "requirements"


def test_allowed_users外のgithubコメントは履歴に入らない() -> None:
    comments = [
        _make_comment(1, "@vv-ai confirm", "other-user", "2026-05-15T00:01:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, None, None)

    assert result.command == "confirm"


def test_解析不能コメントは履歴に入らない() -> None:
    comments = [
        _make_comment(1, "hello", "Hiroshiba", "2026-05-15T00:01:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, None, None)

    assert result.command == "confirm"


def test_replyとissueは履歴に入らない() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["reply", "issue"]),
        None,
        None,
    )

    assert result.command == "confirm"


def test_issue履歴ではreviewを無視する() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["review"]),
        None,
        None,
    )

    assert result.command == "confirm"


def test_pr履歴では設計工程を無視する() -> None:
    result = _resolve_github(
        _make_target("pr", "github"),
        _make_comments(["confirm", "requirements", "arch", "detail", "breakdown"]),
        None,
        None,
    )

    assert result.command == "review"


def test_github_issue_comment起動では現在コメントより後を履歴に入れない() -> None:
    comments = [
        _make_comment(1, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(2, "@vv-ai next", "Hiroshiba", "2026-05-15T00:02:00Z"),
        _make_comment(3, "@vv-ai requirements", "Hiroshiba", "2026-05-15T00:03:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, None, 2)

    assert result.command == "requirements"


def test_同じcreated_atのコメントはidで境界を切る() -> None:
    comments = [
        _make_comment(10, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(11, "@vv-ai next", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(12, "@vv-ai requirements", "Hiroshiba", "2026-05-15T00:01:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, None, 11)

    assert result.command == "requirements"


def test_comment_idがあるのに現在コメントが見つからない場合は失敗する() -> None:
    with pytest.raises(NextResolutionError):
        _resolve_github(_make_target("issue", "github"), [], None, 999)


def test_comment_idがnoneの場合は履歴全体を使う() -> None:
    comments = [
        _make_comment(1, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(2, "@vv-ai requirements", "Hiroshiba", "2026-05-15T00:02:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, None, None)

    assert result.command == "arch"


def test_local_targetはcomments_mdをファイル名順に読む(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".vv-ai" / "issues" / "test"
    comments_dir = issue_dir / "comments"
    comments_dir.mkdir(parents=True)
    (comments_dir / "002.md").write_text("@vv-ai requirements", encoding="utf-8")
    (comments_dir / "001.md").write_text("@vv-ai confirm", encoding="utf-8")
    target = _make_target("issue", "local").model_copy(update={"path": str(issue_dir)})

    result = resolve_next_command(
        tmp_path,
        _make_next_command(target, None),
        _make_config(),
    )

    assert result.command == "arch"


def test_local_targetのcommentsディレクトリがない場合は失敗する(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".vv-ai" / "issues" / "test"
    issue_dir.mkdir(parents=True)
    target = _make_target("issue", "local").model_copy(update={"path": str(issue_dir)})

    with pytest.raises(NextResolutionError):
        resolve_next_command(tmp_path, _make_next_command(target, None), _make_config())
