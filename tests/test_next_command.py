"""next コマンド解決の単体テスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vv_ai.config import VVAIConfig
from vv_ai.backends.github.models import GitHubActor, GitHubIssueTimelineEvent
from vv_ai.next_decision import format_next_decision_history_comment
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


def _make_next_label_command(
    target: ResolvedTarget,
    trigger_event_created_at: str,
) -> ResolvedCommand:
    return ResolvedCommand(
        event_name="issues" if target.kind == "issue" else "pull_request",
        command="next",
        target_type=target.kind,
        target_number=target.number,
        has_target=True,
        repository_full_name=target.repository_full_name,
        actor="Hiroshiba",
        trigger_label_name="vv-ai:next",
        trigger_event_created_at=trigger_event_created_at,
        target=target,
    )


def _make_comment(
    comment_id: int,
    body: str,
    author: str,
    created_at: str,
) -> GitHubIssueTimelineEvent:
    return GitHubIssueTimelineEvent(
        id=comment_id,
        event="commented",
        actor=GitHubActor(login=author),
        created_at=created_at,
        body=body,
        label_name=None,
    )


def _make_comments(commands: list[str]) -> list[GitHubIssueTimelineEvent]:
    return [
        _make_comment(
            comment_id=index,
            body=f"@vv-ai {command}",
            author="Hiroshiba",
            created_at=f"2026-05-15T00:{index:02}:00Z",
        )
        for index, command in enumerate(commands, start=1)
    ]


def _make_next_decision_comment(
    comment_id: int,
    command: str,
    created_at: str,
) -> GitHubIssueTimelineEvent:
    return _make_comment(
        comment_id,
        format_next_decision_history_comment(command),
        "vv-ai-public-read-github-app[bot]",
        created_at,
    )


def _make_label_event(
    event_id: int,
    label_name: str,
    actor: str,
    created_at: str,
) -> GitHubIssueTimelineEvent:
    return GitHubIssueTimelineEvent(
        id=event_id,
        event="labeled",
        label_name=label_name,
        actor=GitHubActor(login=actor),
        created_at=created_at,
        body=None,
    )


def _make_label_events(commands: list[str]) -> list[GitHubIssueTimelineEvent]:
    return [
        _make_label_event(
            event_id=index,
            label_name=f"vv-ai:{command}",
            actor="Hiroshiba",
            created_at=f"2026-05-15T00:{index:02}:30Z",
        )
        for index, command in enumerate(commands, start=1)
    ]


def _resolve_github(
    target: ResolvedTarget,
    comments: list[GitHubIssueTimelineEvent],
    labeled_events: list[GitHubIssueTimelineEvent],
    parent_number: int | None,
    comment_id: int | None,
) -> ResolvedCommand:
    return _resolve_github_timeline(
        target,
        sorted([*comments, *labeled_events], key=lambda event: event.created_at),
        parent_number,
        _make_next_command(target, comment_id),
    )


def _resolve_github_label(
    target: ResolvedTarget,
    comments: list[GitHubIssueTimelineEvent],
    labeled_events: list[GitHubIssueTimelineEvent],
    parent_number: int | None,
) -> ResolvedCommand:
    timeline_events = sorted([*comments, *labeled_events], key=lambda event: event.created_at)
    next_events = [
        event
        for event in timeline_events
        if event.event == "labeled"
        and event.label_name == "vv-ai:next"
        and event.actor.login == "Hiroshiba"
    ]
    trigger_event_created_at = (
        "2026-05-15T00:00:00Z"
        if len(next_events) == 0
        else next_events[-1].created_at
    )
    return _resolve_github_timeline(
        target,
        timeline_events,
        parent_number,
        _make_next_label_command(target, trigger_event_created_at),
    )


def _resolve_github_label_at(
    target: ResolvedTarget,
    timeline_events: list[GitHubIssueTimelineEvent],
    parent_number: int | None,
    trigger_event_created_at: str,
) -> ResolvedCommand:
    return _resolve_github_timeline(
        target,
        timeline_events,
        parent_number,
        _make_next_label_command(target, trigger_event_created_at),
    )


def _resolve_github_timeline(
    target: ResolvedTarget,
    timeline_events: list[GitHubIssueTimelineEvent],
    parent_number: int | None,
    command: ResolvedCommand,
) -> ResolvedCommand:
    github_client = MagicMock()
    github_client.list_issue_timeline_events.return_value = timeline_events
    github_client.get_issue_parent_number.return_value = parent_number
    with patch("vv_ai.next_command.build_github_client", return_value=github_client):
        return resolve_next_command(Path("/dummy"), command, _make_config())


def test_next以外は同じresolved_commandを返す() -> None:
    command = _make_command("confirm", _make_target("issue", "github"), None)

    result = resolve_next_command(Path("/dummy"), command, _make_config())

    assert result is command


def test_github通常issueの履歴なしnextはconfirmに解決される() -> None:
    result = _resolve_github(_make_target("issue", "github"), [], [], None, None)

    assert result.command == "confirm"


def test_githubサブissueの履歴なしnextはimplementに解決される() -> None:
    result = _resolve_github(_make_target("issue", "github"), [], [], 10, None)

    assert result.command == "implement"


def test_issueのdetail後のnextはAI判断対象として残る() -> None:
    target = _make_target("issue", "github")

    result = _resolve_github(
        target,
        _make_comments(["confirm", "requirements", "arch", "detail"]),
        [],
        None,
        None,
    )

    assert result.command == "next"


def test_githubサブissueのdetail後nextはimplementに解決される() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["confirm", "requirements", "arch", "detail"]),
        [],
        10,
        None,
    )

    assert result.command == "implement"


def test_過去nextがAI判断対象なら履歴を更新しない() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["confirm", "requirements", "arch", "detail", "next", "confirm"]),
        [],
        None,
        None,
    )

    assert result.command == "requirements"


def test_過去nextのbreakdown判断結果は履歴として再生される() -> None:
    target = _make_target("issue", "github")

    with pytest.raises(NextResolutionError, match="breakdown 後"):
        _resolve_github_timeline(
            target,
            [
                *_make_comments(["confirm", "requirements", "arch", "detail", "next"]),
                _make_next_decision_comment(
                    100,
                    "breakdown",
                    "2026-05-15T00:06:00Z",
                ),
            ],
            None,
            _make_next_command(target, None),
        )


def test_過去nextのimplement判断結果は履歴として再生される() -> None:
    target = _make_target("issue", "github")

    with pytest.raises(NextResolutionError, match="implement 後"):
        _resolve_github_timeline(
            target,
            [
                *_make_comments(["confirm", "requirements", "arch", "detail", "next"]),
                _make_next_decision_comment(
                    100,
                    "implement",
                    "2026-05-15T00:06:00Z",
                ),
            ],
            None,
            _make_next_command(target, None),
        )


def test_humanのnext判断履歴コメントは無視される() -> None:
    target = _make_target("issue", "github")

    result = _resolve_github_timeline(
        target,
        [
            *_make_comments(["confirm", "requirements", "arch", "detail", "next"]),
            _make_comment(
                100,
                format_next_decision_history_comment("breakdown"),
                "Hiroshiba",
                "2026-05-15T00:06:00Z",
            ),
            _make_comment(101, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:07:00Z"),
        ],
        None,
        _make_next_command(target, None),
    )

    assert result.command == "requirements"


def test_issueのbreakdown後のnextは失敗する() -> None:
    with pytest.raises(NextResolutionError):
        _resolve_github(
            _make_target("issue", "github"),
            _make_comments(["confirm", "requirements", "arch", "detail", "breakdown"]),
            [],
            None,
            None,
        )


def test_issueのimplement後のnextは失敗する() -> None:
    with pytest.raises(NextResolutionError):
        _resolve_github(
            _make_target("issue", "github"),
            _make_comments(["implement"]),
            [],
            None,
            None,
        )


def test_prの履歴なしnextはreviewに解決される() -> None:
    result = _resolve_github(_make_target("pr", "github"), [], [], None, None)

    assert result.command == "review"


def test_prのreview後のnextはaddressに解決される() -> None:
    result = _resolve_github(
        _make_target("pr", "github"),
        _make_comments(["review"]),
        [],
        None,
        None,
    )

    assert result.command == "address"


def test_prのaddress後のnextはreviewに解決される() -> None:
    result = _resolve_github(
        _make_target("pr", "github"),
        _make_comments(["address"]),
        [],
        None,
        None,
    )

    assert result.command == "review"


def test_prのreviewとimplementを履歴から再生する() -> None:
    result = _resolve_github(
        _make_target("pr", "github"),
        _make_comments(["review", "implement"]),
        [],
        None,
        None,
    )

    assert result.command == "review"


def test_過去の連続nextはその時点の履歴状態から実コマンド化される() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["next", "next", "next"]),
        [],
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
        [],
        None,
        None,
    )

    assert result.command == "requirements"


def test_allowed_users外のgithubコメントは履歴に入らない() -> None:
    comments = [
        _make_comment(1, "@vv-ai confirm", "other-user", "2026-05-15T00:01:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, [], None, None)

    assert result.command == "confirm"


def test_解析不能コメントは履歴に入らない() -> None:
    comments = [
        _make_comment(1, "hello", "Hiroshiba", "2026-05-15T00:01:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, [], None, None)

    assert result.command == "confirm"


def test_replyとissueは履歴に入らない() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["reply", "issue"]),
        [],
        None,
        None,
    )

    assert result.command == "confirm"


def test_issue履歴ではreviewを無視する() -> None:
    result = _resolve_github(
        _make_target("issue", "github"),
        _make_comments(["review"]),
        [],
        None,
        None,
    )

    assert result.command == "confirm"


def test_pr履歴では設計工程を無視する() -> None:
    result = _resolve_github(
        _make_target("pr", "github"),
        _make_comments(["confirm", "requirements", "arch", "detail", "breakdown"]),
        [],
        None,
        None,
    )

    assert result.command == "review"


def test_pr履歴ではsyncを無視する() -> None:
    result = _resolve_github(
        _make_target("pr", "github"),
        _make_comments(["sync"]),
        [],
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

    result = _resolve_github(_make_target("issue", "github"), comments, [], None, 2)

    assert result.command == "requirements"


def test_同じcreated_atのコメントはidで境界を切る() -> None:
    comments = [
        _make_comment(10, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(11, "@vv-ai next", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(12, "@vv-ai requirements", "Hiroshiba", "2026-05-15T00:01:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, [], None, 11)

    assert result.command == "requirements"


def test_comment_idがあるのに現在コメントが見つからない場合は失敗する() -> None:
    with pytest.raises(NextResolutionError):
        _resolve_github(_make_target("issue", "github"), [], [], None, 999)


def test_comment_idがnoneの場合は履歴全体を使う() -> None:
    comments = [
        _make_comment(1, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(2, "@vv-ai requirements", "Hiroshiba", "2026-05-15T00:02:00Z"),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, [], None, None)

    assert result.command == "arch"


def test_issueのラベルrequirements後のnextラベルはarchに解決される() -> None:
    result = _resolve_github_label(
        _make_target("issue", "github"),
        [],
        _make_label_events(["requirements", "next"]),
        None,
    )

    assert result.command == "arch"


def test_commentとラベル履歴をcreated_at順に再生する() -> None:
    comments = [
        _make_comment(1, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(2, "@vv-ai arch", "Hiroshiba", "2026-05-15T00:03:00Z"),
    ]
    labeled_events = [
        _make_label_event(
            10,
            "vv-ai:requirements",
            "Hiroshiba",
            "2026-05-15T00:02:00Z",
        ),
        _make_label_event(11, "vv-ai:next", "Hiroshiba", "2026-05-15T00:04:00Z"),
    ]

    result = _resolve_github_label(
        _make_target("issue", "github"),
        comments,
        labeled_events,
        None,
    )

    assert result.command == "detail"


def test_ラベルrequirements後のコメントnextはarchに解決される() -> None:
    comments = [
        _make_comment(2, "@vv-ai next", "Hiroshiba", "2026-05-15T00:02:00Z"),
    ]
    labeled_events = [
        _make_label_event(
            10,
            "vv-ai:requirements",
            "Hiroshiba",
            "2026-05-15T00:01:00Z",
        ),
    ]

    result = _resolve_github(_make_target("issue", "github"), comments, labeled_events, None, 2)

    assert result.command == "arch"


def test_同じcreated_atでラベルがコメントより前ならラベルを履歴に入れる() -> None:
    timeline_events = [
        _make_label_event(
            10,
            "vv-ai:requirements",
            "Hiroshiba",
            "2026-05-15T00:01:00Z",
        ),
        _make_comment(2, "@vv-ai next", "Hiroshiba", "2026-05-15T00:01:00Z"),
    ]

    result = _resolve_github_timeline(
        _make_target("issue", "github"),
        timeline_events,
        None,
        _make_next_command(_make_target("issue", "github"), 2),
    )

    assert result.command == "arch"


def test_過去のnextラベルはその時点の履歴状態から実コマンド化される() -> None:
    result = _resolve_github_label(
        _make_target("issue", "github"),
        [],
        _make_label_events(["next", "next", "next", "next"]),
        None,
    )

    assert result.command == "detail"


def test_prのreviewラベル後のnextラベルはaddressに解決される() -> None:
    result = _resolve_github_label(
        _make_target("pr", "github"),
        [],
        _make_label_events(["review", "next"]),
        None,
    )

    assert result.command == "address"


def test_prのaddressラベル後のnextラベルはreviewに解決される() -> None:
    result = _resolve_github_label(
        _make_target("pr", "github"),
        [],
        _make_label_events(["address", "next"]),
        None,
    )

    assert result.command == "review"


def test_prのimplementラベル後のnextラベルはreviewに解決される() -> None:
    result = _resolve_github_label(
        _make_target("pr", "github"),
        [],
        _make_label_events(["implement", "next"]),
        None,
    )

    assert result.command == "review"


def test_allowed_users外のgithubラベルは履歴に入らない() -> None:
    labeled_events = [
        _make_label_event(
            10,
            "vv-ai:requirements",
            "other-user",
            "2026-05-15T00:01:00Z",
        ),
        _make_label_event(11, "vv-ai:next", "Hiroshiba", "2026-05-15T00:02:00Z"),
    ]

    result = _resolve_github_label(
        _make_target("issue", "github"),
        [],
        labeled_events,
        None,
    )

    assert result.command == "confirm"


def test_古いnextラベルrunは後続の再付与ラベルを履歴に入れない() -> None:
    target = _make_target("issue", "github")
    timeline_events = [
        _make_label_event(10, "vv-ai:next", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_label_event(
            11,
            "vv-ai:requirements",
            "Hiroshiba",
            "2026-05-15T00:02:00Z",
        ),
        _make_label_event(12, "vv-ai:next", "Hiroshiba", "2026-05-15T00:03:00Z"),
    ]

    result = _resolve_github_label_at(
        target,
        timeline_events,
        None,
        "2026-05-15T00:01:00Z",
    )

    assert result.command == "confirm"


def test_nextラベル境界が一意に決まらない場合は失敗する() -> None:
    target = _make_target("issue", "github")
    timeline_events = [
        _make_label_event(10, "vv-ai:next", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_label_event(11, "vv-ai:next", "Hiroshiba", "2026-05-15T00:01:00Z"),
    ]

    with pytest.raises(NextResolutionError, match="一意"):
        _resolve_github_label_at(
            target,
            timeline_events,
            None,
            "2026-05-15T00:01:00Z",
        )


def test_vv_ai以外のラベルは履歴に入らない() -> None:
    labeled_events = [
        _make_label_event(10, "bug", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_label_event(11, "vv-ai:next", "Hiroshiba", "2026-05-15T00:02:00Z"),
    ]

    result = _resolve_github_label(
        _make_target("issue", "github"),
        [],
        labeled_events,
        None,
    )

    assert result.command == "confirm"


def test_github_issue_comment起動では現在コメントより後のラベルを履歴に入れない() -> None:
    comments = [
        _make_comment(1, "@vv-ai confirm", "Hiroshiba", "2026-05-15T00:01:00Z"),
        _make_comment(2, "@vv-ai next", "Hiroshiba", "2026-05-15T00:02:00Z"),
    ]
    labeled_events = [
        _make_label_event(
            10,
            "vv-ai:requirements",
            "Hiroshiba",
            "2026-05-15T00:03:00Z",
        ),
    ]

    result = _resolve_github(
        _make_target("issue", "github"),
        comments,
        labeled_events,
        None,
        2,
    )

    assert result.command == "requirements"


def test_nextラベル起動では現在ラベル自身を履歴に入れない() -> None:
    result = _resolve_github_label(
        _make_target("issue", "github"),
        [],
        _make_label_events(["requirements", "next"]),
        None,
    )

    assert result.command == "arch"


def test_nextラベル起動で現在ラベルが見つからない場合は失敗する() -> None:
    with pytest.raises(NextResolutionError):
        _resolve_github_label(
            _make_target("issue", "github"),
            [],
            [],
            None,
        )


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
