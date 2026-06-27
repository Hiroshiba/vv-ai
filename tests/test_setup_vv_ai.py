"""vv-ai 導入ツールの単体テスト。"""

from __future__ import annotations

import getpass
import json
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

from tools import setup_vv_ai
from tools.setup_vv_ai import (
    DistributionSource,
    SetupVVAIError,
    _ask_github_app_private_key,
    _ask_yes_no,
    _build_new_config_text,
    _list_secret_names,
    _read_source_file,
    _resolve_distribution_source,
    _resolve_github_repository_name,
    _resolve_repository_info,
    _run_setup,
    _run_optional_uvx_tools,
    _run_uvx_tool,
    _set_secret,
    _setup_optional_context7_secret,
    _setup_required_secrets,
    _write_config_file_if_needed,
)


class _FakeDistribution:
    """direct_url.json だけを返す配布情報。"""

    def __init__(self, direct_url_text: str) -> None:
        self.direct_url_text = direct_url_text

    def read_text(self, filename: str) -> str:
        """指定ファイルの内容を返す。"""
        assert filename == "direct_url.json"
        return self.direct_url_text


def test_resolve_repository_info_reads_gh_repo_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_repository_info は gh repo view から対象情報を読む。"""
    calls: list[list[str]] = []

    def fake_run_gh_text(args: list[str]) -> str:
        calls.append(args)
        return json.dumps(
            {
                "nameWithOwner": "org/repo",
                "defaultBranchRef": {"name": "main"},
            }
        )

    monkeypatch.setattr(setup_vv_ai, "_run_gh_text", fake_run_gh_text)

    repository_info = _resolve_repository_info()

    assert repository_info.name_with_owner == "org/repo"
    assert repository_info.default_branch == "main"
    assert calls == [
        [
            "repo",
            "view",
            "--json",
            "nameWithOwner,defaultBranchRef",
        ]
    ]


def test_resolve_distribution_source_reads_local_direct_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_resolve_distribution_source はローカル配布元を解決する。"""
    source_root = tmp_path / "source"
    source_root.mkdir()
    direct_url_text = json.dumps({"url": source_root.as_uri(), "dir_info": {}})
    fake_distribution = _FakeDistribution(direct_url_text)

    monkeypatch.setattr(setup_vv_ai, "distribution", lambda name: fake_distribution)

    distribution_source = _resolve_distribution_source()

    assert distribution_source == DistributionSource(
        uvx_from=str(source_root),
        source_root=source_root,
        commit_id=None,
    )


def test_resolve_distribution_source_reads_github_direct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_distribution_source は GitHub 配布元を commit 固定で解決する。"""
    direct_url_text = json.dumps(
        {
            "url": "https://github.com/Hiroshiba/vv-ai.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123"},
        }
    )
    fake_distribution = _FakeDistribution(direct_url_text)

    monkeypatch.setattr(setup_vv_ai, "distribution", lambda name: fake_distribution)

    distribution_source = _resolve_distribution_source()

    assert distribution_source == DistributionSource(
        uvx_from="git+https://github.com/Hiroshiba/vv-ai.git@abc123",
        source_root=None,
        commit_id="abc123",
    )


def test_read_source_file_reads_local_workflow(tmp_path: Path) -> None:
    """_read_source_file はローカル配布元のワークフローを読む。"""
    source_root = tmp_path / "source"
    workflow_path = source_root / ".github/workflows/vv-ai.yml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text("name: vv-ai\n", encoding="utf-8")

    distribution_source = DistributionSource(
        uvx_from=str(source_root),
        source_root=source_root,
        commit_id=None,
    )

    assert _read_source_file(distribution_source) == "name: vv-ai\n"


def test_read_source_file_reads_github_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_read_source_file は GitHub 配布元のワークフローを gh api で読む。"""
    calls: list[list[str]] = []

    def fake_run_gh_text(args: list[str]) -> str:
        calls.append(args)
        return "name: vv-ai\n"

    monkeypatch.setattr(setup_vv_ai, "_run_gh_text", fake_run_gh_text)

    distribution_source = DistributionSource(
        uvx_from="git+https://github.com/Hiroshiba/vv-ai.git@abc123",
        source_root=None,
        commit_id="abc123",
    )

    assert _read_source_file(distribution_source) == "name: vv-ai\n"
    assert calls == [
        [
            "api",
            "repos/Hiroshiba/vv-ai/contents/.github/workflows/vv-ai.yml?ref=abc123",
            "-H",
            "Accept: application/vnd.github.raw",
        ]
    ]


