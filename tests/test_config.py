"""設定モデルの単体テスト。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vv_ai.config import VVAIConfig


class TestVVAIConfig:
    def test_allowed_users_trimmed(self) -> None:
        config = VVAIConfig(allowed_users=["  Hiroshiba  "])

        assert config.allowed_users == ["Hiroshiba"]

    def test_allowed_users_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            VVAIConfig(allowed_users=[" "])

    def test_internal_bot_ids_default(self) -> None:
        config = VVAIConfig(allowed_users=["Hiroshiba"])

        assert config.internal_bot_ids == [274163862]

    def test_internal_bot_ids_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            VVAIConfig(allowed_users=["Hiroshiba"], internal_bot_ids=[0])

    def test_internal_bot_ids_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            VVAIConfig(allowed_users=["Hiroshiba"], internal_bot_ids=[-1])

    def test_pull_request_target_branch_trimmed(self) -> None:
        config = VVAIConfig(
            allowed_users=["Hiroshiba"],
            pull_request_target_branch="  main  ",
        )

        assert config.pull_request_target_branch == "main"

    def test_pull_request_target_branch_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            VVAIConfig(
                allowed_users=["Hiroshiba"],
                pull_request_target_branch=" ",
            )

    def test_merge_args_default(self) -> None:
        config = VVAIConfig(allowed_users=["Hiroshiba"])

        assert config.merge_args == []

    def test_merge_args_allows_auto_and_squash(self) -> None:
        config = VVAIConfig(
            allowed_users=["Hiroshiba"],
            merge_args=["--auto", "--squash"],
        )

        assert config.merge_args == ["--auto", "--squash"]

    def test_merge_args_allows_subject_value(self) -> None:
        config = VVAIConfig(
            allowed_users=["Hiroshiba"],
            merge_args=["--subject", "件名"],
        )

        assert config.merge_args == ["--subject", "件名"]

    def test_merge_args_allows_subject_equal_value(self) -> None:
        config = VVAIConfig(
            allowed_users=["Hiroshiba"],
            merge_args=["--subject=件名"],
        )

        assert config.merge_args == ["--subject=件名"]

    @pytest.mark.parametrize(
        "merge_args",
        [
            ["--repo", "org/repo"],
            ["-R", "org/repo"],
            ["--disable-auto"],
            ["--body-file", "file"],
            ["-F", "file"],
            ["1"],
            ["--subject"],
            ["--unknown"],
        ],
    )
    def test_merge_args_rejects_invalid_args(self, merge_args: list[str]) -> None:
        with pytest.raises(ValidationError):
            VVAIConfig(allowed_users=["Hiroshiba"], merge_args=merge_args)
