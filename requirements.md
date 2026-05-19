# PROJECT REQUIREMENTS — vv-ai

## SOFTWARE TYPE

GitHub Actions ワークフロー + Python CLI ツール（Codex CLI / Claude Code CLI を活用した AI 自動化基盤）

## PROJECT OVERVIEW

GitHub の Issue / PR に対してコメント、ラベル、ワークフローディスパッチで AI（Codex CLI / Claude Code CLI）を起動し、計画・実装・レビュー・Issue 作成などのタスクを自動実行する仕組み。ローカル実行は GitHub Actions の再現実行やローカル Issue / PR を使ったデバッグ補助として扱う。各リポジトリにソースをコピーして導入するプロトタイプとして開始し、将来的に Reusable Workflow への切り出しを見据える。

---

## COMMAND SYSTEM

### プレフィックス

- コメント起動: `@vv-ai`（`@` 付き、本文先頭に記述）
- ラベル起動: `vv-ai:<command>`（対象 Issue / PR に label として付与）

### サブコマンド一覧

| コマンド       | 説明                                                         | Issue 上 | PR 上 |
| -------------- | ------------------------------------------------------------ | -------- | ----- |
| （省略時）     | **reply**（デフォルト）。指示に対してコメントで返答するだけ   | ✅        | ✅     |
| `confirm`      | confirm-intent スキルで要望の意図確認を行いコメントで返す    | ✅        | ✅     |
| `requirements` | define-requirements スキルで要件定義を行いコメントで返す     | ✅        | ✅     |
| `arch`       | basic-design スキルで基本設計を行いコメントで返す            | ✅        | ✅     |
| `detail`       | detailed-design スキルで詳細設計を行いコメントで返す         | ✅        | ✅     |
| `breakdown`    | task-breakdown スキルでタスク分割し、サブ Issue を作成する   | ✅        | —      |
| `implement`    | 実装して PR 作成、または既存 PR に追コミット                       | ✅        | ✅     |
| `address`      | PR のレビュー指摘に対応して追コミット                         | —        | ✅     |
| `review`       | PR をレビューし、指摘・改善提案をコメント                    | —        | ✅     |
| `sync`         | PR ブランチをベースブランチに同期する                       | —        | ✅     |
| `issue`        | 自然言語指示から Issue を作成                                | ✅        | ✅     |
| `next`         | 履歴と必要時の AI 判断で次の既存工程を実行するショートカット | ✅        | ✅     |

AI が工程成果物をコメントとして返す結果コメントは、本文の先頭に内容種別を表す H2 見出しを付ける。任意返信や、実装・レビュー対応・同期など作業結果の連絡コメントには付けない。`next` は解決後の工程に従う。

`sync` は PR 専用コマンドとして実行し、公開用の同期コマンドは分けない。PR head branch を checkout し、`origin/<base>` との共通祖先を判定できる履歴を取得して取り込み状況を確認する。base branch がすでに HEAD の祖先なら merge commit は作らない。取り込みが必要なら `--no-ff --no-commit` で merge し、conflict がなければ wrapper が merge commit を作成する。

conflict がある場合、AI には conflict file の解消だけを依頼する。AI が commit や stage を行った場合、想定外の staged diff がある場合、conflict marker が残った場合、未解消 conflict が残った場合は失敗する。wrapper は AI 実行後に conflict file だけを stage し、merge commit を作成する。marker がない conflict file は、内容または存在状態が不変でも stage 対象にする。conflict 解消と整合性確認は別の provider 実行にし、conflict ありの sync は provider 実行 2 回とする。

merge commit 後または merge 不要判定後、AI に整合性確認、必要最小限の修正、最終コメント本文の作成を依頼する。conflict なしの sync は provider 実行 1 回とする。修正がある場合、wrapper が `chore: sync 整合性を修正する` で別 commit を作成する。merge commit も整合性修正 commit もない場合は push しない。

push が必要な same repository PR では head branch を origin へ push する。push が必要な fork PR では現在 branch の upstream へ push し、失敗した場合は patch または手順を PR にコメントして失敗する。push 不要の fork PR は push 権限不足を失敗扱いしない。

