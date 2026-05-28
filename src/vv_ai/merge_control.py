"""`vv-ai:merge` 制御ラベルの実行処理。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from vv_ai.backends.github.client import GitHubClient
from vv_ai.config import VVAIConfig
from vv_ai.inputs.resolve import ResolvedControlLabel

MergeControlStatus = Literal["merge_requested", "auto_disabled"]


class MergeControlError(Exception):
    """merge 制御に失敗したことを表す例外。"""


class MergeControlResult(BaseModel):
    """merge 制御の実行結果。"""

    model_config = ConfigDict(extra="forbid")

    status: MergeControlStatus
    repository_full_name: str
    pull_request_number: int


def run_merge_control(
    github_client: GitHubClient,
    control: ResolvedControlLabel,
    config: VVAIConfig,
) -> MergeControlResult:
    """`vv-ai:merge` 制御ラベルを実行する。"""
    if control.control_label_name != "vv-ai:merge":
        raise MergeControlError("`vv-ai:merge` 以外の制御ラベルは扱えません")
    if control.target_type != "pr":
        raise MergeControlError("`vv-ai:merge` は PR 専用です")

    if control.label_action == "labeled":
        github_client.merge_pull_request(
            control.repository_full_name,
            control.target_number,
            config.merge_args,
        )
        return MergeControlResult(
            status="merge_requested",
            repository_full_name=control.repository_full_name,
            pull_request_number=control.target_number,
        )

    if control.label_action == "unlabeled":
        github_client.disable_pull_request_auto_merge(
            control.repository_full_name,
            control.target_number,
        )
        return MergeControlResult(
            status="auto_disabled",
            repository_full_name=control.repository_full_name,
            pull_request_number=control.target_number,
        )

    raise MergeControlError(f"未対応の label action です: {control.label_action}")
