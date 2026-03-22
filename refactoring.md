# Nullable とデフォルト値の整理仕様

## 前提

- この文書は nullable と不要なデフォルト値を整理する実装仕様
- この段階ではコードを書き換えない
- 実装はこの文書に従って進める
- requirements に書かれた product behavior は維持する
- requirements に書かれていない救済 fallback は増やさない
- 欠損は入力境界でだけ扱い、内部では型で前提条件を表現する

## この整理で維持する仕様

- command 省略時は `reply`
- `session` 省略時は `inherit`
- `dry_run` 省略時は `false`
- provider 明示指定が最優先
- provider 自動選択は `vv-ai.yml` の `provider_priority` を優先し、未指定なら `codex` `claude`
- provider 自動選択は優先順を先頭から調べ、秘密値がある最初の provider を選ぶ
- `issue` の `--repo` は GitHub event では未指定なら workflow の repo、local event では未指定なら local backend
- `target_url` は `target_type` と `target_number` より優先
- 未許可 `issue_comment` は完全サイレント
- `workflow_dispatch` は `Hiroshiba` だけが通る
- `workflow_dispatch` の `reply` `plan` `implement` `review` は workflow が置かれた repo だけを対象にする
- local replay の GitHub event は synthetic な workflow ID を使える
- Issue から PR を作る `implement` は Issue の main session を PR の main session へ fork する
- `compact` では fork 前に compact をかけた state を複製する
- `new` では PR 側を新規 session にする

## 削除対象と例外

- 削除対象
- `field: T | None = None`
- `arg: T | None = None`
- `value or fallback`
- `value.strip() or None`
- `if value is None: return fallback`
- `if value == "": return fallback`
- `usage or MetricsUsage()`
- `tools or {}`
- `steps or {}`
- `dry_run or False`
- `repo or repository_full_name`
- `slug_hint or body or "comment"`
- 残してよい `None`
- 外部入力の生データ
- 永続化上、本当に省略可能と定義した構造
- 残してよい default
- `argparse` が要求する parser default
- schema version の固定値
- 判別用の固定 literal
- 外部ライブラリ都合で避けられない値
- 残さない default
- product behavior を暗黙に補う field default
- 現在時刻の暗黙補完
- body から slug を補う値
- status の初期値のような生成時挙動

## runtime 文脈

- `GitHubRunContext`
- GitHub Actions 上の実行
- `GITHUB_RUN_ID` と `GITHUB_RUN_ATTEMPT` を使う
- `GitHubReplayContext`
- local で GitHub event payload を再現する実行
- debug 用の synthetic workflow ID を使う
- `LocalRunContext`
- local CLI 直実行
- local workflow ID を使う

## 層ごとの責務

### 入力層

- CLI 引数、GitHub event payload、`vv-ai.yml` を受ける
- 空文字と空白だけの文字列はこの層で処理を終える
- event-file の event 判定のような字句的補助はこの層で完結させる
- nullable を後段へ持ち込まない

### 正規化層

- product behavior の既定値をここで明示的に確定する
- command 別、event 別、target 入力別、provider 選択別の内部 model へ分解する
- `has_target` のような補助フラグは使わない
- `repo or repository_full_name` のような後段 fallback はここで置き換える

### 実行層

- 完成済みの内部 model だけを受け取る
- `None` 判定ではなく型分岐で処理する
- event 差分、backend 差分、provider 差分、session mode 差分を union で表現する

### 永続化層

- 保存 JSON に不要な `null` を残さない
- backend 別 provider 別に shape を分ける
- 保存関数は完成済み model だけを受け取る

## 仕様で保持する既定値の置き場所

- `reply` 既定は parser 後の command 正規化で確定する
- `inherit` 既定は session 入力正規化で確定する
- `false` 既定は dry-run 入力正規化で確定する
- provider 既定順は provider 選択入力の生成時に `codex` `claude` を明示的に埋める
- GitHub event の `issue` repo 既定は issue destination 正規化で workflow repo を埋める
- local event の `issue` repo 省略は `LocalIssueDestination` を選ぶことで表す
- status の初期値は `create_local_issue` `create_local_pr` の呼び出し側で明示する
- synthetic workflow ID は replay context の生成で明示する

## 内部 model の分割方針

### command と event

