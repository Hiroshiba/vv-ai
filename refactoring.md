# Nullable とデフォルト引数の整理方針

## 前提

- この文書は、`| None` と不要なデフォルト引数を減らすための詳細なリファクタリング方針をまとめたもの
- この段階ではコードを変更しない
- 互換性は考慮しない
- 実装時に既存の曖昧な構造を守る必要はない
- 欠損を救済するフォールバックは原則としてバグの温床とみなす
- 欠損は入力境界でだけ扱い、内部では型で前提条件を表現する

## 目的

- `None` と空文字とデフォルト値で欠損を表す設計をやめる
- `is None` `or None` `== ""` で成り立っている後段の分岐をなくす
- 内部モデルを読めば必須値が分かる状態にする
- 想定外の入力や中途半端な状態を即例外にする
- 型を合わせるためだけの救済ロジックをなくす

## ゴール

- 外部入力モデル以外で不要な `| None` が残っていない
- class のフィールド定義にある不要なデフォルト値が消えている
- 通常関数の引数にある不要なデフォルト値が消えている
- `== ""` `!= ""` `or {}` `or False` のような後段救済が消えている
- 各 nullable に対して、なぜ必要かを一行で説明できる
- 各デフォルト引数に対して、なぜ不可避かを一行で説明できる

## 基本方針

- 欠損を扱ってよい層を限定する
- command 差分、backend 差分、provider 差分は nullable ではなく型で表現する
- 空文字を未指定扱いする正規化は外部入力直後に閉じ込める
- 後段では空文字も `None` も救済しない
- 必須かどうかを boolean 補助フラグで持たず、型で表す
- 呼び出し側が決めるべき値を callee 側で補完しない
- 互換のための余分なフィールドは残さない

## 何を残してよいか

- `None` を残してよいのは次だけ
- 外部入力の生データ
- 判別 union の未選択側
- 永続化上、本当に省略可能と定義した構造
- デフォルト値を残してよいのは次だけ
- `argparse` のように外部 API の都合で必要なもの
- 外部ライブラリの制約で避けられないもの

## 何を削除対象にするか

- `field: T | None = None`
- `arg: T | None = None`
- `value or fallback`
- `value.strip() or None`
- `if value is None: return fallback`
- `if value == "": return fallback`
- `repo or repository_full_name`
- `usage or MetricsUsage()`
- `tools or {}`
- `steps or {}`
- `dry_run or False`
- `slug_hint or body or "comment"` のような多段フォールバック

## 現状の問題

- 入力層で許した欠損が、そのまま内部層へ流れ込んでいる
- 単一モデルに複数の command や backend の事情を押し込んでいる
- GitHub target と local target のような別概念が同じモデルに入っている
- session 関連で、状態差分を `None` 判定に依存している
- artifact と metrics の保存モデルが nullable の集合になっている
- 現在時刻や slug のような値を関数内デフォルトで補完している
- 空文字が未指定の代用品として使われている
- `has_target` のような補助フラグが、型で表せる情報を重複保持している

## 層ごとの整理方針

### 入力層

- CLI 引数と GitHub event payload は未指定や欠損を受け入れる
- ただし、空文字や空白だけの文字列はこの層で処理を終える
- 片側だけ指定された target 情報はここで落とすか、直後の正規化層で落とす
- この層の責務は、受理と最小限の字句変換までに限定する
- この層の nullable は後段へ広げない

### 正規化層

- 外部入力から内部実行モデルへ変換する責務を明確にする
- command ごとの必須条件をここで確定する
- ここを超えたら、内部モデルには不要な `None` を持ち込まない
- 空文字から未指定への変換や option の補完はここまでで終える
- `has_target` のような補助フラグは、型で置き換えられるなら削除する

### 実行層

- command 差分を union へ分解する
- backend 差分を union へ分解する
- provider 差分を union へ分解する
- mode 差分を union へ分解する
- 実行コードは `None` 判定ではなく型分岐で書ける形にする

### 永続化層

- 保存 JSON に不要な `null` を残さない
- backend ごとに形が違うならサブモデルを分ける
- provider ごとに形が違うならサブモデルを分ける
- 集計未実施と値 0 を混同しない
- 呼び出し時点で保存対象を完成させ、保存関数内で補完しない

## モジュール別の詳細方針

### `src/vv_ai/input.py`