push 成功後または push 不要時、wrapper は整合性確認 AI の出力から最終コメント本文を取り出して投稿する。最終コメント本文には push 結果や push 後の GitHub PR 状態を含めない。最終コメント投稿の失敗は、push 済みまたは push 不要の同期結果を失敗に変えない。

### オプション

| オプション                          | 説明                                                   | デフォルト              |
| ----------------------------------- | ------------------------------------------------------ | ----------------------- |
| `--provider codex\|claude`          | 使用する AI プロバイダを上書き指定                      | 優先順に従い自動選択    |
| `--session_mode inherit\|inherit_or_new\|compact\|new` | セッション継続方式                                  | `inherit_or_new`        |
| `--dry-run`                         | GitHub への外部反映を一切行わない（artifact のみ保存） | `false`                 |
| `--repo org/repo`                   | Issue 作成先リポジトリ（`issue` コマンド専用）         | workflow が置かれた repo |

### コマンド例

```
@vv-ai 調べて要点だけ返して
@vv-ai confirm
@vv-ai requirements
@vv-ai arch 基本設計の方針を示して
@vv-ai detail
@vv-ai breakdown
@vv-ai implement --provider codex このIssueを実装して
@vv-ai address --provider codex レビュー指摘に対応して
@vv-ai review --session_mode inherit このPRをレビューして
@vv-ai sync
@vv-ai issue --repo org/repo この不具合をIssue化して
@vv-ai next
@vv-ai implement --dry-run この修正を試してみて
```

### next の解決

- `next` は原則として履歴から次の既存コマンドへ解決するショートカット
- 通常 Issue の履歴なし `next` は `confirm`
- サブ Issue の履歴なし `next` は `implement`
- Issue では `confirm` → `requirements` → `arch` → `detail` の順に進む
- 通常 Issue の `detail` 後の `next` は AI が `breakdown` または `implement` を判断する
- AI 判断付き `next` の選択結果は履歴保存コメントとして残し、後続の `next` 履歴再生で選択済みコマンドとして扱う
- 親 Issue の `breakdown` 後の `next` はエラー終了
- Issue の `implement` 後の `next` はエラー終了
- PR の履歴なし `next` は `review`
- PR では `review` と `address` を交互に実行
- PR の `implement` 後の `next` は `review`

---

## 起動経路

### 1. GitHub コメント起動（`issue_comment`）

- Issue コメントまたは PR コメントで `@vv-ai ...` と書くと起動
- 許可ユーザーのコメントのみ反応。未許可は**完全サイレント**（何も返さない）

### 2. GitHub ラベル起動（`issues.labeled` / `pull_request.labeled`）

- Issue または PR に `vv-ai:<command>` label を付けると起動
- 許可ユーザーの label 付与のみ反応。未許可は**完全サイレント**（何も返さない）
- label 名から command を決め、`instruction` はなしとして扱う

### 3. GitHub workflow_dispatch

- 手元 PC から `gh workflow run ...` で起動
- 入力項目:
  - `command`: confirm | requirements | arch | detail | breakdown | implement | address | review | sync | issue | next | reply
  - `target_type`: issue | pr（任意）
  - `target_number`: 番号（任意）
  - `target_url`: Issue/PR URL（任意）
  - `instruction`: 自然言語指示（コマンドにより必須/任意）
  - `provider`: codex | claude（任意）
  - `session_mode`: inherit | inherit_or_new | compact | new（任意）
  - `dry_run`: true/false（任意）
  - `repo`: Issue 作成先 org/repo（`issue` 用、任意）
- `target_type`/`target_number` と `target_url` の両対応（C 方式）
  - `target_url` が優先
- 対象省略時の扱い:
  - `issue` コマンド: 対象不要（repo 未指定なら workflow のある repo に作成）
  - `confirm` / `reply` / `requirements` / `arch` / `detail` / `breakdown` / `implement` / `address` / `review` / `sync` / `next`: 対象必須（不足ならエラー終了）
- `instruction` 省略:
  - `confirm` / `reply` / `requirements` / `arch` / `detail` / `breakdown` / `implement` / `address` / `review` / `sync` / `issue` / `next`: 省略可
