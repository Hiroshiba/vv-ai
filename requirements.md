# PROJECT REQUIREMENTS — vv-ai

## SOFTWARE TYPE

GitHub Actions ワークフロー + Python CLI ツール（Codex CLI / Claude Code CLI を活用した AI 自動化基盤）

## PROJECT OVERVIEW

GitHub の Issue / PR に対してコメントやワークフローディスパッチで AI（Codex CLI / Claude Code CLI）を起動し、計画・実装・レビュー・Issue 作成などのタスクを自動実行する仕組み。ローカル実行にも対応し、GitHub に依存しないローカル Issue / PR 管理もサポートする。各リポジトリにソースをコピーして導入するプロトタイプとして開始し、将来的に Reusable Workflow への切り出しを見据える。

---

## COMMAND SYSTEM

### プレフィックス

- コメント起動: `@vv-ai`（`@` 付き、本文先頭に記述）

### サブコマンド一覧

| コマンド    | 説明                                                         | Issue 上 | PR 上 |
| ----------- | ------------------------------------------------------------ | -------- | ----- |
| （省略時）  | **reply**（デフォルト）。指示に対してコメントで返答するだけ   | ✅        | ✅     |
| `plan`      | 計画（タスク分解・方針）をコメントで返す                     | ✅        | ✅     |
| `implement` | 実装して PR 作成、または既存 PR に追コミット                 | ✅        | ✅     |
| `review`    | PR をレビューし、指摘・改善提案をコメント                    | —        | ✅     |
| `issue`     | 自然言語指示から Issue を作成                                | ✅        | ✅     |

### オプション

| オプション                          | 説明                                                   | デフォルト              |
| ----------------------------------- | ------------------------------------------------------ | ----------------------- |
| `--provider codex\|claude`          | 使用する AI プロバイダを上書き指定                      | 優先順に従い自動選択    |
| `--session inherit\|compact\|new`   | セッション継続方式                                     | `inherit`               |
| `--dry-run`                         | GitHub への外部反映を一切行わない（artifact のみ保存） | `false`                 |
| `--repo org/repo`                   | Issue 作成先リポジトリ（`issue` コマンド専用）         | workflow が置かれた repo |

### コマンド例

```
@vv-ai 調べて要点だけ返して
@vv-ai plan 実装方針を3案ください
@vv-ai implement --provider codex このIssueを実装して
@vv-ai review --session inherit このPRをレビューして
@vv-ai issue --repo org/repo この不具合をIssue化して
@vv-ai implement --dry-run この修正を試してみて
```

---

## 起動経路

### 1. GitHub コメント起動（`issue_comment`）

- Issue コメントまたは PR コメントで `@vv-ai ...` と書くと起動
- 許可ユーザーのコメントのみ反応。未許可は**完全サイレント**（何も返さない）

### 2. GitHub workflow_dispatch

- 手元 PC から `gh workflow run ...` で起動
- 入力項目:
  - `command`: reply | plan | implement | review | issue
  - `target_type`: issue | pr（任意）
  - `target_number`: 番号（任意）
  - `target_url`: Issue/PR URL（任意）
  - `instruction`: 自然言語指示（コマンドにより必須/任意）
  - `provider`: codex | claude（任意）
  - `session_mode`: inherit | compact | new（任意）
  - `dry_run`: true/false（任意）
  - `repo`: Issue 作成先 org/repo（`issue` 用、任意）
- `target_type`/`target_number` と `target_url` の両対応（C 方式）
  - `target_url` が優先
- 対象省略時の扱い:
  - `issue` コマンド: 対象不要（repo 未指定なら workflow のある repo に作成）
  - `reply` / `plan` / `implement` / `review`: 対象必須（不足ならエラー終了）
- `instruction` 省略:
  - `reply`: 必須
  - `plan` / `implement` / `review`: 省略可
  - `issue`: 必須
- GitHub 上への可視化: **何もしない**。Actions Run と artifact だけを見る運用
- 認可: `github.actor == "Hiroshiba"` を必須チェック（workflow 実行権限があっても Hiroshiba 以外は即終了）
- 対象 repo:
  - 通常コマンド: workflow が置かれた repo のみ
  - `issue` コマンドのみ: `--repo` で別 repo も可（任意 OK）

### 3. ローカル CLI

- CLI 名: `vv-ai`
- サブコマンドなし（`vv-ai [options]` で直接実行）
- workflow 側からもこの CLI を呼ぶ
- 入力方法:
  - 直接指定: `--command`, `--instruction`, `--target-url`, `--target-type`, `--target-number`, `--provider`, `--session`, `--dry-run`, `--repo`
  - イベントファイル: `--event-file <json>` で GitHub event payload を読み込み再現実行
  - `--event issue_comment|workflow_dispatch|local`
- ローカルデバッグは主に **dry-run** で実施
- 扱うコマンドは `@vv-ai` と同じ（専用デバッグコマンドは設けない）

