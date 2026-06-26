"""vv-ai の導入作業を案内するツール。"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import unquote, urlparse

from vv_ai.config import VVAIConfigError, load_vv_ai_config_file

_WORKFLOW_RELATIVE_PATH = Path(".github/workflows/vv-ai.yml")
_REQUIRED_SECRET_NAMES = (
    "VV_AI_AGE_PUBLIC_KEY",
    "VV_AI_AGE_SECRET_KEY",
    "VV_AI_APP_ID",
    "VV_AI_APP_PRIVATE_KEY",
)
_CONTEXT7_SECRET_NAME = "VV_CONTEXT7_API_KEY"


def main() -> None:
    """vv-ai の導入作業を案内する。"""
    _parse_args()
    try:
        _run_setup()
    except SetupVVAIError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


class SetupVVAIError(RuntimeError):
    """vv-ai 導入作業の失敗を表すエラー。"""


@dataclass(frozen=True)
class DistributionSource:
    """vv-ai 配布元の解決結果。"""

    uvx_from: str
    source_root: Path | None
    commit_id: str | None


@dataclass(frozen=True)
class _RepositoryInfo:
    """対象 GitHub リポジトリの情報。"""

    name_with_owner: str
    default_branch: str


def _parse_args() -> argparse.Namespace:
    """コマンドライン引数を解釈する。"""
    parser = argparse.ArgumentParser(description="vv-ai の導入作業を案内する")
    return parser.parse_args()


def _run_setup() -> None:
    """vv-ai の導入作業を実行する。"""
    repository_info = _resolve_repository_info()
    distribution_source = _resolve_distribution_source()
    workflow_text = _read_source_file(distribution_source)
    repo_root = Path.cwd()

    print(f"対象リポジトリ: {repository_info.name_with_owner}")
    _write_workflow_file(repo_root, workflow_text)
    _write_config_file_if_needed(repo_root, repository_info.default_branch)

    secret_names = _list_secret_names()
    _setup_required_secrets(secret_names)
    _setup_optional_context7_secret(secret_names)
    _run_optional_uvx_tools(distribution_source)


def _resolve_distribution_source() -> DistributionSource:
    """インストール済み vv-ai の direct_url.json から配布元を解決する。"""
    try:
        vv_ai_distribution = distribution("vv-ai")
    except PackageNotFoundError as e:
        raise SetupVVAIError("インストール済み vv-ai の配布情報が見つかりません") from e

    direct_url_text = vv_ai_distribution.read_text("direct_url.json")
    if direct_url_text is None:
        raise SetupVVAIError("vv-ai の direct_url.json が見つかりません")

    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError as e:
        raise SetupVVAIError("vv-ai の direct_url.json を JSON として解釈できません") from e

    url = direct_url.get("url")
    if not isinstance(url, str) or url == "":
        raise SetupVVAIError("vv-ai の direct_url.json に url がありません")

    vcs_info = direct_url.get("vcs_info")
    if vcs_info is None:
        source_root = _path_from_file_url(url)
        return DistributionSource(
            uvx_from=str(source_root),
            source_root=source_root,
            commit_id=None,
        )

    if not isinstance(vcs_info, dict):
        raise SetupVVAIError("vv-ai の direct_url.json の vcs_info が不正です")
    if vcs_info.get("vcs") != "git":
        raise SetupVVAIError("vv-ai の配布元は Git またはローカルディレクトリにしてください")

    commit_id = vcs_info.get("commit_id")
    if not isinstance(commit_id, str) or commit_id == "":
        raise SetupVVAIError("vv-ai の direct_url.json に commit_id がありません")

    git_url = url if url.startswith("git+") else f"git+{url}"
    _resolve_github_repository_name(git_url)
    return DistributionSource(
        uvx_from=f"{git_url}@{commit_id}",
        source_root=None,
        commit_id=commit_id,
    )


def _resolve_repository_info() -> _RepositoryInfo:
    """gh から対象リポジトリ名とデフォルトブランチ名を取得する。"""
    output = _run_gh_text(
        [
            "repo",
            "view",
            "--json",
            "nameWithOwner,defaultBranchRef",
        ]
    )
    try:
        raw_info = json.loads(output)
    except json.JSONDecodeError as e:
        raise SetupVVAIError("gh repo view の出力を JSON として解釈できません") from e

    name_with_owner = raw_info.get("nameWithOwner")
    default_branch_ref = raw_info.get("defaultBranchRef")
    if not isinstance(name_with_owner, str) or name_with_owner == "":
        raise SetupVVAIError("対象リポジトリ名を解決できませんでした")
    if not isinstance(default_branch_ref, dict):
        raise SetupVVAIError("対象リポジトリのデフォルトブランチ情報が不正です")

    default_branch = default_branch_ref.get("name")
    if not isinstance(default_branch, str) or default_branch == "":
        raise SetupVVAIError("対象リポジトリのデフォルトブランチ名を解決できませんでした")

    return _RepositoryInfo(
        name_with_owner=name_with_owner,
        default_branch=default_branch,
    )


def _read_source_file(distribution_source: DistributionSource) -> str:
    """配布元から vv-ai ワークフローファイルを読む。"""
    if distribution_source.source_root is not None:
        workflow_path = distribution_source.source_root / _WORKFLOW_RELATIVE_PATH
        try:
            return workflow_path.read_text(encoding="utf-8")
        except OSError as e:
            raise SetupVVAIError(f"`{workflow_path}` の読み込みに失敗しました") from e

    if distribution_source.commit_id is None:
        raise SetupVVAIError("GitHub 配布元の commit_id がありません")

    repository_name = _resolve_github_repository_name(distribution_source.uvx_from)
    return _run_gh_text(
        [
            "api",
            f"repos/{repository_name}/contents/.github/workflows/vv-ai.yml?ref={distribution_source.commit_id}",
            "-H",
            "Accept: application/vnd.github.raw",
        ]
    )


def _write_workflow_file(repo_root: Path, workflow_text: str) -> None:
    """対象リポジトリへ vv-ai ワークフローファイルを書き込む。"""
    workflow_path = repo_root / _WORKFLOW_RELATIVE_PATH
    try:
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(workflow_text, encoding="utf-8")
    except OSError as e:
        raise SetupVVAIError(f"`{workflow_path}` の書き込みに失敗しました") from e
    print(f"ワークフローを配置しました: {workflow_path}")


def _write_config_file_if_needed(repo_root: Path, default_branch: str) -> None:
    """vv-ai.yml が無い場合は作成し、ある場合は検証する。"""
    config_path = repo_root / "vv-ai.yml"
    if config_path.exists():
        try:
            load_vv_ai_config_file(config_path)
        except VVAIConfigError as e:
            raise SetupVVAIError(f"`{config_path}` の検証に失敗しました: {e}") from e
        print(f"既存設定を検証しました: {config_path}")
        return

    allowed_users = _ask_allowed_users()
    config_text = _build_new_config_text(allowed_users, default_branch)
    try:
        config_path.write_text(config_text, encoding="utf-8")
    except OSError as e:
        raise SetupVVAIError(f"`{config_path}` の書き込みに失敗しました") from e
    print(f"設定ファイルを作成しました: {config_path}")


def _build_new_config_text(allowed_users: list[str], default_branch: str) -> str:
    """新規 vv-ai.yml の本文を組み立てる。"""
    if len(allowed_users) == 0:
        raise SetupVVAIError("allowed_users には 1 件以上のユーザー名が必要です")
    if default_branch == "":
        raise SetupVVAIError("デフォルトブランチ名が空です")

    allowed_user_lines = "\n".join(
        f"  - {json.dumps(allowed_user, ensure_ascii=False)}"
        for allowed_user in allowed_users
    )
    return (
        "allowed_users:\n"
        f"{allowed_user_lines}\n"
        "provider_priority: [codex, claude]\n"
        f"pull_request_target_branch: {json.dumps(default_branch, ensure_ascii=False)}\n"
        "merge_args: []\n"
    )


def _list_secret_names() -> set[str]:
    """対象リポジトリに登録済みの GitHub Secret 名を取得する。"""
    output = _run_gh_text(
        [
            "secret",
            "list",
            "--json",
            "name",
            "--jq",
            ".[].name",
        ]
    )
    return set(output.splitlines())


def _setup_required_secrets(secret_names: set[str]) -> None:
    """必須 GitHub Secret を登録または更新する。"""
    for secret_name in _REQUIRED_SECRET_NAMES:
        if secret_name in secret_names:
            if not _ask_yes_no(f"{secret_name} は登録済みです。更新しますか", False):
                continue
        if secret_name == "VV_AI_APP_PRIVATE_KEY":
            secret_value = _ask_github_app_private_key()
        else:
            secret_value = _ask_single_line_secret(secret_name)
        _set_secret(secret_name, secret_value)


def _setup_optional_context7_secret(secret_names: set[str]) -> None:
    """任意の context7 GitHub Secret を登録または更新する。"""
    if _CONTEXT7_SECRET_NAME in secret_names:
        if not _ask_yes_no(f"{_CONTEXT7_SECRET_NAME} は登録済みです。更新しますか", False):
            return
    elif not _ask_yes_no(f"{_CONTEXT7_SECRET_NAME} を登録しますか", False):
        return

    _set_secret(_CONTEXT7_SECRET_NAME, _ask_single_line_secret(_CONTEXT7_SECRET_NAME))


def _set_secret(secret_name: str, secret_value: str) -> None:
    """gh secret set で GitHub Secret を設定する。"""
    if secret_name == "":
        raise SetupVVAIError("GitHub Secret 名が空です")
    if secret_value == "":
        raise SetupVVAIError(f"{secret_name} の値が空です")
    _validate_secret_value(secret_name, secret_value)

    cmd = ["gh", "secret", "set", secret_name]
    try:
        result = subprocess.run(
            cmd,
            input=secret_value,
            text=True,
            capture_output=True,
        )
    except OSError as e:
        raise SetupVVAIError(f"gh secret set の実行に失敗しました: {e}") from e

    if result.returncode != 0:
        raise SetupVVAIError(
            f"gh secret set が失敗しました exit {result.returncode}:\n{result.stderr}"
        )
    print("GitHub Secret を設定しました")


def _validate_secret_value(secret_name: str, secret_value: str) -> None:
    """GitHub Secret の値の形式を検証する。"""
    if secret_name == "VV_AI_AGE_PUBLIC_KEY":
        if not secret_value.startswith("age1"):
            raise SetupVVAIError("VV_AI_AGE_PUBLIC_KEY は age 公開鍵の形式で入力してください")
        return
    if secret_name == "VV_AI_AGE_SECRET_KEY":
        if not secret_value.startswith("AGE-SECRET-KEY-"):
            raise SetupVVAIError("VV_AI_AGE_SECRET_KEY は age 秘密鍵の形式で入力してください")
        return
    if secret_name == "VV_AI_APP_ID":
        if not _is_positive_decimal_text(secret_value):
            raise SetupVVAIError("VV_AI_APP_ID は正の整数で入力してください")
        return
    if secret_name == "VV_AI_APP_PRIVATE_KEY":
        _validate_github_app_private_key(secret_value)


def _is_positive_decimal_text(value: str) -> bool:
    """文字列が正の整数表記なら真を返す。"""
    if value == "":
        return False
    for char in value:
        if char not in "0123456789":
            return False
    return int(value) > 0


def _validate_github_app_private_key(secret_value: str) -> None:
    """GitHub App 秘密鍵の PEM 形式を検証する。"""
    stripped_value = secret_value.strip()
    if stripped_value.startswith("-----BEGIN RSA PRIVATE KEY-----") and (
        stripped_value.endswith("-----END RSA PRIVATE KEY-----")
    ):
        return
    if stripped_value.startswith("-----BEGIN PRIVATE KEY-----") and (
        stripped_value.endswith("-----END PRIVATE KEY-----")
    ):
        return
    raise SetupVVAIError("VV_AI_APP_PRIVATE_KEY は PEM 形式の秘密鍵で入力してください")


def _ask_yes_no(question: str, default_answer: bool) -> bool:
    """yes または no の入力を求める。"""
    prompt_suffix = "[Y/n]" if default_answer else "[y/N]"
    while True:
        try:
            answer = input(f"{question} {prompt_suffix}: ").strip().lower()
        except EOFError as e:
            raise SetupVVAIError("yes または no の入力が必要です") from e
        if answer == "":
            return default_answer
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("y または n を入力してください")


def _ask_allowed_users() -> list[str]:
    """allowed_users に設定する GitHub ユーザー名を入力させる。"""
    try:
        raw_value = input("許可する GitHub ユーザー名をカンマ区切りで入力してください: ")
    except EOFError as e:
        raise SetupVVAIError("allowed_users の入力が必要です") from e

    allowed_users = [user.strip() for user in raw_value.split(",")]
    if len(allowed_users) == 0 or any(user == "" for user in allowed_users):
        raise SetupVVAIError("allowed_users には空でない GitHub ユーザー名が必要です")
    return allowed_users


def _ask_single_line_secret(secret_name: str) -> str:
    """1 行の GitHub Secret 値を入力させる。"""
    try:
        secret_value = getpass.getpass(f"{secret_name} を入力してください: ")
    except EOFError as e:
        raise SetupVVAIError(f"{secret_name} の入力が必要です") from e
    if secret_value == "":
        raise SetupVVAIError(f"{secret_name} の値が空です")
    return secret_value


def _ask_github_app_private_key() -> str:
    """GitHub App の秘密鍵を複数行で入力させる。"""
    print("VV_AI_APP_PRIVATE_KEY を入力してください。空行で終了します。")
    lines: list[str] = []
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        if line in {"\n", "\r\n"}:
            break
        lines.append(line)
    if len(lines) == 0:
        raise SetupVVAIError("VV_AI_APP_PRIVATE_KEY の入力が必要です")
    return "".join(lines)


def _run_uvx_tool(distribution_source: DistributionSource, tool_name: str) -> None:
    """uvx --from で補助コマンドを別プロセス実行する。"""
    if tool_name == "":
        raise SetupVVAIError("uvx で実行するコマンド名が空です")

    cmd = ["uvx", "--from", distribution_source.uvx_from, tool_name]
    try:
        result = subprocess.run(cmd)
    except OSError as e:
        raise SetupVVAIError(f"uvx の実行に失敗しました: {e}") from e
    if result.returncode != 0:
        raise SetupVVAIError(f"{tool_name} が失敗しました exit {result.returncode}")


def _run_optional_uvx_tools(distribution_source: DistributionSource) -> None:
    """対話結果に応じて補助コマンドを別プロセス実行する。"""
    if _ask_yes_no("Codex 認証 Secret を設定しますか", False):
        _run_uvx_tool(distribution_source, "set-codex-auth-secret")
    if _ask_yes_no("Claude 設定 Secret を設定しますか", False):
        _run_uvx_tool(distribution_source, "set-claude-settings-secret")
    if _ask_yes_no("vv-ai ラベルを同期しますか", True):
        _run_uvx_tool(distribution_source, "create-vv-ai-labels")


def _run_gh_text(args: Sequence[str]) -> str:
    """gh コマンドを実行し、標準出力を返す。"""
    cmd = ["gh", *args]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
        )
    except OSError as e:
        raise SetupVVAIError(f"gh コマンドの実行に失敗しました: {e}") from e
    if result.returncode != 0:
        raise SetupVVAIError(
            f"gh コマンドが失敗しました exit {result.returncode}:\n{result.stderr}"
        )
    return result.stdout


def _path_from_file_url(url: str) -> Path:
    """file URL からローカルパスを作る。"""
    parsed_url = urlparse(url)
    if parsed_url.scheme != "file":
        raise SetupVVAIError("vv-ai の配布元は GitHub またはローカルディレクトリにしてください")
    if parsed_url.netloc not in {"", "localhost"}:
        raise SetupVVAIError("ローカル配布元の file URL が不正です")
    path_text = unquote(parsed_url.path)
    if path_text == "":
        raise SetupVVAIError("ローカル配布元のパスが空です")
    return Path(path_text).resolve()


def _resolve_github_repository_name(git_url: str) -> str:
    """GitHub URL から owner/repo を取り出す。"""
    raw_url = git_url.removeprefix("git+")
    if raw_url.startswith("git@github.com:"):
        raw_path = raw_url.removeprefix("git@github.com:")
        path_without_revision = raw_path.rsplit("@", maxsplit=1)[0]
        repository_name = path_without_revision.removesuffix(".git")
        if repository_name.count("/") != 1:
            raise SetupVVAIError("GitHub 配布元のリポジトリ名が不正です")
        return repository_name

    parsed_url = urlparse(raw_url)
    if parsed_url.scheme not in {"https", "ssh"} or parsed_url.hostname != "github.com":
        raise SetupVVAIError("GitHub 配布元は github.com の URL にしてください")

    path_without_revision = parsed_url.path.rsplit("@", maxsplit=1)[0]
    path_parts = [part for part in path_without_revision.split("/") if part != ""]
    if len(path_parts) != 2:
        raise SetupVVAIError("GitHub 配布元のリポジトリ名を解決できません")

    repository_name = "/".join(path_parts)
    if repository_name.endswith(".git"):
        repository_name = repository_name.removesuffix(".git")
    if repository_name.count("/") != 1:
        raise SetupVVAIError("GitHub 配布元のリポジトリ名が不正です")
    return repository_name


if __name__ == "__main__":
    main()