- GitHub 上への可視化: **何もしない**。Actions Run と artifact だけを見る運用
- 認可: `github.actor == "Hiroshiba"` を必須チェック（workflow 実行権限があっても Hiroshiba 以外は即終了）
- 対象 repo:
  - 通常コマンド: workflow が置かれた repo のみ
  - `issue` コマンドのみ: `--repo` で別 repo も可（任意 OK）

### 4. ローカル CLI によるデバッグ

- CLI 名: `vv-ai`
- サブコマンドなし（`vv-ai [options]` で直接実行）
- workflow 側からもこの CLI を呼ぶ
- 通常運用の入口ではなく、GitHub Actions の挙動を手元で再現するために使う
- 入力方法:
  - 直接指定: `--command`, `--instruction`, `--target-url`, `--target-type`, `--target-number`, `--provider`, `--session_mode`, `--dry-run`, `--repo`
  - イベントファイル: `--event-file <json>` で GitHub event payload を読み込み再現実行
  - `--event issue_comment|workflow_dispatch|issues|pull_request|local`
- ローカルデバッグは主に **dry-run** で実施
- 扱うコマンドは `@vv-ai` と同じ（専用デバッグコマンドは設けない）

---

## ワークフロー設計

### イベントトリガー

| イベント             | 有効/無効 |
| -------------------- | --------- |
| `issue_comment`      | ✅ 有効    |
| `workflow_dispatch`   | ✅ 有効    |
| `issues.labeled`     | ✅ 有効    |
| `pull_request.labeled` | ✅ 有効  |
| `issues.opened`      | ❌ 無効    |

### ワークフロー構成

- **1 本の workflow** に `issue_comment`、`issues.labeled`、`pull_request.labeled`、`workflow_dispatch` を同居
- 同一 Issue/PR 番号の実行は **直列化（キュー）**
  - GitHub Actions の `concurrency` を使用
  - `cancel-in-progress: false`（前の実行が終わるまで待つ）

---

## BACKEND SYSTEM

### GitHub Backend

- GitHub.com の **Public repo** が主対象
- GitHub Enterprise Server（オンプレ）は対象外
- 外部フォークからの PR も想定
- GitHub API 操作は **`gh` コマンド**で統一

### Local Backend

- GitHub Actions での処理を手元で再現するための補助 backend
- ローカル Issue / PR をファイルベースで管理し、デバッグ用 target を作る
- `--target-url` にローカルパスを指定して起動

### Backend 判定ルール

- `https://github.com/` で始まる → `github`
- 存在するローカルパスとして解決可能 → `local`
- どちらにも当てはまらない → 入力エラー

---

## ローカル ISSUE / PR 管理

### ディレクトリ構造

```
.vv-ai/
  issues/
    <issue-id>/
      issue.md          # Issue 本文
      meta.json          # メタ情報
      comments/          # コメント群
        <timestamp>-<slug>.md
  prs/
    <pr-id>/
      pr.md              # PR 説明
      meta.json          # メタ情報
      comments/
        <timestamp>-<slug>.md
  artifacts/
    <workflow-id>/       # 実行単位でまとめる
      sessions/
      metrics/
      reports/
```

### ID 規約

- 人が読める slug + 短いランダム suffix
  - 例: `login-403-7k2p9a`, `refactor-engine-1m4x8q`

### meta.json 最低項目

| フィールド     | 説明               | 備考           |
| -------------- | ------------------ | -------------- |
| `id`           | Issue/PR の ID     |                |
| `kind`         | `issue` or `pr`    |                |
| `status`       | 状態               |                |
| `created_at`   | 作成日時           |                |
| `updated_at`   | 更新日時           |                |
| `backend`      | `local`            |                |
| `head_branch`  | 作業ブランチ       | PR のみ        |
| `base_branch`  | ベースブランチ     | PR のみ        |

### ローカル実行時の workflow_id

- CLI 側で生成（UUID またはタイムスタンプ + ランダム suffix）

---

## PROVIDER 設計

### 対応プロバイダ

- **Codex CLI**（OpenAI）
- **Claude Code CLI**（Anthropic）

### 選択ルール（優先順位）

