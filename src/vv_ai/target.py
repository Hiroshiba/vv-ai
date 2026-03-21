"""GitHub target を共通表現へ解決する。"""

from __future__ import annotations

from urllib.parse import urlsplit

from vv_ai.input import TargetType
from vv_ai.resolve import ResolvedCommand, ResolvedTarget


class TargetResolutionError(Exception):
    """target 解決に失敗したことを表す例外。"""


def resolve_github_target(command: ResolvedCommand) -> ResolvedCommand:
    """GitHub target を共通表現へ変換する。"""
    target = _build_github_target(command)
    if target is None:
        return command
    return command.model_copy(update={"target": target})


def _build_github_target(command: ResolvedCommand) -> ResolvedTarget | None:
    """GitHub と確定できる入力から target を組み立てる。"""
    if command.target_url is not None:
        return _build_target_from_url(command.target_url)

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


def _build_target_from_url(target_url: str) -> ResolvedTarget | None:
    """GitHub Issue / PR URL を解決する。"""
    if not target_url.startswith("https://github.com/"):
        raise TargetResolutionError(
            "この target_url はまだ解決できません。"
            " GitHub Issue / PR URL を指定してください"
        )

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
