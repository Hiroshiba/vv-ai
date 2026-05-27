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