1. `--provider` 引数で明示指定されていればそれを使う
2. `vv-ai.yml` の `provider_priority` に従う
3. どちらも未指定なら: **codex → claude**（Secrets が存在する方を自動選択、両方あれば codex 優先）

### Secrets 名

| Secret 名                 | 用途                       |
| ------------------------- | -------------------------- |
| `VV_OPENAI_API_KEY`       | Codex CLI 用               |
| `VV_ANTHROPIC_API_KEY`    | Claude Code CLI 用         |
| `VV_AI_AGE_PUBLIC_KEY`    | artifact 暗号化（公開鍵）  |
| `VV_AI_AGE_SECRET_KEY`    | artifact 復号（秘密鍵）    |

---

## リポジトリ設定ファイル

### ファイル: `vv-ai.yml`（リポジトリルート）

```yaml
allowed_users:
  - Hiroshiba

provider_priority:
  - codex
  - claude
```

### 設定項目

| 項目                | 説明                                    |
| ------------------- | --------------------------------------- |
| `allowed_users`     | コマンド実行を許可する GitHub ユーザー  |
| `provider_priority` | プロバイダの優先順                      |

---

## 権限・認可

### MVP

- `vv-ai.yml` の `allowed_users` に入っているユーザーのみ実行可能
- MVP では **Hiroshiba** のみ
- workflow_dispatch では追加で `github.actor == "Hiroshiba"` をチェック

### 未許可ユーザーの操作

- **完全サイレント**（コメントもリアクションも返さない）

### 将来拡張（MVP 外）

- Write 権限以上
- Team 単位
- CODEOWNERS 相当

---

## セッション管理

### セッションスコープ

- **同一 Issue/PR 内** で `confirm` / `reply` / `requirements` / `arch` / `detail` / `breakdown` / `implement` / `address` は **同じセッション**（main lane）を共有
- `review` は **別セッション**（review lane）
- `next` は既存コマンドへ解決した後、そのコマンドの lane を使う
- セッションキー: `<backend> / <target> / <provider> / <lane>`
  - 例: `github / org/repo#123 / codex / main`
  - 例: `local / issue:login-403-7k2p9a / codex / main`

### セッション継続方式（`--session_mode`）

| 値               | 説明                                                                        |
| ---------------- | --------------------------------------------------------------------------- |
| `inherit`        | 前回セッションを必ず継続。保存済みセッションが見つからないとエラー終了       |
| `inherit_or_new` | 前回セッションがあれば継続、なければ新規で開始（**デフォルト**）             |
| `compact`        | コンパクト化して継続。保存済みセッションが見つからないとエラー終了           |
| `new`            | 新規セッション                                                              |

### Issue → PR 作成時のセッション引き継ぎ

- Issue の main セッションを **フォーク（複製）** して PR の main セッションとする
- `--session_mode compact` 指定時: フォーク前に compact をかけた状態を複製
- `--session_mode new` 指定時: PR 側は複製せず新規
- 複数の PR を同じ Issue から作った場合: PR 番号が異なるため各 PR は別セッション

### セッション共有

- 起動経路（コメント / workflow_dispatch / ローカル CLI）に関係なく、同じ target × provider × lane なら共有

### セッション保存内容

| 内容                         | 説明                                         |
| ---------------------------- | -------------------------------------------- |
| CLI セッションディレクトリ   | Codex/Claude Code の継続に必要な状態を丸ごと |
| git diff                     | ワークツリーの変更（追跡ファイルのみ）       |
| git diff --staged            | ステージ済みの変更                           |
| git status --porcelain       | ファイル状態                                 |
| メタ情報 JSON                | org/repo, Issue/PR 番号, provider, lane, ブランチ名, HEAD SHA, 保存時刻, Allow edits 案内済みフラグ |

---

## ARTIFACT 保存

### 保存対象（3 系統、すべて別ファイル）

| artifact              | 内容                           | 形式       |
| --------------------- | ------------------------------ | ---------- |
| session artifact      | CLI セッション + git 差分 + メタ | 暗号化バンドル |
| metrics artifact      | 実行統計                       | 暗号化 JSON  |
| report artifact       | タスクレポート                 | 暗号化 Markdown |

### 暗号化方式

