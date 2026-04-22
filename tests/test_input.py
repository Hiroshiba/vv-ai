"""入力正規化と validation の単体テスト。"""

from __future__ import annotations

import pytest

from vv_ai.input import (
    CLIInput,
    CommentInvocation,
    InputError,
    IssueCommentEvent,
    RawInput,
    WorkflowDispatchEvent,
    build_raw_input_from_cli,
    build_raw_input_from_issue_comment_event,
    build_raw_input_from_workflow_dispatch_event,
    parse_comment_invocation,
)
from vv_ai.resolve import ResolutionError, resolve_raw_input


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

    def test_command_review(self) -> None:
        result = parse_comment_invocation("@vv-ai review")
        assert result.command == "review"
        assert result.instruction is None

    def test_command_issue(self) -> None:
        result = parse_comment_invocation("@vv-ai issue この不具合をIssue化して")
        assert result.command == "issue"
        assert result.instruction == "この不具合をIssue化して"

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

    def test_double_dash_separator(self) -> None:
        result = parse_comment_invocation("@vv-ai implement -- --dry-run は指示文です")
        assert result.command == "implement"
        assert result.dry_run is False
        assert result.instruction == "--dry-run は指示文です"

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

    def test_pr_comment(self) -> None:
        event = self._make_event("@vv-ai review", pr=True)
        raw = build_raw_input_from_issue_comment_event(event)
        assert raw.target_type == "pr"


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

    def test_empty_string_becomes_none(self) -> None:
        event = WorkflowDispatchEvent.model_validate({
            "inputs": {
                "command": "",
                "instruction": "  ",
                "target_url": "",
            },
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "Hiroshiba"},
        })
        raw = build_raw_input_from_workflow_dispatch_event(event)
        assert raw.command is None
        assert raw.instruction is None
        assert raw.target_url is None


class TestResolveRawInput:
    def test_reply_requires_target(self) -> None:
        raw = RawInput(event_name="local", command="reply", instruction="要約して")
        with pytest.raises(ResolutionError, match="target"):
            resolve_raw_input(raw)

    def test_reply_requires_instruction(self) -> None:
        raw = RawInput(
            event_name="local",
            command="reply",
            target_url="https://github.com/org/repo/issues/1",
        )
        with pytest.raises(ResolutionError, match="instruction"):
            resolve_raw_input(raw)

    def test_issue_requires_instruction(self) -> None:
        raw = RawInput(event_name="local", command="issue")
        with pytest.raises(ResolutionError, match="instruction"):
            resolve_raw_input(raw)

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

    def test_empty_instruction_normalized_to_none(self) -> None:
        raw = RawInput(
            event_name="local",
            command="arch",
            instruction="   ",
            target_url="https://github.com/org/repo/issues/1",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.instruction is None

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

    def test_breakdown_requires_target(self) -> None:
        raw = RawInput(event_name="local", command="breakdown")
        with pytest.raises(ResolutionError, match="target"):
            resolve_raw_input(raw)

    def test_breakdown_ignores_repo_fallback(self) -> None:
        raw = RawInput(
            event_name="local",
            command="breakdown",
            target_url="https://github.com/org/repo/issues/1",
            repository_full_name="fallback/repo",
        )
        resolved = resolve_raw_input(raw)
        assert resolved.repo is None