- `ResolvedCommand` は廃止する
- command は少なくとも `ReplyCommand` `PlanCommand` `ImplementCommand` `ReviewCommand` `IssueCommand` に分ける
- event は少なくとも `IssueCommentEventCommand` `WorkflowDispatchCommand` `LocalCommand` に分ける
- `actor` `comment_id` `comment_author` `comment_body` を共通 optional field に置かない
- `issue_comment` は target を必ず持つ event model にする
- `workflow_dispatch` は target 省略可の event model にする
- local は GitHub comment metadata を持たない event model にする
- `workflow_dispatch` の通常コマンドは workflow repo 外の GitHub target を弾く

### target 入力

- `target_url` に GitHub URL と local path を同居させない
- 正規化層で少なくとも次に分ける
- `GitHubUrlTargetInput`
- `LocalPathTargetInput`
- `LocalDirectoryTargetInput`
- `LocalDocumentTargetInput`
- `GitHubNumberTargetInput`
- 対象なしは command 側の型で表す
- `target_url` 優先は正規化層で分岐順として固定する
- `workflow_dispatch` の通常コマンドでは GitHub target の repo が workflow repo と一致することを正規化層で検証する
- local target は directory と markdown file の両方を受ける

### issue の行き先

- `issue` の行き先は専用型へ分ける
- `ExplicitIssueRepoDestination`
- `WorkflowRepositoryIssueDestination`
- `LocalIssueDestination`
- 後段は destination 型だけを見る

### provider

- provider 未確定の入力と provider 確定後の入力を分ける
- provider source は少なくとも次に分ける
- `ExplicitProviderSelection`
- `ConfigPriorityProviderSelection`
- `DefaultPriorityProviderSelection`
- 明示指定では指定 provider だけを検証する
- 自動選択では優先順を先頭から順に見て、使える最初の provider を選ぶ
- 先頭が使えないときは次へ進む

### session

- `ResolvedSession` は廃止する
- 少なくとも `NewSession` `InheritedSession` `CompactSession` に分ける
- `restore_manifest` と `state_ref` を optional field にしない
- session scope は target あり command、GitHub issue 作成、local issue 作成で別型にする
- Issue から PR を作る経路は通常の target 変更と分けて `ForkedSession` のような専用遷移型で表す
- fork 元の Issue session key と fork 先の PR session key の関係を型に残す

### persistence

- summary と meta は backend 別に分ける
- provider 固有 metrics は provider 別に分ける
- session manifest の一覧取得とキー指定取得は API を分ける
- artifact 名の正規化は空文字 fallback を使わず、正規化後に空なら例外にする

## モジュール別の決定

### `src/vv_ai/input.py`

- `CLIInput` は外部入力なので nullable を許容してよい
- `RawInput` は削る
- `CommentInvocation` も command 別 model に寄せる
- `_coerce_optional_*` は入力層専用として残してよい
- `parse_comment_invocation` の command 省略 `reply` は仕様として維持する
- `event_file` の event 自動判定は入力層の補助として扱う
- `dry_run` の既定 `false` は parser 後の正規化で確定する

### `src/vv_ai/config.py`

- `VVAIConfig.provider_priority` の field default は削る
- config model は設定ファイルに書かれた値だけを表す
- built-in の `codex` `claude` は config model ではなく provider 選択入力の生成で埋める
- `find_repo_root` の探索順は `vv-ai.yml`、`.git`、現在地で固定し、repo root 解決の仕様として明記する

### `src/vv_ai/resolve.py`

- `ResolvedTarget` を `resolve.py` に置かない
- command の必須条件は command 別 model で表す
- `instruction: str | None`
- `provider: ProviderName | None`
- `session_mode: SessionMode | None`
- `repo: str | None`
- `target_url: str | None`
- `target_type: TargetType | None`
- `target_number: int | None`
- `has_target`
- 上記の共通 optional field は削除対象

### `src/vv_ai/target.py`

- `GitHubTarget` と `LocalTarget` に分ける
- GitHub URL 判定と local path 判定を input 型の段階で終える
- `startswith("https://github.com/")` のような文字列判定を後段へ残さない
- local target path の許可範囲は `.vv-ai/issues/<id>` `.vv-ai/issues/<id>/issue.md` `.vv-ai/prs/<id>` `.vv-ai/prs/<id>/pr.md` に固定する

### `src/vv_ai/provider.py`

- `resolve_provider` の入力を command 全体ではなく provider 選択入力へ絞る
- `ProviderSource` は `explicit` `config` `default` に分ける
- required secrets のエラーメッセージは最終的な優先順から組み立てる

### `src/vv_ai/preflight.py`