- **age** を使用（公開鍵暗号）
- GitHub Secrets に公開鍵と秘密鍵の **両方** を保存
  - ただし **用途を限定**: 公開鍵は暗号化ステップのみ、秘密鍵は復号が必要なステップのみに渡す
- 手元 PC にも同じ秘密鍵を保持し、artifact をローカルでも復号可能

### GitHub 保存先

- GitHub Actions の **Artifacts**
- artifact 名: 固定 prefix + セッションキー + run ID
  - 例: `vv-ai-session__pr-456__codex__main__run-1234567890`
- 保持期間: **90 日**（GitHub 側の GC に任せる）
- **削除しない** 運用（削除権限不要。古いものは自然に期限切れ）
- 復元時: REST API で repo 全体から名前 prefix 一致 → 最新を取得

### ローカル保存先

```
.vv-ai/artifacts/<workflow-id>/
  sessions/
  metrics/
  reports/
```

### 復元失敗時

- `--session_mode inherit` / `--session_mode compact` で artifact が見つからない場合: **エラー終了**
- `--session_mode inherit_or_new` の場合: 探しに行き、見つからなければ新規開始
- `--session_mode new` の場合: 探さず新規開始
- `--session_mode new` は保存済み session を探さない指定であり、同じ `sync` 実行内の後続 provider 実行まで毎回新規にする指定ではない

### 必ず保存するタイミング

- **成功・失敗・cancel のいずれでも** session / metrics / report は保存する（`finally` 相当で保証）

---

## METRICS

### 収集方針

- **Codex**: `--json` 出力を主ソース、OTel を従
- **Claude Code**: OTel を主ソース
- 取れない項目は `null`
- per-run で 1 つの metrics JSON を生成

### データ構造

- 最上位に `schema_version` を含める
- 大項目:
  - `summary`: 実行の概要
  - `usage`: トークン・コスト
  - `behavior`: ターン数・成功率・判断
  - `tools`: ツール別の統計
  - `steps`: フェーズ別の所要時間
  - `provider_specific`: プロバイダ固有の詳細

### Codex 固有

- input / cached_input / output tokens
- total_turns / failed_turns / success_rate
- command_execution_count
- file_change_count
- mcp_tool_call_count
- web_search_count
- plan_update_count

### Claude Code 固有

- cost_usd
- input / output / cache tokens
- session_count
- lines_added / lines_removed
- pr_count / commit_count
- code_edit_decision counts
- active_time_seconds
- tool_name 別 success / failure / duration 集計

---

## REPORT

### 生成タイミング

- 毎回のタスク完了後（成功・失敗問わず）

### 保存形式

- Markdown（`report.md`）
- session / metrics とは別の暗号化 artifact として保存

### テンプレート

```markdown
# Report

## Summary
（何を頼まれ、最終的に何をしたかを短くまとめる）

## Changes
（どのような変更を加えたか。コード変更だけでなく、調査・設計・設定変更・レビュー内容も含む）

## Decisions
（重要な判断。何を選び、なぜそうしたかを短く添える）

## Validation
（実施した確認。テスト、lint、build、レビュー観点、手動確認。未確認事項も記載）

## Risks / Open Questions
（詰まった点、失敗した点、未解決の懸念。回避策や暫定対応があれば記載）

## Next Actions
（続きでやるとよいこと。次回のAIや人間がそのまま着手しやすい粒度で記載）

## Notes
（上のどこにも入りにくいが後で読む価値があること。一時的な学びや観察。恒久ルールのようには書かない）
```

### 書き方ルール

- 短く、具体的に書く
- 事実と判断を分ける
- 長いログは貼らない
- **Secrets、認証情報、鍵、内部 URL、トークンは書かない**
- metrics 的な数値の詳細は書かない（必要なら要点だけ）
- 恒久ルール化したい内容でも、この report では断定しない
- 次回の作業者が「何を見て、どこから始めるか」が分かる状態を目指す

---

## IMPLEMENT コマンド詳細

### Issue 起点