- `CLIInput` は外部入力モデルなので nullable を許容してよい
- `RawInput` は長く生かさず、内部モデルへ変換するためだけの短命な中間表現にする
- `CommentInvocation` も command 差分を一つに押し込めない
- `_coerce_optional_str` `_coerce_optional_int` `_coerce_optional_bool` `_coerce_optional_literal` は、外部入力受理専用の関数として閉じ込める
- 空文字を `None` に変える処理はここで完結させる
- 文字列項目は `""` を有効値として扱わない
- option の片側指定を許さない
- ここで受理した nullable をそのまま `resolve.py` 以後へ流さない

### `src/vv_ai/resolve.py`

- `ResolvedCommand` を単一モデルで持たない
- command 別 union へ分解する
- 少なくとも以下の違いを型で表す
- instruction 必須か
- target 必須か
- repo 必須か
- session 設定を持ちうるか
- target 情報が URL 由来か番号指定由来か
- `has_target` は削除候補
- `instruction: str | None` は削除候補
- `target_url: str | None` `target_type: TargetType | None` `target_number: int | None` は同居させない
- `repo or repository_full_name` のようなフォールバックをやめる
- `issue` command で repo が必須なら必須型にする
- `reply` `issue` の instruction 必須条件は、例外判定でなく型で表現する

### `src/vv_ai/target.py`

- `ResolvedTarget` を単一モデルで持たない
- `GitHubTarget` と `LocalTarget` に分割する
- GitHub 側だけが持つ `repository_full_name` `number` `url` を local 側に持たせない
- local 側だけが持つ `local_id` `path` を GitHub 側に持たせない
- target がない状態を `ResolvedTarget | None` で持つのではなく、command 側の型で表す
- `None` を返す補助関数は、対象なしを扱う責務と対象解決責務が混ざっているので分離する
- URL からの target 解決と field からの target 解決は、戻り型を明確に分ける

### `src/vv_ai/session.py`

- `SessionStateRef` は provider 差分を見直す
- `provider_session_id` が必要な経路では必須型にする
- `summary_path` `artifact_hint` が本当に省略可能かを個別に判断する
- `ResolvedSession` も mode ごとに分ける
- `new` `inherit` `compact` の違いを nullable で表さない
- `restore_manifest` があるかないかを `None` で持たない
- `state_ref` があるかないかも mode 別型で表す
- `restore_strategy` と `mode` の重複関係も見直す

### `src/vv_ai/session_store.py`

- `saved_at: datetime | None = None` は削除対象
- 時刻は呼び出し側で確定して渡す
- `session_key: SessionKey | None = None` で絞り込みの有無を持つ API は再設計する
- 全件取得とキー指定取得を別関数に分ける
- 保存 JSON の manifest でも不要な nullable をやめる
- manifest は、その mode で必須な情報だけを持つ構造にする

### `src/vv_ai/session_artifact.py`

- `SessionArtifactMeta` の target 関連 nullable を整理する
- backend によって構造が違うなら別サブモデルに分ける
- `provider_session_id` が必須な保存物なら必須型にする
- `provider_session_path: Path | None = None` は削除対象
- 保存対象に provider session directory があるなら、呼び出し側が明示的に渡す
- `saved_at: datetime | None = None` は削除対象
- `allow_edits_notice_posted` が常に必要な値ならデフォルト値を持たせない
- Git 状態、target 状態、session 状態を平坦な一つの model に押し込めすぎない

### `src/vv_ai/metrics_artifact.py`

- `MetricsSummary` の target 関連 nullable を backend ごとに再設計する
- `MetricsUsage` の各項目が nullable の羅列になっている構造を見直す
- `MetricsBehavior` も同様
- `ToolMetric` `StepMetric` も未集計を nullable で持つ必要があるか再検討する
- provider 固有 metrics を `codex: ... | None` `claude: ... | None` で並べるのをやめる
- provider ごとに別構造へ分ける
- `save_metrics_artifact` の `usage=None` `behavior=None` `tools=None` `steps=None` `provider_specific=None` `saved_at=None` は削除対象
- 保存関数は完成済みモデルだけを受け取る
- 「未集計」と「0」は別概念として定義する

### `src/vv_ai/local_store.py`