- `ReadyExecution.resolved_session: ResolvedSession | None` は削除する
- preflight 完了状態と session 解決後状態を分ける
- 認可は event 別 model を受けて行い、`actor is None` を見ない
- unauthorized `issue_comment` の silent skip は専用 result 型で維持する
- `workflow_dispatch` の `Hiroshiba` 制約は preflight 仕様として維持する
- workflow ID は runtime context 型から解決する

### `src/vv_ai/session.py`

- `SessionStateRef` は provider 別に再設計する
- `provider_session_id` が必要な経路では必須にする
- `summary_path` `artifact_hint` の必要性を個別に判断する
- `restore_strategy` と `mode` の重複をなくす
- Issue → PR fork の入力と結果を専用型で表し、通常の `inherit` `compact` `new` と合流させる位置を明示する

### `src/vv_ai/session_store.py`

- `saved_at: datetime | None = None` は削除する
- `session_key: SessionKey | None = None` の API は分離する
- filename 生成はそのままでもよいが、入力は完成済み `SessionKey` だけにする

### `src/vv_ai/local_store.py`

- `generate_local_workflow_id(now: datetime | None = None)` は削除する
- `create_local_issue` `create_local_pr` の `created_at=None` は削除する
- `append_local_comment` の `slug_hint=None` `created_at=None` は削除する
- `LocalIssueMeta.status = "open"` `LocalPRMeta.status = "open"` は生成側で明示し、field default に置かない
- `kind` と `backend` の固定 literal は判別用定数として残してよい
- `_slugify(..., fallback=...)` は fallback をやめ、空なら例外にする

### `src/vv_ai/session_artifact.py`

- `SessionArtifactMeta` は backend 別に分ける
- `provider_session_path: Path | None = None` は削除する
- `saved_at: datetime | None = None` は削除する
- `allow_edits_notice_posted` は本当に常時必要なら呼び出し側で明示する
- `_resolve_repository_full_name` の文字列 fallback は destination 型と target 型から解決する形へ置き換える
- `_sanitize_name` の `"unknown"` fallback は削除する

### `src/vv_ai/metrics_artifact.py`

- `MetricsSummary` は backend 別に分ける
- `MetricsUsage` `MetricsBehavior` `ToolMetric` `StepMetric` の nullable 羅列を見直す
- `ProviderSpecificMetrics` は provider 別 union に置き換える
- `save_metrics_artifact` の optional 引数は削除する
- `_resolve_repository_full_name` の文字列 fallback は destination 型と target 型から解決する形へ置き換える
- `_sanitize_name` の `"item"` fallback は削除する

### `src/vv_ai/cli.py`

- `main(argv: Sequence[str] | None = None)` は CLI 慣習として例外扱い
- parser default は外部 API 都合として残してよい
- parser の出力を内部 model へ流すまでに product behavior の既定値を確定する
- `preflight_result` の再代入で異なる状態を同じ変数に入れない

## トレーサビリティ確認項目

- requirements にある既定動作が文書へ 1 行ずつ書かれている
- repo に残る `| None` すべてに理由がある
- repo に残る field default すべてに理由がある
- repo に残る function default すべてに理由がある
- `or` fallback が入力境界以外から消えている
- event 差分が型へ出ている
- backend 差分が型へ出ている
- provider 差分が型へ出ている
- session mode 差分が型へ出ている
- runtime 文脈の違いが型へ出ている

## 実装順

- まず `input.py` `config.py` `resolve.py` の入力境界を固定する
- 次に `target.py` `provider.py` `preflight.py` の分岐を型へ移す
- 次に `session.py` `session_store.py` を分割する
- 次に `local_store.py` の hidden fallback を消す
- 次に `session_artifact.py` `metrics_artifact.py` を backend 別 provider 別へ寄せる
- 最後に `cli.py` の状態遷移を整理する

## 最小限の手動確認

- CLI 直入力で command 省略時に `reply` になる
- `session` 省略時に `inherit` になる
- `dry_run` 省略時に `false` になる
- explicit provider は config の優先順が無くても成立する
- implicit provider は下位優先 provider に落ちられる
- GitHub event の `issue` は repo 未指定で workflow repo になる
- local event の `issue` は repo 未指定で local backend になる
- unauthorized `issue_comment` は silent skip のまま
- `workflow_dispatch` の actor 制約が保たれる
- `target_url` 優先と GitHub URL / local path 分岐が保たれる
- replay context で synthetic workflow ID が作られる
- artifact と metrics の保存 JSON に不要な `null` が残らない

## 完了条件

- この文書だけで実装者が型分割と既定動作の置き場を決められる
- requirements と衝突する余地が残っていない
- repo にある nullable、default、fallback の処遇がすべて文書化されている