- 毎回 **新規ブランチ + 新規 PR** を作成
- ブランチ名: `vv-ai/issue-<番号>-<6〜8桁ランダム英数字>`
- PR タイトル / commit message / description は AI の最終出力から決定
- 複数回実行しても毎回新規（既存 PR への自動更新はしない。既存 PR で作業させたい場合はコメントで追加指示）

### PR 起点

- 既存 PR のブランチで作業（追コミットで更新）
- commit message は AI の最終出力から決定
- 対象 PR に AI の応答本文をコメント投稿

### Runner 側の前処理

- `git checkout` とブランチ切り替えを先に完了
- そのブランチ上にいることを AI に定型プロンプトで伝える

### プロンプト

- Issue 起点の出力形式
  - 1 行目: `TITLE: <タイトル文字列>`
  - 2 行目: `COMMIT_MESSAGE: <コミットメッセージ>`
  - 3 行目: `BODY:`
  - 4 行目以降: Markdown 本文
- PR 起点の出力形式
  - 1 行目: `COMMIT_MESSAGE: <コミットメッセージ>`
  - 2 行目: `BODY:`
  - 3 行目以降: Markdown の PR コメント本文
- PR タイトルと commit message は Conventional Commits 形式を推奨
- Issue 起点の PR description は元 Issue への参照を含める
- Issue 起点の PR description は、Issue を解決する内容なら GitHub closing keyword を使ってもよい

---

## ISSUE 作成コマンド詳細

### コマンド

```
@vv-ai issue [--repo org/repo] <自然言語指示>
```

### AI 出力フォーマット

- 1 行目: `TITLE: <タイトル文字列>`
- 2 行目: `BODY:`
- 3 行目以降: Markdown 本文

### 実行

- ワークフロー側は TITLE 行を抽出して `--title` に、BODY 以降を `/tmp/issue.md` に書いて `gh issue create --body-file` で作成
- タイトルと本文の内容決定はユーザーが用意するプロンプトに委譲

### 結果の通知

- 成功時: 起点コメントに Issue リンクを **1 行だけ返信**（例: `Created: https://github.com/.../issues/123`）
- これは `issue` コマンドのみの例外（他のコマンドはコメントを残さない）

### `--repo` 指定

- 任意の repo を指定可能（MVP では Hiroshiba のみ実行なので制限なし）
- 未指定時: workflow が置かれた repo

---

## FORK PR 対応

### 前提

- Public repo + 外部フォークからの PR を想定

### implement 時の挙動

1. **可能なら** fork 側の head ブランチに push（追コミット）
2. **push できなければ** patch（差分）をコメントで提示
3. push できなかった場合: patch + **「Allow edits from maintainers を有効にすると直接修正できる」旨の案内コメント**
   - この案内は **PR ごとに 1 回だけ**（案内済みフラグを session メタに保存）

### セキュリティ上の注意

- fork PR 由来の `pull_request` イベントでは Secrets が渡らない
- `pull_request_target` は未信頼コードの checkout/実行リスクがあるため避ける
- コメント起動は base repo 側のトリガーとして処理する
- PR ラベル起動は外部 fork PR では Secrets と権限の制約を受ける

---

## AI に渡すコンテキスト

### コメント起動時

- **渡すもの:**
  - org / repo / Issue(or PR) 番号
  - 実行タスクの定型文（「plan を実行してください」等）
  - `@vv-ai ...` コメント本文（そのまま）
  - target context として Issue/PR のタイトル・description・コメント
    - 同じ provider セッション中に同じ target context は 1 回だけ渡す
    - 継続セッションでは前回以降に追加または編集された target context だけ渡す

### ラベル起動時

- **渡すもの:**
  - org / repo / Issue(or PR) 番号
  - label 名から決まる実行タスクの定型文
  - target context として Issue/PR のタイトル・description・コメント
    - 同じ provider セッション中に同じ target context は 1 回だけ渡す
    - 継続セッションでは前回以降に追加または編集された target context だけ渡す

### 起動定型プロンプト（AI に伝える情報）

- 「あなたは GitHub Actions runner 上で実行中。repo: `<org>/<repo>`、対象: Issue|PR `#NNN`、現在のブランチ: `<branch>`」
- 「このブランチは checkout 済み。git 追跡ファイルはブランチの内容が永続（push/commit されたものが正）」
- 「未追跡ファイルは原則永続しない（毎回クリーンアップされる想定）。必要なら git 管理に入れる」
- 「前回のセッション状態 +（必要なら）未コミット差分を、暗号化バンドルから復元済み」（該当時のみ）

