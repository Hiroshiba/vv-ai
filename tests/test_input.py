"""入力正規化と validation の単体テスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vv_ai.inputs.build import (
    build_raw_input_from_cli,
    build_raw_input_from_issue_comment_event,
    build_raw_input_from_issue_labeled_event,
    build_raw_input_from_pull_request_event,
    build_raw_input_from_pull_request_label_event,
    build_raw_input_from_workflow_dispatch_event,
    parse_comment_invocation,
    parse_control_label_invocation,
    parse_label_invocation,
)
from vv_ai.inputs.models import (
    CLIInput,
    InputError,
    IssueCommentEvent,
    IssueLabeledEvent,
    PullRequestEvent,
    PullRequestLabelEvent,
    RawInput,
    WorkflowDispatchEvent,
)
from vv_ai.inputs.resolve import ResolutionError, resolve_raw_input


class TestParseCommentInvocation:
    def test_prefix_only(self) -> None:
        result = parse_comment_invocation("@vv-ai")
        assert result.command == "reply"
        assert result.instruction is None

    def test_prefix_with_trailing_space(self) -> None:
        result = parse_comment_invocation("@vv-ai ")
        assert result.command == "reply"
        assert result.instruction is None

    def test_command_reply_implicit(self) -> None:
        result = parse_comment_invocation("@vv-ai 要約して")
        assert result.command == "reply"
        assert result.instruction == "要約して"

    def test_command_reply_explicit_for_command_word(self) -> None:
        result = parse_comment_invocation("@vv-ai reply confirm について教えて")
        assert result.command == "reply"
        assert result.instruction == "confirm について教えて"

    def test_command_requirements(self) -> None:
        result = parse_comment_invocation("@vv-ai requirements")
        assert result.command == "requirements"
        assert result.instruction is None

    def test_command_arch(self) -> None:
        result = parse_comment_invocation("@vv-ai arch 基本設計してください")
        assert result.command == "arch"
        assert result.instruction == "基本設計してください"

    def test_command_detail(self) -> None:
        result = parse_comment_invocation("@vv-ai detail")
        assert result.command == "detail"

    def test_command_breakdown(self) -> None:
        result = parse_comment_invocation("@vv-ai breakdown")
        assert result.command == "breakdown"

    def test_command_implement(self) -> None:
        result = parse_comment_invocation("@vv-ai implement このIssueを実装して")
        assert result.command == "implement"
        assert result.instruction == "このIssueを実装して"

    def test_command_address(self) -> None:
        result = parse_comment_invocation("@vv-ai address")
        assert result.command == "address"
        assert result.instruction is None

    def test_command_review(self) -> None:
        result = parse_comment_invocation("@vv-ai review")
        assert result.command == "review"
        assert result.instruction is None

    def test_command_issue(self) -> None:
        result = parse_comment_invocation("@vv-ai issue この不具合をIssue化して")
        assert result.command == "issue"
        assert result.instruction == "この不具合をIssue化して"

    def test_command_issue_without_instruction(self) -> None:
        result = parse_comment_invocation("@vv-ai issue")
        assert result.command == "issue"
        assert result.instruction is None

    def test_command_next_without_instruction(self) -> None:
        result = parse_comment_invocation("@vv-ai next")
        assert result.command == "next"
        assert result.instruction is None

    def test_command_sync_without_instruction(self) -> None:
        result = parse_comment_invocation("@vv-ai sync")
        assert result.command == "sync"
        assert result.instruction is None

    def test_command_next_with_options(self) -> None:
        result = parse_comment_invocation(
            "@vv-ai next --provider codex --session_mode new --dry-run"
        )
        assert result.command == "next"
        assert result.provider == "codex"
        assert result.session_mode == "new"
        assert result.dry_run is True
        assert result.instruction is None

    def test_option_dry_run(self) -> None:
        result = parse_comment_invocation("@vv-ai implement --dry-run 修正して")
        assert result.command == "implement"
        assert result.dry_run is True
        assert result.instruction == "修正して"

    def test_option_provider(self) -> None:
        result = parse_comment_invocation("@vv-ai implement --provider codex 実装して")
        assert result.provider == "codex"

    def test_option_session_mode(self) -> None:
        result = parse_comment_invocation("@vv-ai review --session_mode new")
        assert result.session_mode == "new"

    def test_option_session_mode_inherit_or_new(self) -> None:
        result = parse_comment_invocation(
            "@vv-ai reply --session_mode inherit_or_new 要約して"
        )
        assert result.session_mode == "inherit_or_new"

    def test_option_repo(self) -> None:
        result = parse_comment_invocation("@vv-ai issue --repo org/repo Issue化して")
        assert result.repo == "org/repo"

    def test_error_double_dash_separator(self) -> None:
        with pytest.raises(InputError, match="未対応のオプションです: --"):
            parse_comment_invocation("@vv-ai implement -- --dry-run は指示文です")

    def test_multiple_options(self) -> None:
        result = parse_comment_invocation(
            "@vv-ai implement --provider claude --session_mode compact --dry-run 実装して"
        )
        assert result.command == "implement"
        assert result.provider == "claude"
        assert result.session_mode == "compact"
        assert result.dry_run is True
        assert result.instruction == "実装して"

    def test_error_legacy_session_option(self) -> None:
        with pytest.raises(InputError, match="未対応のオプションです: --session"):
            parse_comment_invocation("@vv-ai review --session new")

    def test_leading_whitespace(self) -> None:
        result = parse_comment_invocation("  @vv-ai arch 基本設計して")
        assert result.command == "arch"
        assert result.instruction == "基本設計して"

    def test_error_not_vv_ai_prefix(self) -> None:
        with pytest.raises(InputError):
            parse_comment_invocation("@other-bot hello")

    def test_error_vv_ai_like_prefix(self) -> None:
        with pytest.raises(InputError):
            parse_comment_invocation("@vv-aibot hello")

    def test_error_unknown_option(self) -> None:
        with pytest.raises(InputError):
            parse_comment_invocation("@vv-ai implement --unknown-opt val")

    def test_error_option_missing_value(self) -> None:
        with pytest.raises(InputError):
            parse_comment_invocation("@vv-ai implement --provider")

    def test_error_invalid_provider(self) -> None:
        with pytest.raises(InputError):
            parse_comment_invocation("@vv-ai implement --provider invalid")


class TestParseLabelInvocation:
    def test_label_confirm(self) -> None:
        assert parse_label_invocation("vv-ai:confirm") == "confirm"

    def test_label_next(self) -> None:
        assert parse_label_invocation("vv-ai:next") == "next"

    def test_label_address(self) -> None:
        assert parse_label_invocation("vv-ai:address") == "address"

    def test_label_sync(self) -> None:
        assert parse_label_invocation("vv-ai:sync") == "sync"

    def test_label_auto_is_not_command(self) -> None:
        with pytest.raises(InputError):
            parse_label_invocation("vv-ai:auto")

    def test_label_merge_is_not_command(self) -> None:
        with pytest.raises(InputError):
            parse_label_invocation("vv-ai:merge")

    @pytest.mark.parametrize(
        "label_name",
        ["bug", "vv-ai", "vv-ai:", "vv-ai:unknown"],
    )
    def test_error_invalid_label(self, label_name: str) -> None:
        with pytest.raises(InputError):
            parse_label_invocation(label_name)


class TestParseControlLabelInvocation:
    def test_label_auto(self) -> None:
        assert parse_control_label_invocation("vv-ai:auto") == "vv-ai:auto"

    def test_label_merge(self) -> None:
        assert parse_control_label_invocation("vv-ai:merge") == "vv-ai:merge"

    @pytest.mark.parametrize(
        "label_name",
        ["bug", "vv-ai:next", "vv-ai:unknown"],
    )
    def test_error_invalid_control_label(self, label_name: str) -> None:
        with pytest.raises(InputError):
            parse_control_label_invocation(label_name)


class TestBuildRawInputFromCli:
    def test_local_event(self) -> None:
        cli = CLIInput(
            command="reply",
            instruction="要約して",
            target_url="https://github.com/org/repo/issues/1",
        )
        raw = build_raw_input_from_cli(cli)
        assert raw.event_name == "local"
        assert raw.command == "reply"
        assert raw.instruction == "要約して"
        assert raw.target_url == "https://github.com/org/repo/issues/1"

    def test_dry_run(self) -> None:
        cli = CLIInput(
            command="arch",
            target_url="https://github.com/org/repo/issues/1",
            dry_run=True,
        )
        raw = build_raw_input_from_cli(cli)
        assert raw.dry_run is True

    def test_next_command(self) -> None:
        cli = CLIInput(
            command="next",
            target_url="https://github.com/org/repo/issues/1",
        )
        raw = build_raw_input_from_cli(cli)
        assert raw.command == "next"

    def test_sync_command(self) -> None:
        cli = CLIInput(
            command="sync",
            target_url="https://github.com/org/repo/pull/1",
        )
        raw = build_raw_input_from_cli(cli)
        assert raw.command == "sync"

    def test_event_file_rejects_direct_args(self) -> None:
        cli = CLIInput(
            event_file="/tmp/event.json",
            command="arch",
        )
        with pytest.raises(InputError):
            build_raw_input_from_cli(cli)


class TestBuildRawInputFromIssueCommentEvent:
    def _make_event(self, body: str, pr: bool) -> IssueCommentEvent:
        return IssueCommentEvent.model_validate({
            "comment": {"id": 100, "body": body, "user": {"login": "Hiroshiba"}},
            "issue": {
                "number": 42,
                "pull_request": {"url": "https://api.github.com/repos/org/repo/pulls/42"}
                if pr
                else None,
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })

    def test_issue_comment(self) -> None:
        event = self._make_event("@vv-ai arch 基本設計して", pr=False)
        raw = build_raw_input_from_issue_comment_event(event)
        assert raw.event_name == "issue_comment"
        assert raw.command == "arch"
        assert raw.target_type == "issue"
        assert raw.target_number == 42
        assert raw.repository_full_name == "org/repo"
        assert raw.actor == "Hiroshiba"
        assert raw.comment_id == 100

    def test_issue_comment_reply_implicit(self) -> None:
        event = self._make_event("@vv-ai 要約して", pr=False)
        raw = build_raw_input_from_issue_comment_event(event)
        assert raw.event_name == "issue_comment"
        assert raw.command == "reply"
        assert raw.instruction == "要約して"
        assert raw.target_type == "issue"
        assert raw.target_number == 42

    def test_pr_comment(self) -> None:
        event = self._make_event("@vv-ai review", pr=True)
        raw = build_raw_input_from_issue_comment_event(event)
        assert raw.target_type == "pr"


class TestBuildRawInputFromIssueLabeledEvent:
    def _make_event(self, label_name: str) -> IssueLabeledEvent:
        return IssueLabeledEvent.model_validate({
            "action": "labeled",
            "issue": {"number": 42, "updated_at": "2026-05-18T04:00:00Z"},
            "label": {"name": label_name},
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba", "id": 1},
        })

    def test_issue_labeled_confirm(self) -> None:
        raw = build_raw_input_from_issue_labeled_event(
            self._make_event("vv-ai:confirm")
        )
        assert raw.event_name == "issues"
        assert raw.command == "confirm"
        assert raw.instruction is None
        assert raw.provider is None
        assert raw.session_mode is None
        assert raw.dry_run is False
        assert raw.repo is None
        assert raw.target_type == "issue"
        assert raw.target_number == 42
        assert raw.repository_full_name == "org/repo"
        assert raw.actor == "Hiroshiba"
        assert raw.actor_id == 1
        assert raw.trigger_label_name == "vv-ai:confirm"
        assert raw.trigger_event_created_at == "2026-05-18T04:00:00Z"

    def test_issue_labeled_auto(self) -> None:
        raw = build_raw_input_from_issue_labeled_event(
            self._make_event("vv-ai:auto")
        )

        assert raw.command is None
        assert raw.control_label_name == "vv-ai:auto"
        assert raw.label_action == "labeled"
        assert raw.target_type == "issue"
        assert raw.target_number == 42
        assert raw.actor == "Hiroshiba"
        assert raw.actor_id == 1

    def test_issue_labeled_merge(self) -> None:
        raw = build_raw_input_from_issue_labeled_event(
            self._make_event("vv-ai:merge")
        )

        assert raw.command is None
        assert raw.control_label_name == "vv-ai:merge"
        assert raw.label_action == "labeled"
        assert raw.target_type == "issue"
        assert raw.target_number == 42
        assert raw.actor == "Hiroshiba"
        assert raw.actor_id == 1

    def test_issue_labeled_reply_without_instruction(self) -> None:
        raw = build_raw_input_from_issue_labeled_event(
            self._make_event("vv-ai:reply")
        )
        assert raw.command == "reply"
        assert raw.instruction is None

    def test_issue_labeled_issue_without_instruction(self) -> None:
        raw = build_raw_input_from_issue_labeled_event(
            self._make_event("vv-ai:issue")
        )
        assert raw.command == "issue"
        assert raw.instruction is None

    def test_issue_labeled_next_without_instruction(self) -> None:
        raw = build_raw_input_from_issue_labeled_event(
            self._make_event("vv-ai:next")
        )
        assert raw.command == "next"
        assert raw.instruction is None
        assert raw.target_type == "issue"
        assert raw.trigger_label_name == "vv-ai:next"

    def test_issue_labeled_rejects_review(self) -> None:
        with pytest.raises(InputError):
            build_raw_input_from_issue_labeled_event(
                self._make_event("vv-ai:review")
            )

    def test_issue_labeled_rejects_address(self) -> None:
        with pytest.raises(InputError):
            build_raw_input_from_issue_labeled_event(
                self._make_event("vv-ai:address")
            )

    def test_issue_labeled_rejects_sync(self) -> None:
        with pytest.raises(InputError):
            build_raw_input_from_issue_labeled_event(
                self._make_event("vv-ai:sync")
            )


class TestBuildRawInputFromPullRequestLabelEvent:
    def _make_event(
        self,
        label_name: str,
        action: str,
    ) -> PullRequestLabelEvent:
        return PullRequestLabelEvent.model_validate({
            "action": action,
            "pull_request": {"number": 43, "updated_at": "2026-05-18T04:00:00Z"},
            "label": {"name": label_name},
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba", "id": 1},
        })

    def test_pull_request_labeled_review(self) -> None:
        raw = build_raw_input_from_pull_request_label_event(
            self._make_event("vv-ai:review", "labeled")
        )
        assert raw.event_name == "pull_request"
        assert raw.command == "review"
        assert raw.instruction is None
        assert raw.target_type == "pr"
        assert raw.target_number == 43
        assert raw.repository_full_name == "org/repo"
        assert raw.actor == "Hiroshiba"
        assert raw.actor_id == 1
        assert raw.trigger_label_name == "vv-ai:review"
        assert raw.trigger_event_created_at == "2026-05-18T04:00:00Z"

    def test_pull_request_labeled_auto(self) -> None:
        raw = build_raw_input_from_pull_request_label_event(
            self._make_event("vv-ai:auto", "labeled")
        )

        assert raw.command is None
        assert raw.control_label_name == "vv-ai:auto"
        assert raw.label_action == "labeled"
        assert raw.target_type == "pr"
        assert raw.target_number == 43
        assert raw.actor == "Hiroshiba"
        assert raw.actor_id == 1

    def test_pull_request_labeled_merge(self) -> None:
        raw = build_raw_input_from_pull_request_label_event(
            self._make_event("vv-ai:merge", "labeled")
        )

        assert raw.command is None
        assert raw.control_label_name == "vv-ai:merge"
        assert raw.label_action == "labeled"
        assert raw.target_type == "pr"
        assert raw.target_number == 43
        assert raw.actor == "Hiroshiba"
        assert raw.actor_id == 1

    def test_pull_request_unlabeled_merge(self) -> None:
        raw = build_raw_input_from_pull_request_label_event(
            self._make_event("vv-ai:merge", "unlabeled")
        )

        assert raw.command is None
        assert raw.control_label_name == "vv-ai:merge"
        assert raw.label_action == "unlabeled"
        assert raw.target_type == "pr"
        assert raw.target_number == 43

    def test_pull_request_labeled_address(self) -> None:
        raw = build_raw_input_from_pull_request_label_event(
            self._make_event("vv-ai:address", "labeled")
        )
        assert raw.command == "address"
        assert raw.instruction is None
        assert raw.target_type == "pr"
        assert raw.trigger_label_name == "vv-ai:address"

    def test_pull_request_labeled_next_without_instruction(self) -> None:
        raw = build_raw_input_from_pull_request_label_event(
            self._make_event("vv-ai:next", "labeled")
        )
        assert raw.command == "next"
        assert raw.instruction is None
        assert raw.target_type == "pr"
        assert raw.trigger_label_name == "vv-ai:next"

    def test_pull_request_labeled_sync_without_instruction(self) -> None:
        raw = build_raw_input_from_pull_request_label_event(
            self._make_event("vv-ai:sync", "labeled")
        )
        assert raw.command == "sync"
        assert raw.instruction is None
        assert raw.target_type == "pr"
        assert raw.trigger_label_name == "vv-ai:sync"

    def test_pull_request_labeled_rejects_breakdown(self) -> None:
        with pytest.raises(InputError):
            build_raw_input_from_pull_request_label_event(
                self._make_event("vv-ai:breakdown", "labeled")
            )

    def test_pull_request_unlabeled_rejects_auto(self) -> None:
        with pytest.raises(InputError):
            build_raw_input_from_pull_request_label_event(
                self._make_event("vv-ai:auto", "unlabeled")
            )

    def test_pull_request_unlabeled_rejects_command_label(self) -> None:
        with pytest.raises(InputError):
            build_raw_input_from_pull_request_label_event(
                self._make_event("vv-ai:review", "unlabeled")
            )


class TestBuildRawInputFromPullRequestEvent:
    def test_pull_request_closed(self) -> None:
        event = PullRequestEvent.model_validate({
            "action": "closed",
            "pull_request": {
                "number": 43,
                "merged": True,
                "labels": [{"name": "vv-ai:auto"}],
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba", "id": 1},
        })

        raw = build_raw_input_from_pull_request_event(event)

        assert raw.event_name == "pull_request"
        assert raw.command is None
        assert raw.control_label_name is None
        assert raw.target_type == "pr"
        assert raw.target_number == 43
        assert raw.repository_full_name == "org/repo"
        assert raw.actor == "Hiroshiba"
        assert raw.actor_id == 1
        assert raw.pull_request_merged is True

    def test_pull_request_closed_requires_merged(self) -> None:
        event = PullRequestEvent.model_validate({
            "action": "closed",
            "pull_request": {"number": 43},
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba", "id": 1},
        })

        with pytest.raises(InputError, match="merged"):
            build_raw_input_from_pull_request_event(event)


class TestBuildRawInputFromEventFile:
    def test_auto_detect_issue_labeled_event(self, tmp_path: Path) -> None:
        event_file = tmp_path / "event.json"
        event_file.write_text(
            json.dumps({
                "action": "labeled",
                "issue": {"number": 42, "updated_at": "2026-05-18T04:00:00Z"},
                "label": {"name": "vv-ai:confirm"},
                "repository": {"full_name": "org/repo"},
                "sender": {"login": "Hiroshiba"},
            }),
            encoding="utf-8",
        )

        raw = build_raw_input_from_cli(CLIInput(event_file=event_file))

        assert raw.event_name == "issues"
        assert raw.command == "confirm"

    def test_auto_detect_pull_request_labeled_event(self, tmp_path: Path) -> None:
        event_file = tmp_path / "event.json"
        event_file.write_text(
            json.dumps({
                "action": "labeled",
                "pull_request": {"number": 43, "updated_at": "2026-05-18T04:00:00Z"},
                "label": {"name": "vv-ai:review"},
                "repository": {"full_name": "org/repo"},
                "sender": {"login": "Hiroshiba"},
            }),
            encoding="utf-8",
        )

        raw = build_raw_input_from_cli(CLIInput(event_file=event_file))

        assert raw.event_name == "pull_request"
        assert raw.command == "review"

    def test_auto_detect_pull_request_closed_event(self, tmp_path: Path) -> None:
        event_file = tmp_path / "event.json"
        event_file.write_text(
            json.dumps({
                "action": "closed",
                "pull_request": {"number": 43, "merged": True},
                "repository": {"full_name": "org/repo"},
                "sender": {"login": "Hiroshiba", "id": 1},
            }),
            encoding="utf-8",
        )

        raw = build_raw_input_from_cli(CLIInput(event_file=event_file))

        assert raw.event_name == "pull_request"
        assert raw.target_type == "pr"
        assert raw.target_number == 43
        assert raw.pull_request_merged is True


class TestBuildRawInputFromWorkflowDispatchEvent:
    def test_basic(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "implement",
                "target_type": "issue",
                "target_number": "10",
                "instruction": "実装して",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        raw = build_raw_input_from_workflow_dispatch_event(event)
        assert raw.event_name == "workflow_dispatch"
        assert raw.command == "implement"
        assert raw.target_type == "issue"
        assert raw.target_number == 10
        assert raw.instruction == "実装して"

    def test_address(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "address",
                "target_type": "pr",
                "target_number": "10",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        raw = build_raw_input_from_workflow_dispatch_event(event)
        assert raw.command == "address"
        assert raw.target_type == "pr"
        assert raw.target_number == 10

    def test_issue_without_target_and_repo(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "issue",
                "instruction": "Issue 化して",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        raw = build_raw_input_from_workflow_dispatch_event(event)
        assert raw.command == "issue"
        assert raw.instruction == "Issue 化して"
        assert raw.target_url is None
        assert raw.target_type is None
        assert raw.target_number is None
        assert raw.repo is None
        assert raw.repository_full_name == "org/repo"

    def test_empty_string_becomes_none(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "reply",
                "instruction": "  ",
                "target_url": "",
                "target_type": "",
                "target_number": "",
                "provider": "",
                "session_mode": "",
                "repo": "",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        raw = build_raw_input_from_workflow_dispatch_event(event)
        assert raw.command == "reply"
        assert raw.instruction is None
        assert raw.target_url is None
        assert raw.target_type is None
        assert raw.target_number is None
        assert raw.provider is None
        assert raw.session_mode is None
        assert raw.repo is None

    def test_empty_command_raises(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        with pytest.raises(InputError, match="command"):
            build_raw_input_from_workflow_dispatch_event(event)

    def test_next_command(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "next",
                "target_type": "issue",
                "target_number": "10",
                "provider": "codex",
                "session_mode": "new",
                "dry_run": "true",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        raw = build_raw_input_from_workflow_dispatch_event(event)
        assert raw.command == "next"
        assert raw.target_type == "issue"
        assert raw.target_number == 10
        assert raw.instruction is None
        assert raw.provider == "codex"
        assert raw.session_mode == "new"
        assert raw.dry_run is True

    def test_sync_command(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "sync",
                "target_type": "pr",
                "target_number": "10",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        raw = build_raw_input_from_workflow_dispatch_event(event)
        assert raw.command == "sync"
        assert raw.target_type == "pr"
        assert raw.target_number == 10


class TestResolveRawInput:
    def test_reply_requires_target(self) -> None:
        raw = RawInput(event_name="local", command="reply", instruction="要約して")
        with pytest.raises(ResolutionError, match="target"):
            resolve_raw_input(raw)

    def test_reply_allows_missing_instruction(self) -> None:
        raw = RawInput(
            event_name="local",
            command="reply",
            target_url="https://github.com/org/repo/issues/1",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "reply"
        assert resolved.instruction is None

    def test_issue_allows_missing_instruction(self) -> None:
        raw = RawInput(
            event_name="local",
            command="issue",
            repository_full_name="org/repo",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "issue"
        assert resolved.instruction is None
        assert resolved.has_target is False
        assert resolved.repo == "org/repo"

    def test_issue_does_not_require_target(self) -> None:
        raw = RawInput(
            event_name="local",
            command="issue",
            instruction="バグ報告",
            repository_full_name="org/repo",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "issue"
        assert resolved.has_target is False
        assert resolved.repo == "org/repo"

    def test_workflow_dispatch_issue_uses_event_repository_without_target(self) -> None:
        raw = RawInput(
            event_name="workflow_dispatch",
            command="issue",
            repository_full_name="org/repo",
            actor="Hiroshiba",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "issue"
        assert resolved.instruction is None
        assert resolved.has_target is False
        assert resolved.repo == "org/repo"

    def test_default_command_is_reply(self) -> None:
        raw = RawInput(
            event_name="local",
            instruction="要約して",
            target_url="https://github.com/org/repo/issues/1",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "reply"

    def test_target_url_sets_has_target(self) -> None:
        raw = RawInput(
            event_name="local",
            command="arch",
            target_url="https://github.com/org/repo/issues/1",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.has_target is True
        assert resolved.target_url == "https://github.com/org/repo/issues/1"

    def test_target_type_and_number(self) -> None:
        raw = RawInput(
            event_name="local",
            command="arch",
            target_type="pr",
            target_number=5,
        )
        resolved = resolve_raw_input(raw)
        assert resolved.has_target is True
        assert resolved.target_type == "pr"
        assert resolved.target_number == 5

    def test_target_type_without_number_raises(self) -> None:
        raw = RawInput(event_name="local", command="arch", target_type="issue")
        with pytest.raises(ResolutionError, match="target_type.*target_number"):
            resolve_raw_input(raw)

    def test_target_number_zero_raises(self) -> None:
        raw = RawInput(
            event_name="local",
            command="arch",
            target_type="issue",
            target_number=0,
        )
        with pytest.raises(ResolutionError, match="1 以上"):
            resolve_raw_input(raw)

    def test_issue_comment_requires_fields(self) -> None:
        raw = RawInput(event_name="issue_comment")
        with pytest.raises(ResolutionError, match="必須の項目が不足"):
            resolve_raw_input(raw)

    def test_workflow_dispatch_requires_fields(self) -> None:
        raw = RawInput(event_name="workflow_dispatch")
        with pytest.raises(ResolutionError, match="必須の項目が不足"):
            resolve_raw_input(raw)

    def test_issue_labeled_requires_fields(self) -> None:
        raw = RawInput(event_name="issues")
        with pytest.raises(ResolutionError, match="必須の項目が不足"):
            resolve_raw_input(raw)

    def test_pull_request_labeled_requires_fields(self) -> None:
        raw = RawInput(event_name="pull_request")
        with pytest.raises(ResolutionError, match="必須の項目が不足"):
            resolve_raw_input(raw)

    def test_labeled_trigger_label_name_is_resolved(self) -> None:
        raw = RawInput(
            event_name="issues",
            command="confirm",
            target_type="issue",
            target_number=42,
            repository_full_name="org/repo",
            actor="Hiroshiba",
            trigger_label_name="vv-ai:confirm",
            trigger_event_created_at="2026-05-18T04:00:00Z",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.trigger_label_name == "vv-ai:confirm"
        assert resolved.trigger_event_created_at == "2026-05-18T04:00:00Z"

    def test_empty_instruction_raises(self) -> None:
        raw = RawInput(
            event_name="local",
            command="arch",
            instruction="   ",
            target_url="https://github.com/org/repo/issues/1",
        )
        with pytest.raises(ResolutionError, match="instruction"):
            resolve_raw_input(raw)

    def test_issue_repo_fallback(self) -> None:
        raw = RawInput(
            event_name="local",
            command="issue",
            instruction="バグ報告",
            repository_full_name="fallback/repo",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.repo == "fallback/repo"

    def test_issue_repo_explicit_takes_priority(self) -> None:
        raw = RawInput(
            event_name="local",
            command="issue",
            instruction="バグ報告",
            repo="explicit/repo",
            repository_full_name="fallback/repo",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.repo == "explicit/repo"

    def test_issue_repo_empty_string_raises(self) -> None:
        raw = RawInput(
            event_name="local",
            command="issue",
            instruction="バグ報告",
            repo="",
            repository_full_name="fallback/repo",
        )
        with pytest.raises(ResolutionError, match="repo"):
            resolve_raw_input(raw)

    def test_issue_repo_blank_string_raises(self) -> None:
        raw = RawInput(
            event_name="local",
            command="issue",
            instruction="バグ報告",
            repo="   ",
            repository_full_name="fallback/repo",
        )
        with pytest.raises(ResolutionError, match="repo"):
            resolve_raw_input(raw)

    def test_breakdown_requires_target(self) -> None:
        raw = RawInput(event_name="local", command="breakdown")
        with pytest.raises(ResolutionError, match="target"):
            resolve_raw_input(raw)

    def test_sync_requires_target(self) -> None:
        raw = RawInput(event_name="local", command="sync")
        with pytest.raises(ResolutionError, match="target"):
            resolve_raw_input(raw)

    def test_address_requires_target(self) -> None:
        raw = RawInput(event_name="local", command="address")
        with pytest.raises(ResolutionError, match="target"):
            resolve_raw_input(raw)

    def test_address_allows_pr_target(self) -> None:
        raw = RawInput(
            event_name="local",
            command="address",
            target_type="pr",
            target_number=1,
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "address"
        assert resolved.has_target is True

    def test_sync_rejects_issue_target(self) -> None:
        raw = RawInput(
            event_name="local",
            command="sync",
            target_type="issue",
            target_number=1,
        )
        with pytest.raises(ResolutionError, match="PR 専用"):
            resolve_raw_input(raw)

    def test_sync_allows_pr_target(self) -> None:
        raw = RawInput(
            event_name="local",
            command="sync",
            target_type="pr",
            target_number=1,
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "sync"
        assert resolved.has_target is True

    def test_breakdown_ignores_repo_fallback(self) -> None:
        raw = RawInput(
            event_name="local",
            command="breakdown",
            target_url="https://github.com/org/repo/issues/1",
            repository_full_name="fallback/repo",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.repo is None

    def test_next_requires_target(self) -> None:
        raw = RawInput(event_name="local", command="next")
        with pytest.raises(ResolutionError, match="target"):
            resolve_raw_input(raw)

    def test_next_allows_missing_instruction_and_ignores_repo_fallback(self) -> None:
        raw = RawInput(
            event_name="local",
            command="next",
            target_url="https://github.com/org/repo/issues/1",
            repository_full_name="fallback/repo",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.command == "next"
        assert resolved.instruction is None
        assert resolved.repo is None
