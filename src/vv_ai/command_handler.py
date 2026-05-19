"""コマンド実行処理の実装モジュールを参照する。"""

from __future__ import annotations

import sys

from vv_ai.commands import runner as _runner

sys.modules[__name__] = _runner