---

## SECURITY 設計

### 脅威モデル

- Public repo + fork PR 前提
- プロンプトインジェクション（未信頼コード/差分/コメント → LLM 誘導 → 任意実行）を想定
- **目標: どんなプロンプトインジェクションが来ても、API キーと GitHub トークンが構造的に抜けない**

### Claude Code

1. **apiKeyHelper** で認証情報を注入（settings に直接キーを書かない）
   - 外部スクリプトが認証ヘッダを返す仕組み
   - settings に書くのは「スクリプトへのパス」だけ
2. **Sandbox を常時 ON**
   - `allowUnsandboxedCommands: false` で脱出を封じる
3. **denyRead** で秘密領域を遮断
   - 秘密ファイル置き場（例: `/home/runner/.vv-secrets/**`）
   - `/proc/**`（プロセスメモリ・環境変数覗き経路）
   - `~/.claude/**`（Claude の設定・状態ファイル）
4. **permissions.deny** で Read ツール側も二重に遮断

### Codex CLI

1. **shell_environment_policy** でサブプロセスへの環境変数伝播を制御
   - include_only で PATH / HOME 等だけに絞る
   - VV_OPENAI_API_KEY 等を子プロセスに渡さない
2. **workspace-write のネットワークを有効化**
   - `gh` から GitHub API を読めるようにする

### GitHub Actions ステップ分離

- **Secrets ありステップ**: 秘密ファイル（0400）と apiKeyHelper スクリプトを作成するだけ
- **CLI 実行ステップ**: Secrets を env に入れずに Codex / Claude Code を起動
  - 子プロセスにも env 経由で渡らない

### GitHub 書き込み権限の分離

- write 権限のある **GITHUB_TOKEN は AI プロセスに渡さない**
- GitHub App 経由の read-only トークンを `GH_TOKEN` として AI プロセスに渡す（Issue/PR/コード読み取り用）
- `git commit` / `git push` / PR 作成 / Issue 作成 / コメント投稿は **非 AI ラッパー**（ワークフローステップ）が実施

---

## REACTION / 通知設計

### コメント起動時

| 状態   | リアクション                          | コメント                                                    |
| ------ | ------------------------------------- | ----------------------------------------------------------- |
| 実行中 | 👀 (eyes) を起点コメントに付与        | なし                                                        |
| 成功   | 👀 を外す                             | コマンド別の結果通知に従う                                  |
| 失敗   | 👀 を外し、😕 (confused) を付与       | なし                                                        |

### workflow_dispatch 起動時

- GitHub 上には **何もしない**（リアクションもコメントもなし）
- Actions Run と artifact だけを見る運用

### 実装

- リアクション操作は `gh api` で実行
- 許可されていないユーザーのコメントには何も付けない

---

## DRY-RUN

### 定義

- AI の思考・計画・差分生成・ローカル `git commit` までは行う
- GitHub への外部反映は一切しない:
  - push しない
  - PR 作成しない
  - Issue 作成しない
  - コメント投稿しない
  - リアクション操作しない
  - ラベル操作しない
- 結果は **暗号化 artifact にのみ保存**（GitHub 上には何も返さない）

### 指定方法

- コメント起動: `@vv-ai <command> --dry-run ...`
- workflow_dispatch: `dry_run: true`
- ローカル CLI: `--dry-run`

---

## 実行環境

- **GitHub-hosted runner**: `ubuntu-latest`
- **外向き通信**: OK（依存取得等に必要）
- **ローカルサーバ起動**: OK（開発用サーバの起動やポート使用を許可）

---

## 実装方針

### アーキテクチャ

- **Python アプリ + 薄い GitHub Actions workflow**
- workflow は薄いオーケストレーター:
  - event / inputs / secrets を受ける
  - 必要なセットアップだけする
  - Python CLI (`vv-ai`) を呼ぶ