### ラベル起動

- **廃止**。プロンプト指定やオプション指定がしづらく、コメント起動と二重化するため

---

## ワークフロー設計

### イベントトリガー

| イベント             | 有効/無効 |
| -------------------- | --------- |
| `issue_comment`      | ✅ 有効    |
| `workflow_dispatch`   | ✅ 有効    |
| `issues.labeled`     | ❌ 無効    |
| `pull_request.labeled` | ❌ 無効  |
| `issues.opened`      | ❌ 無効    |

### ワークフロー構成

- **1 本の workflow** に `issue_comment` と `workflow_dispatch` を同居
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

- GitHub 非依存で動作可能
- ローカル Issue / PR をファイルベースで管理
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

- **同一 Issue/PR 内** で `reply` / `plan` / `implement` は **同じセッション**（main lane）を共有
- `review` は **別セッション**（review lane）
- セッションキー: `<backend> / <target> / <provider> / <lane>`
  - 例: `github / org/repo#123 / codex / main`
  - 例: `local / issue:login-403-7k2p9a / codex / main`

### セッション継続方式（`--session`）

| 値        | 説明                                           |
| --------- | ---------------------------------------------- |
| `inherit` | 前回セッションをそのまま継続（**デフォルト**） |
| `compact` | コンパクト化して継続                           |
| `new`     | 新規セッション                                 |

### Issue → PR 作成時のセッション引き継ぎ

- Issue の main セッションを **フォーク（複製）** して PR の main セッションとする
- `--session compact` 指定時: フォーク前に compact をかけた状態を複製
- `--session new` 指定時: PR 側は複製せず新規
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

- `--session inherit` / `--session compact` で artifact が見つからない場合: **エラー終了**
- `--session new` の場合: 探さず新規開始

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
- PR タイトル / コミットメッセージ: ユーザー側で用意するプロンプトに委譲（MVP では規約固定しない）
- 複数回実行しても毎回新規（既存 PR への自動更新はしない。既存 PR で作業させたい場合はコメントで追加指示）

### PR 起点

- 既存 PR のブランチで作業（追コミットで更新）

### Runner 側の前処理

- `git checkout` とブランチ切り替えを先に完了
- そのブランチ上にいることを AI に定型プロンプトで伝える

### プロンプト

- PRタイトル / コミットメッセージ / 整形等のプロンプトはユーザー側で別途用意
- MVP 側は「外部から差し替え可能」なフックだけ用意

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
- タイトル / 本文 / ラベル / assignee 等の内容決定はユーザーが用意するプロンプトに委譲

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
- コメント起動（`issue_comment`）で base repo 側のトリガーとして処理するのが基本

---

## AI に渡すコンテキスト

### コメント起動時

- **渡すもの:**
  - org / repo / Issue(or PR) 番号
  - 実行タスクの定型文（「plan を実行してください」等）
  - `@vv-ai ...` コメント本文（そのまま）
  - 補助として: 過去の `@vv-ai` で始まるコメントのみ
- **渡さないもの:**
  - Issue/PR のタイトル・本文・全コメント（A/B/C は渡さない）
  - AI が必要なら `gh` 等で自分で読みに行く

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

### GitHub Actions ステップ分離

- **Secrets ありステップ**: 秘密ファイル（0400）と apiKeyHelper スクリプトを作成するだけ
- **CLI 実行ステップ**: Secrets を env に入れずに Codex / Claude Code を起動
  - 子プロセスにも env 経由で渡らない

### GitHub 書き込み権限の分離

- AI プロセスには **GITHUB_TOKEN を渡さない**
- `git commit` / `git push` / PR 作成 / Issue 作成 / コメント投稿は **非 AI ラッパー**（ワークフローステップ）が実施

---

## REACTION / 通知設計

### コメント起動時

| 状態   | リアクション                          | コメント                                                    |
| ------ | ------------------------------------- | ----------------------------------------------------------- |
| 実行中 | 👀 (eyes) を起点コメントに付与        | なし                                                        |
| 成功   | 👀 を外す                             | なし（`issue` コマンドのみ Issue リンクを 1 行返信）        |
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
- Codex / Claude Code の両方で基本フロー（plan → implement → review）が回ること
- セッション継続が機能し、文脈を引き継いだ作業ができること
- fork PR でも安全に動作すること（API キー漏洩なし）
- per-run の metrics / report が確実に保存されること

---

## POST-LAUNCH

- Reusable Workflow への切り出し
- allowed_users の拡張（Team / 権限ベース）
- 横断的な metrics 集計・分析
- Report から恒久ルールへの昇格パイプライン（diary → reflect → CLAUDE.md / AGENTS.md）
- Local backend の拡張

---

## OUT OF SCOPE（MVP 外）

- ラベル起動
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
- ローカル Issue / PR の操作 CLI（作成・一覧・状態変更等）の詳細設計
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