def test_build_new_config_text_sets_required_values() -> None:
    """_build_new_config_text は新規設定の既定値を設定する。"""
    assert _build_new_config_text(["alice", "bob"], "main") == (
        "allowed_users:\n"
        '  - "alice"\n'
        '  - "bob"\n'
        "provider_priority: [codex, claude]\n"
        'pull_request_target_branch: "main"\n'
        "merge_args: []\n"
    )


def test_write_config_file_if_needed_keeps_existing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_write_config_file_if_needed は既存設定を変更せず検証だけ行う。"""
    config_path = tmp_path / "vv-ai.yml"
    config_text = "allowed_users:\n  - alice\n"
    config_path.write_text(config_text, encoding="utf-8")

    def fail_ask_allowed_users() -> list[str]:
        raise AssertionError("allowed_users を聞いてはいけません")

    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_allowed_users",
        fail_ask_allowed_users,
    )

    _write_config_file_if_needed(tmp_path, "main")

    assert config_path.read_text(encoding="utf-8") == config_text


def test_write_config_file_if_needed_creates_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_write_config_file_if_needed は設定が無い場合だけ作成する。"""
    monkeypatch.setattr(setup_vv_ai, "_ask_allowed_users", lambda: ["alice"])

    _write_config_file_if_needed(tmp_path, "main")

    assert (tmp_path / "vv-ai.yml").read_text(encoding="utf-8") == (
        "allowed_users:\n"
        '  - "alice"\n'
        "provider_priority: [codex, claude]\n"
        'pull_request_target_branch: "main"\n'
        "merge_args: []\n"
    )


def test_list_secret_names_reads_secret_name_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_list_secret_names は GitHub Secret 名だけを取得する。"""
    calls: list[list[str]] = []

    def fake_run_gh_text(args: list[str]) -> str:
        calls.append(args)
        return "VV_AI_APP_ID\nVV_CONTEXT7_API_KEY\n"

    monkeypatch.setattr(setup_vv_ai, "_run_gh_text", fake_run_gh_text)

    assert _list_secret_names() == {"VV_AI_APP_ID", "VV_CONTEXT7_API_KEY"}
    assert calls == [
        [
            "secret",
            "list",
            "--json",
            "name",
            "--jq",
            ".[].name",
        ]
    ]


def test_setup_required_secrets_asks_missing_and_updates_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_setup_required_secrets は不足分と更新選択分だけ設定する。"""
    questions: list[str] = []
    set_values: dict[str, str] = {}

    def fake_ask_yes_no(question: str, default_answer: bool) -> bool:
        questions.append(question)
        return "PRIVATE_KEY" in question

    def fake_ask_single_line_secret(secret_name: str) -> str:
        return f"value-{secret_name}"

    def fake_set_secret(secret_name: str, secret_value: str) -> None:
        set_values[secret_name] = secret_value

    monkeypatch.setattr(setup_vv_ai, "_ask_yes_no", fake_ask_yes_no)
    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_single_line_secret",
        fake_ask_single_line_secret,
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_github_app_private_key",
        lambda: "private-key\n",
    )
    monkeypatch.setattr(setup_vv_ai, "_set_secret", fake_set_secret)

    _setup_required_secrets({"VV_AI_AGE_PUBLIC_KEY", "VV_AI_APP_PRIVATE_KEY"})

    assert questions == [
        "VV_AI_AGE_PUBLIC_KEY は登録済みです。更新しますか",
        "VV_AI_APP_PRIVATE_KEY は登録済みです。更新しますか",
    ]
    assert set_values == {
        "VV_AI_AGE_SECRET_KEY": "value-VV_AI_AGE_SECRET_KEY",
        "VV_AI_APP_ID": "value-VV_AI_APP_ID",
        "VV_AI_APP_PRIVATE_KEY": "private-key\n",
    }