- 実処理は Python に集約:
  - コマンドパース / 入力検証
  - GitHub 対象解決
  - provider 選択
  - session 復元 / 保存
  - metrics / report 生成
  - `gh` 呼び出し
  - provider 実行ラッパー
- **shell ファイルは作らない**（workflow 内の最小限の `run:` だけ）
- 補助処理も可能な限り Python

### 技術スタック

- **Python** + **uv**（パッケージ管理）
- **Pydantic**（入力検証・JSON スキーマ）
- **age**（暗号化）
- **gh** コマンド（GitHub API 操作）
- Codex CLI / Claude Code CLI

### デプロイ方式

- **MVP: 各リポジトリにソースをコピー**（vendor）
- 将来: Reusable Workflow として切り出し可能な構造を考慮

### 入力の正規化

- workflow / local の差は `RawInput` の作り方だけ
- その後は同じ `ResolvedCommand` に正規化して共通処理へ流す

---

## CONSTRAINTS

- **Budget**: 外部サービスは使わない。GitHub Actions（Public repo 無料枠）+ ローカル環境のみ
- **Timeline**: プロトタイプ優先
- **Team**: Hiroshiba 個人
- **Technical**:
  - Public repo 前提（fork PR のセキュリティ制約あり）
  - GitHub-hosted runner（ubuntu-latest）
  - Codex / Claude Code CLI のセッション継続機能に依存
  - `pull_request_target` は使わない（セキュリティリスク）
  - ローカル DB 等は不使用（ファイルベース管理）

---

## SUCCESS METRICS

- プロトタイプとして 1 リポジトリで安定動作すること
- Codex / Claude Code の両方で基本フロー（confirm → requirements → arch → detail → breakdown または implement → review → address）が回ること
- セッション継続が機能し、文脈を引き継いだ作業ができること
- fork PR でも安全に動作すること（API キー漏洩なし）
- per-run の metrics / report が確実に保存されること

---

## POST-LAUNCH

- Reusable Workflow への切り出し
- allowed_users の拡張（Team / 権限ベース）
- 横断的な metrics 集計・分析
- Report から恒久ルールへの昇格パイプライン（diary → reflect → CLAUDE.md / AGENTS.md）
- ローカルデバッグ補助の拡張

---

## OUT OF SCOPE（MVP 外）

- Issue 作成時の自動反応（`issues.opened`）
- GitHub Enterprise Server 対応
- 横断集計 DB / ダッシュボード
- Report から skill / rule への自動昇格
- egress 制御（外向き通信のネットワーク制限）
- GitHub App ベースの権限設計
- 複数リポジトリ横断の同時実行管理

---

## OPEN QUESTIONS

- `vv-ai.yml` に将来追加する設定項目の洗い出し（session デフォルト、provider 別設定、セキュリティポリシー等）
- ローカルデバッグ用 Issue / PR の操作 CLI（作成・一覧・状態変更等）の詳細設計
- `--instruction` の必須/任意の境界の詳細仕様
- `--event-file` と直接引数が両方ある場合の優先順位
- `target_url` と `target_type`/`target_number` が矛盾する場合の扱い
- Codex / Claude Code の OTel 設定の詳細
- ローカル実行時の暗号化要否（ローカル artifact も暗号化するか）
- metrics.md で提示されたスキーマの最終確定
- age の鍵ペア生成・管理の手順

---

## NOTES & INSIGHTS

- 「vv」は VOICEVOX のプレフィックス
- プロンプトインジェクション対策は「実行を縛る」ではなく「シークレットと権限を構造的に隔離する」方針
- Claude Code の `permissions.deny` は回避可能なので、構造的な分離（apiKeyHelper + sandbox + denyRead）が必要
- Codex の `shell_environment_policy` は子プロセスへの env 伝播を制御できるが、親プロセスからの完全隔離ではない
- セッション継続のため runner 上で復号が必要 → Secrets に秘密鍵も置くが、ステップレベルで用途を限定
- GitHub Artifacts は Public repo で read 権限があるユーザーがダウンロード可能 → 暗号化は必須
- Artifact の削除権限は持たず、保持期間（90日）で自然に GC される運用
- 競合対策として `concurrency` で同一 Issue/PR の実行を直列化
