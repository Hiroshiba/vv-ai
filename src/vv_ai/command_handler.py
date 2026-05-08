"""コマンドディスパッチ・reaction ハンドリング・後処理。"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from vv_ai.execution import ExecutionResult, ExecutionStatus
from vv_ai.git_ops import (
    GitOpsError,
    checkout_fork_pr,
    checkout_ref,
    commit_all_changes,
    create_and_checkout_branch,
    fetch_and_checkout_branch,
    fetch_remote,
    generate_implement_branch_name,
    generate_patch,
    get_head_sha,
    has_commits_ahead,
    push_branch,
    setup_upstream_remote,
    try_push_current_branch,
)
from vv_ai.github import (
    GitHubClient,
    GitHubClientError,
    GitHubIssue,
    GitHubPullRequest,
    GitHubReactionContent,
    build_github_client,
)
from vv_ai.preflight import ReadyExecution
from vv_ai.prompt import build_provider_prompt
from vv_ai.provider_execution import execute_provider
from vv_ai.resolve import ResolvedTarget


_ISSUE_CONTEXT_COMMANDS = frozenset(
    {"confirm", "reply", "requirements", "arch", "detail", "breakdown"}
)


def run_command(
    repo_root: Path,
    ready_execution: ReadyExecution,
    env: Mapping[str, str],
    preflight_duration_seconds: float,
) -> tuple[ExecutionResult, GitHubPullRequest | None]:
    """コマンド固有の前処理・provider 実行・後処理を行って実行結果と作成された PR を返す。"""
    command = ready_execution.command
    target = command.target

    if command.command == "review" and (target is None or target.kind != "pr"):
        raise RuntimeError("`review` コマンドは PR を対象に指定してください")

    if command.command == "breakdown" and (
        target is None or target.kind != "issue" or target.backend != "github"
    ):
        raise RuntimeError("`breakdown` コマンドは GitHub Issue を対象に指定してください")

    github_client = (
        build_github_client()
        if _is_github_target(target) or command.command in {"issue", "breakdown"}
        else None
    )

    eyes_reaction_id: int | None = None
    if (
        github_client is not None
        and not command.dry_run
        and command.comment_id is not None
    ):
        assert target is not None
        assert target.repository_full_name is not None
        eyes_reaction_id = _add_reaction_safe(
            github_client,
            target.repository_full_name,
            command.comment_id,
            "eyes",
        )

    past_vvai_comments = _fetch_past_vvai_comments(github_client, target)

    implement_branch_name: str | None = None
    pr_info: GitHubPullRequest | None = None
    head_sha_before: str | None = None
    fork_base_ref: str | None = None
    worktree_ref: str | None = None
    execution_result: ExecutionResult | None = None
    created_pr: GitHubPullRequest | None = None
    finalize_status: ExecutionStatus = "failure"
    try:
        if command.command == "implement" and target is not None and target.kind == "issue":
            issue_identifier = str(target.number) if target.number is not None else target.local_id
            assert issue_identifier is not None
            implement_branch_name = generate_implement_branch_name(issue_identifier)
            start_point: str | None = None
            if _is_github_target(target):
                assert github_client is not None
                fork_base_ref = _resolve_fork_base_ref(repo_root, target, github_client)
                start_point = fork_base_ref
            create_and_checkout_branch(repo_root, implement_branch_name, start_point)
        elif (
            command.command in _ISSUE_CONTEXT_COMMANDS
            and target is not None
            and target.kind == "issue"
            and _is_github_target(target)
        ):
            assert github_client is not None
            worktree_ref = _resolve_fork_base_ref(repo_root, target, github_client)
            if worktree_ref is not None:
                checkout_ref(repo_root, worktree_ref)
        elif command.command == "implement" and target is not None and target.kind == "pr":
            assert target.repository_full_name is not None
            assert target.number is not None
            if not _is_github_target(target):
                raise RuntimeError("ローカル PR への implement は未対応です")
            assert github_client is not None
            pr_info = github_client.get_pull_request(
                target.repository_full_name, target.number
            )
            implement_branch_name = pr_info.head_ref_name
            if pr_info.is_cross_repository:
                checkout_fork_pr(
                    repo_root, target.repository_full_name, target.number
                )
            else:
                fetch_and_checkout_branch(repo_root, implement_branch_name)

        if pr_info is not None and pr_info.is_cross_repository:
            head_sha_before = get_head_sha(repo_root)

        provider_prompt = build_provider_prompt(
            ready_execution, past_vvai_comments, implement_branch_name, worktree_ref
        )
        execution_result = execute_provider(
            repo_root,
            ready_execution,
            env,
            preflight_duration_seconds,
            provider_prompt,
        )

        created_pr = _handle_post_execution(
            repo_root,
            ready_execution,
            execution_result,
            github_client,
            implement_branch_name,
            pr_info,
            head_sha_before,
            fork_base_ref,
            env,
        )
        assert execution_result is not None
        finalize_status = execution_result.status
    finally:
        if (
            github_client is not None
            and not command.dry_run
            and command.comment_id is not None
        ):
            assert target is not None
            assert target.repository_full_name is not None
            _finalize_reactions(
                github_client,
                target.repository_full_name,
                command.comment_id,
                eyes_reaction_id,
                finalize_status,
            )

    assert execution_result is not None
    return execution_result, created_pr


def _is_github_target(target: ResolvedTarget | None) -> bool:
    """GitHub backend の target かどうかを返す。"""
    return target is not None and target.backend == "github"


def _resolve_fork_base_ref(
    repo_root: Path,
    target: ResolvedTarget,
    github_client: GitHubClient,
) -> str | None:
    """fork repo なら parent default branch の ref を返し、必要な remote を準備する。"""
    assert target.repository_full_name is not None
    repo_info = github_client.get_repo_info(target.repository_full_name)
    if not repo_info.is_fork:
        return None

    assert repo_info.parent_full_name is not None
    assert repo_info.parent_default_branch is not None
    upstream_url = f"https://github.com/{repo_info.parent_full_name}"
    setup_upstream_remote(repo_root, upstream_url)
    fetch_remote(repo_root, "upstream")
    return f"upstream/{repo_info.parent_default_branch}"


def _fetch_past_vvai_comments(
    github_client: GitHubClient | None,
    target: ResolvedTarget | None,
) -> list[str]:
    """過去の @vv-ai コメントを取得する。"""
    if github_client is None or target is None:
        return []
    if target.repository_full_name is None or target.number is None:
        return []
    try:
        comments = github_client.list_issue_comments(
            target.repository_full_name, target.number
        )
    except GitHubClientError as exc:
        print(f"過去コメント取得に失敗しました: {exc}", file=sys.stderr)
        return []
    return [c.body for c in comments if c.body.startswith("@vv-ai")]


def _handle_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str | None,
    pr_info: GitHubPullRequest | None,
    head_sha_before: str | None,
    fork_base_ref: str | None,
    env: Mapping[str, str],
) -> GitHubPullRequest | None:
    """コマンド固有の後処理を行う。作成された PR があれば返す。"""
    command_name = ready_execution.command.command
    if command_name in ("reply", "review", "confirm", "requirements", "arch", "detail"):
        _post_response_comment(ready_execution, execution_result, github_client)
    elif command_name == "breakdown":
        _handle_breakdown_post_execution(repo_root, ready_execution, execution_result, github_client)
    elif command_name == "issue":
        _handle_issue_post_execution(ready_execution, execution_result, github_client)
    elif command_name == "implement" and implement_branch_name is not None:
        target = ready_execution.command.target
        if target is not None and target.kind == "pr":
            _handle_implement_pr_post_execution(
                repo_root,
                ready_execution,
                execution_result,
                github_client,
                implement_branch_name,
                pr_info,
                head_sha_before,
                env,
            )
        else:
            return _handle_implement_issue_post_execution(
                repo_root, ready_execution, execution_result, github_client, implement_branch_name, fork_base_ref, env
            )
    return None


def _handle_implement_issue_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str,
    fork_base_ref: str | None,
    env: Mapping[str, str],
) -> GitHubPullRequest | None:
    """implement + Issue 起点の後処理（push + PR 作成）を行う。作成した PR を返す。"""
    command = ready_execution.command
    target = command.target

    if execution_result.status != "success":
        return None

    if command.dry_run or not _is_github_target(target):
        print(f"[dry-run/local] push と PR 作成をスキップします。ブランチ: {implement_branch_name}")
        return None

    assert target is not None
    assert target.repository_full_name is not None
    assert target.number is not None
    assert github_client is not None

    base_branch = ready_execution.config.pull_request_target_branch
    if base_branch is None:
        base_branch = github_client.get_default_branch(target.repository_full_name)

    commit_message = f"vv-ai: implement for #{target.number}"
    committed = commit_all_changes(repo_root, commit_message)
    if committed:
        print(f"ワーキングツリーの変更をコミットしました: {commit_message}")

    commits_ahead_ref = fork_base_ref if fork_base_ref is not None else base_branch
    ahead = has_commits_ahead(repo_root, commits_ahead_ref)
    if not ahead:
        print("変更コミットがないため push と PR 作成をスキップします")
        return None

    push_branch(repo_root, implement_branch_name, env.get("GITHUB_TOKEN"))

    issue = github_client.get_issue(target.repository_full_name, target.number)
    pr_title = issue.title
    pr_body = f"Closes #{target.number}"

    pr = github_client.create_pull_request(
        target.repository_full_name,
        pr_title,
        pr_body,
        implement_branch_name,
        base_branch,
        maintainer_can_modify=True,
    )
    print(f"PR を作成しました: {pr.url}")
    return pr


def _handle_implement_pr_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    implement_branch_name: str,
    pr_info: GitHubPullRequest | None,
    head_sha_before: str | None,
    env: Mapping[str, str],
) -> None:
    """implement + PR 起点の後処理（push / patch fallback）を行う。"""
    command = ready_execution.command
    target = command.target

    if execution_result.status != "success":
        return

    if command.dry_run or not _is_github_target(target):
        print(f"[dry-run/local] push をスキップします。ブランチ: {implement_branch_name}")
        return

    assert target is not None
    assert target.number is not None

    commit_message = f"vv-ai: implement for PR #{target.number}"
    committed = commit_all_changes(repo_root, commit_message)
    if committed:
        print(f"ワーキングツリーの変更をコミットしました: {commit_message}")

    if pr_info is None or not pr_info.is_cross_repository:
        push_branch(repo_root, implement_branch_name, env.get("GITHUB_TOKEN"))
        print(f"ブランチ `{implement_branch_name}` を push しました。")
        assert target.repository_full_name is not None
        _post_implement_response_comment(
            ready_execution,
            execution_result,
            github_client,
            target.repository_full_name,
            target.number,
        )
        return

    assert target.repository_full_name is not None

    if try_push_current_branch(repo_root, env.get("GITHUB_TOKEN")):
        print(f"fork ブランチ `{implement_branch_name}` を push しました。")
        _post_implement_response_comment(
            ready_execution,
            execution_result,
            github_client,
            target.repository_full_name,
            target.number,
        )
        return

    _post_fork_patch_fallback(
        repo_root,
        ready_execution,
        execution_result,
        github_client,
        target.repository_full_name,
        target.number,
        pr_info,
        head_sha_before,
    )


def _post_fork_patch_fallback(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    repository_full_name: str,
    number: int,
    pr_info: GitHubPullRequest,
    head_sha_before: str | None,
) -> None:
    """fork PR への push 失敗時に patch コメントを投稿する。"""
    if github_client is None:
        github_client = build_github_client()

    base_sha = head_sha_before if head_sha_before is not None else "HEAD~1"
    try:
        patch = generate_patch(repo_root, base_sha)
    except GitOpsError as exc:
        print(f"patch 生成に失敗しました: {exc}", file=sys.stderr)
        return

    if not patch.strip():
        print("fork PR への push に失敗し、patch も空のため投稿をスキップします。")
        return

    notice_already_posted = _get_allow_edits_notice_posted(ready_execution)
    notice = ""
    if not pr_info.maintainer_can_modify and not notice_already_posted:
        notice = (
            "\n\n---\n"
            "**Note**: この PR で \"Allow edits from maintainers\" を有効にすると、"
            "次回以降 vv-ai が直接修正をプッシュできるようになります。"
            "PR の右サイドバー下部にあるチェックボックスから設定できます。"
        )
        execution_result.allow_edits_notice_posted = True

    truncated = patch[:60000]
    response_text = execution_result.response_text
    response_block = ""
    if response_text is not None:
        response_block = f"{response_text}\n\n---\n\n"

    body = (
        "fork リポジトリへの push ができなかったため、変更内容を patch として提示します。\n\n"
        f"{response_block}"
        f"```diff\n{truncated}\n```"
        f"{notice}"
    )

    try:
        github_client.create_issue_comment(repository_full_name, number, body)
    except GitHubClientError as exc:
        print(f"patch コメント投稿に失敗しました: {exc}", file=sys.stderr)
        return
    print("fork PR への push に失敗したため、patch をコメントで投稿しました。")


def _get_allow_edits_notice_posted(ready_execution: ReadyExecution) -> bool:
    """復元済み session から allow_edits_notice_posted を取得する。"""
    session = ready_execution.resolved_session
    if session is None:
        return False
    return session.allow_edits_notice_posted


def _post_implement_response_comment(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
    repository_full_name: str,
    number: int,
) -> None:
    """PR 起点 implement の応答テキストを PR にコメント投稿する。"""
    response_text = execution_result.response_text
    if response_text is None:
        return

    if ready_execution.command.dry_run or github_client is None:
        print(response_text)
        return

    try:
        github_client.create_issue_comment(repository_full_name, number, response_text)
    except GitHubClientError as exc:
        print(f"implement 応答コメント投稿に失敗しました: {exc}", file=sys.stderr)


def _parse_issue_output(response_text: str) -> tuple[str, str]:
    """AI の出力から Issue タイトルと本文を抽出する。"""
    lines = response_text.split("\n")

    if not lines or not lines[0].startswith("TITLE:"):
        raise RuntimeError(
            "AI 出力が期待するフォーマットではありません。1行目は `TITLE: <タイトル>` である必要があります"
        )

    title = lines[0][len("TITLE:"):].strip()
    if not title:
        raise RuntimeError("AI 出力の TITLE が空です")

    if len(lines) < 2 or lines[1].strip() != "BODY:":
        raise RuntimeError(
            "AI 出力が期待するフォーマットではありません。2行目は `BODY:` である必要があります"
        )

    body = "\n".join(lines[2:])
    return title, body


def _handle_issue_post_execution(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    """issue コマンドの後処理（Issue 作成）を行う。"""
    command = ready_execution.command
    response_text = execution_result.response_text

    if execution_result.status != "success":
        return

    if response_text is None:
        raise RuntimeError("AI からの応答がありません")

    title, body = _parse_issue_output(response_text)
    repo = command.repo
    if repo is None:
        raise RuntimeError("Issue 作成先リポジトリが不明です")

    if command.dry_run:
        print(f"[dry-run] Issue 作成をスキップします。repo: {repo}, title: {title}")
        return

    assert github_client is not None
    issue = github_client.create_issue(repo, title, body)

    print(f"Issue を作成しました: {issue.url}")

    target = command.target
    if (
        command.comment_id is not None
        and target is not None
        and target.repository_full_name is not None
        and target.number is not None
    ):
        try:
            github_client.create_issue_comment(
                target.repository_full_name,
                target.number,
                f"Created: {issue.url}",
            )
        except GitHubClientError as exc:
            print(f"Issue リンクのコメント投稿に失敗しました: {exc}", file=sys.stderr)


def _parse_single_task_file(content: str, filename: str) -> tuple[str, str]:
    """タスクファイルの内容から (title, body) を抽出する。"""
    lines = content.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        raise RuntimeError(f"タスクファイルが空です: {filename}")
    title_line = non_empty[0]
    if not title_line.startswith("TITLE:"):
        raise RuntimeError(
            f"{filename}: 先頭行は `TITLE: <タイトル>` である必要があります"
        )
    title = title_line[len("TITLE:"):].strip()
    if not title:
        raise RuntimeError(f"{filename}: TITLE が空です")
    body_index = None
    for i, line in enumerate(lines):
        if line.strip() == "BODY:":
            body_index = i
            break
    if body_index is None:
        raise RuntimeError(f"{filename}: TITLE の後に `BODY:` が必要です")
    body = "\n".join(lines[body_index + 1:])
    return title, body


def _parse_breakdown_dir(
    response_text: str, repo_root: Path
) -> list[tuple[str, str]]:
    """AI の出力からタスクディレクトリを特定し、(title, body) リストを返す。"""
    breakdown_dir: Path | None = None
    for line in response_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("BREAKDOWN_DIR:"):
            dir_path = stripped[len("BREAKDOWN_DIR:"):].strip()
            breakdown_dir = Path(dir_path)
            break
    if breakdown_dir is None:
        raise RuntimeError("AI 出力に BREAKDOWN_DIR: が含まれていません")
    if not breakdown_dir.is_absolute():
        breakdown_dir = repo_root / breakdown_dir
    breakdown_dir = breakdown_dir.resolve()
    hiho_temp = (repo_root / "hiho_temp").resolve()
    if not breakdown_dir.is_relative_to(hiho_temp):
        raise RuntimeError(
            f"タスクディレクトリは hiho_temp 配下である必要があります: {breakdown_dir}"
        )
    if not breakdown_dir.is_dir():
        raise RuntimeError(f"タスクディレクトリが存在しません: {breakdown_dir}")
    md_files = sorted(breakdown_dir.glob("*.md"))
    if not md_files:
        raise RuntimeError(f"タスクディレクトリにファイルがありません: {breakdown_dir}")
    tasks = []
    for md_file in md_files:
        if md_file.is_symlink():
            raise RuntimeError(f"タスクファイルがシンボリックリンクです: {md_file.name}")
        content = md_file.read_text(encoding="utf-8")
        task = _parse_single_task_file(content, md_file.name)
        tasks.append(task)
    return tasks


def _handle_breakdown_post_execution(
    repo_root: Path,
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    """breakdown コマンドの後処理（複数サブ Issue 作成）を行う。"""
    command = ready_execution.command
    response_text = execution_result.response_text

    if execution_result.status != "success":
        return

    if response_text is None:
        raise RuntimeError("AI からの応答がありません")

    tasks = _parse_breakdown_dir(response_text, repo_root)
    target = command.target

    if command.dry_run:
        repo = command.repo or (target.repository_full_name if target else None) or "(不明)"
        print(f"[dry-run] サブ Issue 作成をスキップします。repo: {repo}, タスク数: {len(tasks)}")
        for title, _ in tasks:
            print(f"  - {title}")
        return

    assert github_client is not None
    assert target is not None
    assert target.number is not None
    assert target.repository_full_name is not None

    repo = target.repository_full_name

    created: list[GitHubIssue] = []
    for title, body in tasks:
        issue = github_client.create_issue(repo, title, body)
        try:
            github_client.add_sub_issue(repo, target.number, issue.id)
        except GitHubClientError as exc:
            print(f"サブ Issue 紐付けに失敗しました（続行）: {exc}", file=sys.stderr)
        created.append(issue)
        print(f"サブ Issue を作成しました: {issue.url}")

    if (
        command.comment_id is not None
        and target.repository_full_name is not None
    ):
        links = "\n".join(f"- {issue.url}" for issue in created)
        summary = f"サブ Issue を {len(created)} 件作成しました:\n{links}"
        try:
            github_client.create_issue_comment(
                target.repository_full_name,
                target.number,
                summary,
            )
        except GitHubClientError as exc:
            print(f"サマリコメント投稿に失敗しました: {exc}", file=sys.stderr)


def _post_response_comment(
    ready_execution: ReadyExecution,
    execution_result: ExecutionResult,
    github_client: GitHubClient | None,
) -> None:
    """reply / review / confirm / requirements / arch / detail の応答テキストをコメント投稿する。"""
    command = ready_execution.command
    response_text = execution_result.response_text
    if response_text is None:
        return

    target = command.target
    if command.dry_run or github_client is None or not _is_github_target(target):
        print(response_text)
        return

    assert target is not None
    assert target.repository_full_name is not None
    assert target.number is not None
    try:
        github_client.create_issue_comment(
            target.repository_full_name, target.number, response_text
        )
    except GitHubClientError as exc:
        print(f"コメント投稿に失敗しました: {exc}", file=sys.stderr)


def _add_reaction_safe(
    github_client: GitHubClient,
    repository_full_name: str,
    comment_id: int,
    content: GitHubReactionContent,
) -> int | None:
    """reaction を付与し、reaction_id を返す。失敗しても None を返す。"""
    try:
        reaction = github_client.add_issue_comment_reaction(
            repository_full_name, comment_id, content
        )
        return reaction.id
    except GitHubClientError as exc:
        print(f"reaction 付与に失敗しました: {exc}", file=sys.stderr)
        return None


def _finalize_reactions(
    github_client: GitHubClient,
    repository_full_name: str,
    comment_id: int,
    eyes_reaction_id: int | None,
    status: str,
) -> None:
    """eyes を除去し、失敗時は confused を付与する。"""
    if eyes_reaction_id is not None:
        try:
            github_client.remove_issue_comment_reaction(
                repository_full_name, comment_id, eyes_reaction_id
            )
        except GitHubClientError as exc:
            print(f"eyes reaction 除去に失敗しました: {exc}", file=sys.stderr)

    if status != "success":
        _add_reaction_safe(github_client, repository_full_name, comment_id, "confused")