def test_setup_optional_context7_secret_sets_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_setup_optional_context7_secret は選択された場合だけ設定する。"""
    set_values: dict[str, str] = {}

    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_yes_no",
        lambda question, default_answer: True,
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_single_line_secret",
        lambda secret_name: f"value-{secret_name}",
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_set_secret",
        lambda secret_name, secret_value: set_values.update(
            {secret_name: secret_value}
        ),
    )

    _setup_optional_context7_secret(set())

    assert set_values == {"VV_CONTEXT7_API_KEY": "value-VV_CONTEXT7_API_KEY"}


def test_set_secret_runs_gh_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_set_secret は gh secret set に値を標準入力で渡す。"""
    calls: list[tuple[list[str], str]] = []

    def fake_run(
        cmd: list[str],
        input: str,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, input))
        assert text is True
        assert capture_output is True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _set_secret("VV_AI_APP_ID", "12345")

    assert calls == [(["gh", "secret", "set", "VV_AI_APP_ID"], "12345")]


def test_set_secret_rejects_invalid_app_id() -> None:
    """_set_secret は不正な GitHub App ID を拒否する。"""
    with pytest.raises(SetupVVAIError, match="正の整数"):
        _set_secret("VV_AI_APP_ID", "abc")


def test_set_secret_rejects_invalid_age_public_key() -> None:
    """_set_secret は不正な age 公開鍵を拒否する。"""
    with pytest.raises(SetupVVAIError, match="age 公開鍵"):
        _set_secret("VV_AI_AGE_PUBLIC_KEY", "invalid")


def test_set_secret_rejects_invalid_age_secret_key() -> None:
    """_set_secret は不正な age 秘密鍵を拒否する。"""
    with pytest.raises(SetupVVAIError, match="age 秘密鍵"):
        _set_secret("VV_AI_AGE_SECRET_KEY", "invalid")


def test_set_secret_rejects_invalid_app_private_key() -> None:
    """_set_secret は不正な GitHub App 秘密鍵を拒否する。"""
    with pytest.raises(SetupVVAIError, match="PEM"):
        _set_secret("VV_AI_APP_PRIVATE_KEY", "invalid")


def test_ask_yes_no_returns_default_on_empty_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ask_yes_no は空入力なら既定値を返す。"""
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    assert _ask_yes_no("実行しますか", True) is True
    assert _ask_yes_no("実行しますか", False) is False


def test_ask_github_app_private_key_reads_hidden_multiline_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ask_github_app_private_key は getpass で複数行の秘密鍵を読む。"""
    answers = iter(
        [
            "-----BEGIN PRIVATE KEY-----",
            "private-key-body",
            "-----END PRIVATE KEY-----",
            "",
        ]
    )
    prompts: list[str] = []

    def fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("getpass.getpass", fake_getpass)

    assert _ask_github_app_private_key() == (
        "-----BEGIN PRIVATE KEY-----\n"
        "private-key-body\n"
        "-----END PRIVATE KEY-----\n"
    )
    assert prompts == [
        "VV_AI_APP_PRIVATE_KEY: ",
        "VV_AI_APP_PRIVATE_KEY: ",
        "VV_AI_APP_PRIVATE_KEY: ",
        "VV_AI_APP_PRIVATE_KEY: ",
    ]


def test_ask_github_app_private_key_rejects_getpass_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ask_github_app_private_key は非表示入力できない場合に失敗する。"""

    def fake_getpass(prompt: str) -> NoReturn:
        raise getpass.GetPassWarning("表示されます")

    monkeypatch.setattr(getpass, "getpass", fake_getpass)

    with pytest.raises(SetupVVAIError, match="非表示"):
        _ask_github_app_private_key()


def test_run_uvx_tool_runs_tool_from_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_uvx_tool は uvx --from で補助コマンドを実行する。"""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    distribution_source = DistributionSource(
        uvx_from="git+https://github.com/Hiroshiba/vv-ai.git@abc123",
        source_root=None,
        commit_id="abc123",
    )
    _run_uvx_tool(distribution_source, "create-vv-ai-labels")

    assert calls == [
        [
            "uvx",
            "--from",
            "git+https://github.com/Hiroshiba/vv-ai.git@abc123",
            "create-vv-ai-labels",
        ]
    ]