- `generate_local_workflow_id(now: datetime | None = None)` は削除対象
- 現在時刻を使うなら、呼び出し側が明示的に渡す
- `create_local_issue` `create_local_pr` の `created_at=None` は削除対象
- `append_local_comment` の `slug_hint=None` `created_at=None` は削除対象
- slug を body から補完する挙動も再考する
- slug が必要なら呼び出し側が決める
- body が空でも fallback で comment にするような多段補完はやめる
- 現在時刻生成と保存処理を分離する

### `src/vv_ai/preflight.py`

- `ReadyExecution` の `resolved_session: ResolvedSession | None` は削除候補
- preflight の途中状態と session 解決後の状態を分ける
- 完成済み状態と未完成状態を同じ model に載せない
- `_normalize_optional_env_value` は環境変数入力の正規化なので許容候補
- ただし空文字を後段へ流さない責務を明記する

### `src/vv_ai/cli.py`

- `main(argv: Sequence[str] | None = None)` は CLI 慣習として例外扱い
- それ以外の通常関数で同様のデフォルト引数を増やさない
- CLI parser の default は外部ライブラリ都合として扱う
- ただし parser から受けた値を内部モデルへ流す時点で整理を終える

## 型設計の具体案

### command の分割案

- `ReplyCommand`
- `PlanCommand`
- `ImplementCommand`
- `ReviewCommand`
- `IssueCommand`

### target の分割案

- `GitHubTarget`
- `LocalTarget`

### session の分割案

- `NewSession`
- `InheritedSession`
- `CompactSession`

### provider metrics の分割案

- `CodexMetricsArtifact`
- `ClaudeMetricsArtifact`

### backend summary の分割案

- `GitHubMetricsSummary`
- `LocalMetricsSummary`

## デフォルト引数の扱い

### 禁止対象

- 通常関数の `arg: T | None = None`
- 通常関数の `arg: bool = False` を未指定扱いに使う設計
- class フィールドの `field: T | None = None`
- class フィールドの `field: T = <fallback>` が実質的に未指定救済になっている設計
- 空辞書、空 list、空 model を暗黙に補う設計
- 現在時刻の暗黙補完
- body から slug を暗黙に補完する設計

### 例外候補

- `argparse` の API が要求する default
- 外部ライブラリとの接続面で不可避な default

### 置き換え方

- 呼び出し側で値を確定してから渡す
- 生成責務を専用関数へ分離する
- union へ分割して未指定状態を型から消す
- 一覧取得 API と単一取得 API を分ける

## `None` と空文字の扱い

- `None` は「構造上その値が存在しない」場合だけに使う
- 空文字は有効値として扱わない
- 空文字を未指定へ変換するなら入力境界だけで行う
- 後段の `return stripped or None` は削除対象
- `value == ""` で欠損判定する構造は削除対象
- `value not in (None, False)` のような疑似未指定判定も削除対象

## 実装順の提案

- まず入力境界を固定する
- 次に command と target の内部モデルを分割する
- 次に session の mode 差分を型で表現する
- 次に artifact と metrics の保存モデルを分割する
- 最後に補助関数の `None` とデフォルト引数を掃除する
- 仕上げに、残した nullable と残した default の理由を一覧化する

## 実装時のレビュー観点

- その `None` は本当に構造上必要か
- その default は本当に外部 API の制約か
- command ごとの差が型に出ているか
- backend ごとの差が型に出ているか
- provider ごとの差が型に出ているか
- mode ごとの差が型に出ているか
- 空文字救済が入力境界の外へ漏れていないか
- `or` によるフォールバックが残っていないか
- boolean 補助フラグで型の弱さを補っていないか

## 実装後の最小限の手動確認

- CLI 直入力で不足項目が即例外になること
- `issue_comment` payload で空文字 instruction や不完全な target 指定が即例外になること
- `workflow_dispatch` payload で空文字や片側だけの指定を受け入れないこと
- `reply` `plan` `implement` `review` `issue` の各 command で、不要な `None` 判定なしに実行前状態まで進めること
- GitHub target と local target が別モデルとして扱われること
- session mode ごとの差分が `None` 判定でなく構造で扱われること
- artifact と metrics の保存 JSON に不要な `null` が残らないこと

## 完了の定義

- 後段処理が nullable 救済に依存していない
- 型だけで前提条件が追える
- 残存 nullable の理由が説明できる
- 残存 default の理由が説明できる
- 実装者がこの文書だけで改修順と判断基準を決められる

