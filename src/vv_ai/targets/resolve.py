"""GitHub / local target を共通表現へ解決する。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from vv_ai.inputs.models import TargetType
from vv_ai.inputs.resolve import ResolvedCommand, ResolvedControlLabel, ResolvedTarget


class TargetResolutionError(Exception):
    """target 解決に失敗したことを表す例外。"""


def resolve_target(repo_root: Path, command: ResolvedCommand) -> ResolvedCommand:
    """target を GitHub / local の共通表現へ変換する。"""
    target = _build_target(repo_root, command)
    if target is None:
        return command
    if command.command == "sync" and target.kind == "issue":
        raise TargetResolutionError("`sync` コマンドは PR 専用です")
    return command.model_copy(update={"target": target})


def resolve_control_label_target(
    control_label: ResolvedControlLabel,
) -> ResolvedControlLabel:
    """制御ラベル入力の target を GitHub / local の共通表現へ変換する。"""
    target = _build_target_from_fields(
        repository_full_name=control_label.repository_full_name,
        kind=control_label.target_type,
        number=control_label.target_number,
    )
    return control_label.model_copy(update={"target": target})


def _build_target(
    repo_root: Path,
    command: ResolvedCommand,
) -> ResolvedTarget | None:
    """利用可能な入力から target を組み立てる。"""
    if command.target_url is not None:
        return _build_target_from_target_url(repo_root, command.target_url)

    if (
        command.repository_full_name is not None
        and command.target_type is not None
        and command.target_number is not None
    ):
        return _build_target_from_fields(
            repository_full_name=command.repository_full_name,
            kind=command.target_type,
            number=command.target_number,
        )

    return None


def _build_target_from_target_url(repo_root: Path, target_url: str) -> ResolvedTarget:
    """`target_url` から GitHub または local target を解決する。"""
    if target_url.startswith("https://github.com/"):
        return _build_github_target_from_url(target_url)
    return _build_local_target_from_path(repo_root, target_url)


def _build_github_target_from_url(target_url: str) -> ResolvedTarget:
    """GitHub Issue / PR URL を解決する。"""
    split_result = urlsplit(target_url)
    path_parts = [part for part in split_result.path.split("/") if part]
    if len(path_parts) != 4:
        raise TargetResolutionError(
            "GitHub target URL は "
            "`https://github.com/<owner>/<repo>/(issues|pull)/<number>` "
            "形式で指定してください"
        )

    owner, repo, raw_kind, raw_number = path_parts
    if raw_kind == "issues":
        kind = "issue"
    elif raw_kind == "pull":
        kind = "pr"
    else:
        raise TargetResolutionError(
            "GitHub target URL は Issue または PR の URL を指定してください"
        )

    try:
        number = int(raw_number)
    except ValueError as exc:
        raise TargetResolutionError("GitHub target URL の番号が不正です") from exc
    if number <= 0:
        raise TargetResolutionError("GitHub target URL の番号は 1 以上である必要があります")

    repository_full_name = f"{owner}/{repo}"
    return _build_target_from_fields(
        repository_full_name=repository_full_name,
        kind=kind,
        number=number,
    )


def _build_local_target_from_path(repo_root: Path, raw_path: str) -> ResolvedTarget:
    """ローカル path を共通 target 表現へ変換する。"""
    resolved_path = _resolve_local_input_path(repo_root, raw_path)
    if not resolved_path.exists():
        raise TargetResolutionError(
            f"ローカル target の path が見つかりません: `{raw_path}`"
        )

    local_root = (repo_root / ".vv-ai").resolve()
    if not _is_same_or_child_path(resolved_path, local_root):
        raise TargetResolutionError(
            "ローカル target は repo root 配下の `.vv-ai/issues/<id>` "
            "または `.vv-ai/prs/<id>` を指定してください"
        )

    issue_target = _try_build_local_target(
        resolved_path=resolved_path,
        targets_root=local_root / "issues",
        kind="issue",
        document_name="issue.md",
    )
    if issue_target is not None:
        return issue_target

    pr_target = _try_build_local_target(
        resolved_path=resolved_path,
        targets_root=local_root / "prs",
        kind="pr",
        document_name="pr.md",
    )
    if pr_target is not None:
        return pr_target

    raise TargetResolutionError(
        "ローカル target は `.vv-ai/issues/<id>`、`.vv-ai/issues/<id>/issue.md`、"
        "`.vv-ai/prs/<id>`、`.vv-ai/prs/<id>/pr.md` のいずれかを指定してください"
    )


def _resolve_local_input_path(repo_root: Path, raw_path: str) -> Path:
    """repo root 基準でローカル path を絶対 path に直す。"""
    input_path = Path(raw_path).expanduser()
    if input_path.is_absolute():
        return input_path.resolve()
    return (repo_root / input_path).resolve()


def _try_build_local_target(
    resolved_path: Path,
    targets_root: Path,
    kind: TargetType,
    document_name: str,
) -> ResolvedTarget | None:
    """許可された local target path なら target を返す。"""
    try:
        relative_path = resolved_path.relative_to(targets_root)
    except ValueError:
        return None

    path_parts = relative_path.parts
    if len(path_parts) == 1 and resolved_path.is_dir():
        local_id = path_parts[0]
        target_dir = resolved_path
    elif len(path_parts) == 2 and path_parts[1] == document_name and resolved_path.is_file():
        local_id = path_parts[0]
        target_dir = resolved_path.parent
    else:
        return None

    if not local_id:
        return None

    return ResolvedTarget(
        backend="local",
        kind=kind,
        canonical_id=f"{kind}:{local_id}",
        local_id=local_id,
        path=str(target_dir),
    )


def _is_same_or_child_path(candidate: Path, parent: Path) -> bool:
    """candidate が parent 自身または配下かどうかを返す。"""
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _build_target_from_fields(
    repository_full_name: str,
    kind: TargetType,
    number: int,
) -> ResolvedTarget:
    """GitHub target の基本情報から共通表現を組み立てる。"""
    path_kind = "issues" if kind == "issue" else "pull"
    canonical_id = f"{repository_full_name}#{number}"
    return ResolvedTarget(
        backend="github",
        kind=kind,
        canonical_id=canonical_id,
        repository_full_name=repository_full_name,
        number=number,
        url=f"https://github.com/{repository_full_name}/{path_kind}/{number}",
    )