def test_run_optional_uvx_tools_skips_ai_settings_and_runs_labels_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_optional_uvx_tools は既定でラベル同期だけ実行する。"""
    uvx_tools: list[str] = []

    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_yes_no",
        lambda question, default_answer: default_answer,
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_run_uvx_tool",
        lambda source, tool_name: uvx_tools.append(tool_name),
    )

    distribution_source = DistributionSource(
        uvx_from="git+https://github.com/Hiroshiba/vv-ai.git@abc123",
        source_root=None,
        commit_id="abc123",
    )
    _run_optional_uvx_tools(distribution_source)

    assert uvx_tools == ["create-vv-ai-labels"]


def test_run_optional_uvx_tools_runs_selected_ai_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_optional_uvx_tools は選択された AI 設定コマンドを実行する。"""
    uvx_tools: list[str] = []

    def fake_ask_yes_no(question: str, default_answer: bool) -> bool:
        return question in {
            "Codex 認証 Secret を設定しますか",
            "Claude 設定 Secret を設定しますか",
        }

    monkeypatch.setattr(setup_vv_ai, "_ask_yes_no", fake_ask_yes_no)
    monkeypatch.setattr(
        setup_vv_ai,
        "_run_uvx_tool",
        lambda source, tool_name: uvx_tools.append(tool_name),
    )

    distribution_source = DistributionSource(
        uvx_from="git+https://github.com/Hiroshiba/vv-ai.git@abc123",
        source_root=None,
        commit_id="abc123",
    )
    _run_optional_uvx_tools(distribution_source)

    assert uvx_tools == [
        "set-codex-auth-secret",
        "set-claude-settings-secret",
    ]


def test_run_setup_creates_files_sets_secrets_and_runs_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_run_setup は導入処理全体を順に実行する。"""
    set_values: dict[str, str] = {}
    uvx_tools: list[str] = []
    distribution_source = DistributionSource(
        uvx_from=str(tmp_path / "source"),
        source_root=tmp_path / "source",
        commit_id=None,
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        setup_vv_ai,
        "_resolve_repository_info",
        lambda: setup_vv_ai._RepositoryInfo("org/repo", "main"),
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_resolve_distribution_source",
        lambda: distribution_source,
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_read_source_file",
        lambda source: "name: vv-ai\n",
    )
    monkeypatch.setattr(setup_vv_ai, "_ask_allowed_users", lambda: ["alice"])
    monkeypatch.setattr(setup_vv_ai, "_list_secret_names", lambda: set())
    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_single_line_secret",
        lambda secret_name: f"value-{secret_name}",
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_github_app_private_key",
        lambda: "private-key\n",
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_ask_yes_no",
        lambda question, default_answer: default_answer,
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_set_secret",
        lambda secret_name, secret_value: set_values.update(
            {secret_name: secret_value}
        ),
    )
    monkeypatch.setattr(
        setup_vv_ai,
        "_run_uvx_tool",
        lambda source, tool_name: uvx_tools.append(tool_name),
    )

    _run_setup()

    assert (tmp_path / ".github/workflows/vv-ai.yml").read_text(
        encoding="utf-8"
    ) == "name: vv-ai\n"
    assert (tmp_path / "vv-ai.yml").read_text(encoding="utf-8") == (
        "allowed_users:\n"
        '  - "alice"\n'
        "provider_priority: [codex, claude]\n"
        'pull_request_target_branch: "main"\n'
        "merge_args: []\n"
    )
    assert set_values == {
        "VV_AI_AGE_PUBLIC_KEY": "value-VV_AI_AGE_PUBLIC_KEY",
        "VV_AI_AGE_SECRET_KEY": "value-VV_AI_AGE_SECRET_KEY",
        "VV_AI_APP_ID": "value-VV_AI_APP_ID",
        "VV_AI_APP_PRIVATE_KEY": "private-key\n",
    }
    assert uvx_tools == ["create-vv-ai-labels"]


def test_resolve_github_repository_name_rejects_non_github() -> None:
    """_resolve_github_repository_name は GitHub 以外を拒否する。"""
    with pytest.raises(SetupVVAIError, match="github.com"):
        _resolve_github_repository_name("git+https://example.com/org/repo.git@abc123")
