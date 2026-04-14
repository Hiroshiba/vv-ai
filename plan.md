# vv-ai Plan

## 運用ルール

1. `requirements.md`を読む
2. 過去の日誌を読む
3. `plan.md` を読む
4. 未チェックタスクから次の作業対象を選ぶ
5. そのタスク専用の短い Plan を考える。以降のタスクを必ず順番通り Plan に含める。
6. タスクを実行する
7. `plan.md` の該当タスクにチェックを入れる
8. ユーザーにレビューを依頼する（ということをPlan内に含める）
9. レビューで問題があれば`5.`に戻り、再度レビューを依頼する
10. レビューでOKが出たら次へ進む
11. dateコマンドを実行して日時を取得し、`diary/YYYYMMDD-HHMMSS.md` に日誌を作成する
12. 追加したファイルを全てgit add && git commitする
13. 終了する

## 日誌

日誌には以下の内容を盛り込んで書く。

- 受けたレビュー
- レビューを反映した場合になぜそれを見逃してしまっていた理由の考察
- セッション中にあったユーザーからの指摘・的確な指示
- その指摘を見逃してしまっていた理由・指示内容を自力で思いつけなかった理由の考察
- その他、手こずったこと

## タスクリスト

最終的に要件を満たすなら、途中で一時的な要件違反が残っていても良いです。
その場合はTODOコメントを書いてください。

### 1. プロジェクト基盤

- [x] `uv` ベースで Python プロジェクトを初期化する
- [x] `vv-ai` CLI のエントリポイントを作る

### 2. 設定と入力正規化

- [x] `vv-ai.yml` の設定モデルを定義する
- [x] CLI / event payload を受ける入力モデルを定義する
- [x] `RawInput` から `ResolvedCommand` への正規化を実装する
- [x] `allowed_users` と provider 優先順位の解決を実装する

### 3. Target / Backend 解決

- [x] GitHub URL を target として解決できるようにする
- [x] ローカルパスを target として解決できるようにする
- [x] Issue / PR / local target の共通表現を定義する
- [x] target 不足時と入力不正時のエラー処理を実装する

### 4. ローカルデータ構造

- [x] `.vv-ai/issues` と `.vv-ai/prs` の管理構造を実装する
- [x] `meta.json` の最小スキーマを実装する
- [x] ローカル comments 保存形式を実装する
- [x] workflow_id の生成ルールを実装する

### 5. Provider / Session

- [x] provider 抽象を定義する
- [x] `codex` / `claude` の選択ロジックを実装する
- [x] session key と lane の設計をコードに落とす
- [x] session の保存対象と復元対象を実装する
- [x] `inherit` / `compact` / `new` の振る舞いを実装する

### 5.5. コードリファクタリング

- [x] `refactoring.md` の検討はここで打ち切る。以後はこの項目を再開せず、後続タスクへ進む
- [x] コードリファクタリングの実行は行わないと判断した。以後はこの節を無視して 6 以降へ進む

### 6. Artifact / Metrics / Report

- [x] session artifact の保存形式を実装する
- [x] metrics artifact の保存形式を実装する
- [x] report artifact の保存形式を実装する
- [x] `age` による暗号化 / 復号処理を実装する
- [x] report の Markdown テンプレートを実装する
- [x] success / failure / cancel で必ず保存する処理を実装する

### 7. GitHub 連携

- [x] `gh` ベースの GitHub 操作ラッパーを実装する
- [x] Issue / PR / コメント取得を実装する
- [x] comment reaction の付与 / 解除を実装する
- [x] artifact の検索と復元を実装する
- [x] Issue / PR / コメント作成を実装する

### 8. Provider 実行

- [x] Codex CLI 実行ラッパーを実装する
- [x] Claude Code CLI 実行ラッパーを実装する
- [x] provider ごとの metrics 収集を実装する
- [x] provider 実行時のセキュリティ前提をコードに落とす

### 9. コマンド実装

- [x] `reply` コマンドを実装する
- [x] `plan` コマンドを実装する
- [x] `review` コマンドを実装する
- [x] `implement` の Issue 起点フローを実装する
- [x] `implement` の PR 起点フローを実装する
- [x] fork PR での patch fallback と案内制御を実装する
- [x] `issue` コマンドを実装する

### 10. Workflow / Runner

- [x] `issue_comment` と `workflow_dispatch` を持つ単一 workflow を実装する
- [x] `workflow_dispatch` の inputs を requirements に沿って定義する
- [x] `concurrency` による同一 target の直列化を実装する
- [x] runner セットアップ手順を実装する
- [x] secret を分離した workflow step 構成を実装する

### 11. Dry-run / Security

- [x] dry-run で GitHub への外部反映を止める制御を実装する
- [x] AI プロセスに GitHub token を渡さない構成を実装する
- [x] Codex の環境変数伝播制御を実装する
- [x] Claude Code の sandbox / denyRead / apiKeyHelper 前提を実装する

### 12. テストと検証

- [x] 入力正規化と validation の単体テストを実装する
- [x] target 解決と backend 判定の単体テストを実装する
- [x] session / artifact 保存復元のテストを実装する
- [x] dry-run と finally-save 保証のテストを実装する
- [x] local 実行の統合テストを実装する
- [x] 主要シナリオの受け入れ確認を行う

### 13. ドキュメント

- [x] セットアップ手順をまとめる
- [x] Secrets / `vv-ai.yml` / ローカル実行方法をまとめる
- [x] GitHub Actions からの実行方法をまとめる
- [x] 今後 Reusable Workflow に切り出す前提の整理を残す

### 14. テスト準備

各テスト Phase 開始前に残留 tmux セッションを確認・削除してから始める。

```sh
tmux kill-session -t vvai-test 2>/dev/null || true
```

**セットアップ**

- [x] `vv-ai.yml` を作成する（.gitignore 対象のためコミット不要）
  ```yaml
  allowed_users:
    - Hiroshiba
  provider_priority:
    - codex
    - claude
  ```
- [x] ローカル Issue フィクスチャを作成する
  - `.vv-ai/issues/test-issue-1/comments/`（空ディレクトリ）
  - `.vv-ai/issues/test-issue-1/issue.md`

    ```
    # テスト Issue

    vv-ai の動作確認用 Issue です。ローカル backend のテストに使います。
    ```

  - `.vv-ai/issues/test-issue-1/meta.json`
    ```json
    {
      "id": "test-issue-1",
      "kind": "issue",
      "status": "open",
      "created_at": "2026-03-31T00:00:00Z",
      "updated_at": "2026-03-31T00:00:00Z",
      "backend": "local"
    }
    ```

- [x] ローカル PR フィクスチャを作成する
  - `.vv-ai/prs/test-pr-1/comments/`（空ディレクトリ）
  - `.vv-ai/prs/test-pr-1/pr.md`

    ```
    # テスト PR

    vv-ai の動作確認用 PR です。review コマンドのテストに使います。
    ```

  - `.vv-ai/prs/test-pr-1/meta.json`
    ```json
    {
      "id": "test-pr-1",
      "kind": "pr",
      "status": "open",
      "created_at": "2026-03-31T00:00:00Z",
      "updated_at": "2026-03-31T00:00:00Z",
      "backend": "local",
      "head_branch": "feature-test",
      "base_branch": "main"
    }
    ```

- [x] age 鍵ペアを生成する
  ```sh
  age-keygen -o /tmp/vv-ai-age-key.txt
  # public key は出力のコメント行から取得
  ```
- [x] 外部ツールが PATH にあることを確認する（codex, claude, age, tmux, gh）
- [x] 以下の環境変数を設定する（tmux セッション内で実行）
  - `VV_OPENAI_API_KEY` または `VV_OPENAI_API_KEY_FILE`（`--skip-api-key-check` を使うため不要）
  - `VV_ANTHROPIC_API_KEY` または `VV_ANTHROPIC_API_KEY_FILE`（`--skip-api-key-check` を使うため不要）
  - `VV_AI_AGE_PUBLIC_KEY`（age-keygen の public key）
  - `VV_AI_AGE_SECRET_KEY`（age-keygen の secret key）
- [x] `uv run vv-ai --help` が exit 0 で返ることを確認する

### 15. Codex Provider テスト

tmux セッション作成後、環境変数をセットしてから実行する。

```sh
tmux kill-session -t vvai-test 2>/dev/null || true
tmux new-session -d -s vvai-test -x 200 -y 50 -c /Users/kazuyuki_hiroshiba/Github/vv-ai
```

各テストは `; echo "EXIT=$?"` を末尾に付けて exit code を確認する。エラーが発生した場合はそれ以降のテストを中断し、日誌にエラー内容と原因を記録する。

**エラーケース（provider 実行なし、即座に終了）**

- [x] C-01: 不正コマンド名 → exit 2
  ```sh
  uv run vv-ai --command invalid
  ```
- [x] C-02: reply で instruction なし → exit 2
  ```sh
  uv run vv-ai --command reply --target-url .vv-ai/issues/test-issue-1 --provider codex --session_mode new
  ```
- [x] C-03: review で Issue を指定 → exit 1（`review` は PR 専用）
  ```sh
  uv run vv-ai --command review --target-url .vv-ai/issues/test-issue-1 --provider codex --session_mode new --dry-run --skip-api-key-check
  ```

**正常ケース（dry-run）**

- [x] C-10: reply ローカル Issue → exit 0、応答テキスト出力、`.vv-ai/artifacts/` に保存確認
  ```sh
  uv run vv-ai --command reply --target-url .vv-ai/issues/test-issue-1 \
    --instruction "この Issue の内容を一行で要約して" \
    --provider codex --session_mode new --dry-run --skip-api-key-check
  ```
- [x] C-20: plan ローカル Issue → exit 0
  ```sh
  uv run vv-ai --command plan --target-url .vv-ai/issues/test-issue-1 \
    --instruction "実装方針を出して" \
    --provider codex --session_mode new --dry-run --skip-api-key-check
  ```
- [x] C-30: implement ローカル Issue → exit 0、`[dry-run/local]` 出力、`vv-ai/issue-` ブランチ作成確認→削除
  ```sh
  uv run vv-ai --command implement --target-url .vv-ai/issues/test-issue-1 \
    --provider codex --session_mode new --dry-run --skip-api-key-check
  # 確認後: git checkout main && git branch | grep 'vv-ai/issue-' | xargs git branch -D
  ```
- [x] C-40: reply GitHub Issue → exit 0、GitHub への書き込みなし（dry-run）
  ```sh
  uv run vv-ai --command reply \
    --target-url https://github.com/VOICEVOX/voicevox_core/issues/1 \
    --instruction "テスト" \
    --provider codex --session_mode new --dry-run --skip-api-key-check
  ```

### 16. Claude Provider テスト

tmux セッションを再作成する（Phase 15 のセッションを破棄）。エラーが発生した場合はそれ以降のテストを中断し、日誌にエラー内容と原因を記録する。

**正常ケース（dry-run）**

- [x] D-10: reply ローカル Issue → exit 0、stdout に `provider=claude` を含む
  ```sh
  uv run vv-ai --command reply --target-url .vv-ai/issues/test-issue-1 \
    --instruction "この Issue の内容を一行で要約して" \
    --provider claude --session_mode new --dry-run --skip-api-key-check
  ```
- [x] D-20: plan ローカル Issue → exit 0
  ```sh
  uv run vv-ai --command plan --target-url .vv-ai/issues/test-issue-1 \
    --instruction "実装方針を出して" \
    --provider claude --session_mode new --dry-run --skip-api-key-check
  ```
- [x] D-30: implement ローカル Issue → exit 0、`[dry-run/local]` 出力、ブランチ確認→削除
  ```sh
  uv run vv-ai --command implement --target-url .vv-ai/issues/test-issue-1 \
    --provider claude --session_mode new --dry-run --skip-api-key-check
  ```
- [x] D-40: reply GitHub Issue → exit 0
  ```sh
  uv run vv-ai --command reply \
    --target-url https://github.com/VOICEVOX/voicevox_core/issues/1 \
    --instruction "テスト" \
    --provider claude --session_mode new --dry-run --skip-api-key-check
  ```

### 17. Provider 自動選択テスト

- [x] A-01: `--provider` 省略時に `provider_priority` の先頭（codex）が選択される
  ```sh
  uv run vv-ai --command reply --target-url .vv-ai/issues/test-issue-1 \
    --instruction "テスト" --session_mode new --dry-run --skip-api-key-check
  # stdout に provider=codex を含むことを確認
  ```

### 18. テスト後処理

- [x] `vv-ai.yml` を削除する
- [x] `.vv-ai/` を削除する
- [x] age 鍵ファイルを削除する（`/tmp/vv-ai-age-key.txt`）
- [x] tmux セッションを削除する（`tmux kill-session -t vvai-test`）
- [x] テスト中に作成されたブランチを削除する

### 19. GitHub Actions テスト準備

- [x] `.gitignore` から `/vv-ai.yml` を削除し、`vv-ai.yml` をコミットする
- [x] ワークフローに AI CLI インストールステップを追加する
- [x] push する
- [x] テスト用 Issue と PR を作成する（Issue #1、PR #2）

Secrets はユーザーが別途設定する。

### 20. Claude Provider GitHub テスト

GitHub Actions 経由で `dry_run=true` でテストする。`gh workflow run` で起動し、`gh run watch` で完了を待ち、conclusion=success を確認する。失敗したら中断して日誌を書く。

テスト用の Issue 番号と PR 番号は、セクション 19 で作成したものを `<ISSUE_NUM>` `<PR_NUM>` に入れる。

- [x] G-D-10: reply GitHub Issue
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=reply \
    -f target_url=https://github.com/Hiroshiba/vv-ai/issues/1 \
    -f instruction="この Issue の内容を一行で要約して" \
    -f provider=claude -f session_mode=new -f dry_run=true
  # gh run list --workflow=vv-ai.yml --repo Hiroshiba/vv-ai -L1 で run ID を取得
  # gh run watch <RUN_ID> --repo Hiroshiba/vv-ai で完了を待つ
  ```
- [x] G-D-20: plan GitHub Issue
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=plan \
    -f target_url=https://github.com/Hiroshiba/vv-ai/issues/1 \
    -f instruction="実装方針を出して" \
    -f provider=claude -f session_mode=new -f dry_run=true
  ```
- [x] G-D-30: review GitHub PR
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=review \
    -f target_url=https://github.com/Hiroshiba/vv-ai/pull/2 \
    -f provider=claude -f session_mode=new -f dry_run=true
  ```
- [x] G-D-40: implement GitHub Issue
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=implement \
    -f target_url=https://github.com/Hiroshiba/vv-ai/issues/1 \
    -f provider=claude -f session_mode=new -f dry_run=true
  ```
- [x] G-D-50: issue コマンド
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=issue \
    -f instruction="README の改善案を Issue にして" \
    -f provider=claude -f session_mode=new -f dry_run=true
  ```

### 21. バグ修正 + 認可共通化

- [x] 認可チェックを `allowed_users` に一本化する
- [x] ワークフローに git config ステップを追加する
- [x] session artifact に provider セッションディレクトリを含める

### 22. dry_run=false テスト（workflow_dispatch）

テスト用の Issue #1 と PR #2 を使用する。`provider=codex`, `session_mode=new` で実行する。各テスト後に作成されたリソースをクリーンアップする。エラーが発生した場合はそれ以降を中断し日誌を書く。

- [x] G-Live-10: reply Issue #1 → success → Issue #1 にコメントが投稿されていること
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=reply \
    -f target_url=https://github.com/Hiroshiba/vv-ai/issues/1 \
    -f instruction="この Issue の内容を一行で要約して" \
    -f provider=codex -f session_mode=new
  ```
- [x] G-Live-15: reply Issue #1 でセッション継続 → success → 前回のコンテキストを引き継いでいること
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=reply \
    -f target_url=https://github.com/Hiroshiba/vv-ai/issues/1 \
    -f instruction="前回の要約を踏まえて、この Issue の目的を一文で言い換えて" \
    -f provider=codex -f session_mode=inherit
  ```
- [x] G-Live-20: plan Issue #1 → success → コメント投稿確認
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=plan \
    -f target_url=https://github.com/Hiroshiba/vv-ai/issues/1 \
    -f instruction="実装方針を出して" \
    -f provider=codex -f session_mode=new
  ```
- [x] G-Live-30: implement Issue #1 → success → ブランチ push + PR 作成を確認 → PR クローズ + ブランチ削除
  - 事前に Issue コメントで具体的な実装指示を書いてから implement を実行する
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=implement \
    -f target_url=https://github.com/Hiroshiba/vv-ai/issues/1 \
    -f provider=codex -f session_mode=new
  ```
- [x] G-Live-40: review PR #2 → success → PR #2 にコメント投稿確認
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=review \
    -f target_url=https://github.com/Hiroshiba/vv-ai/pull/2 \
    -f provider=codex -f session_mode=new
  ```
- [x] G-Live-50: issue → success → 新 Issue が作成されること → クローズ
  ```sh
  gh workflow run vv-ai.yml --repo Hiroshiba/vv-ai \
    -f command=issue \
    -f instruction="README の改善案を Issue にして" \
    -f provider=codex -f session_mode=new
  ```

### 23. dry_run=false テスト（issue_comment）

実際に Issue/PR へコメントを書いてワークフローをトリガーする。テスト用の Issue #1 と PR #2 を使用。provider は未指定（`vv-ai.yml` の `provider_priority` 先頭の codex が自動選択される）。エラーが発生した場合はそれ以降を中断し日誌を書く。

**Issue コメント起動:**

- [x] E-10: Issue #1 に `@vv-ai --session_mode new この Issue の内容を一行で要約して` → reply コメント投稿確認
- [x] E-15: Issue #1 に `@vv-ai 前回の要約を少しだけ解説して` → inheritでコメント投稿確認
- [x] E-20: Issue #1 に `@vv-ai plan 実装方針を出して` → コメント投稿確認
- [x] E-30: Issue #1 に `@vv-ai implement` → ブランチ push + PR 作成 → 確認後削除
- [x] E-40: Issue #1 に `@vv-ai issue この Issue をもう少し詳しく書き直して` → 新 Issue 作成 + リンクコメント → 新 Issue クローズ

**PR コメント起動:**

- [x] E-50: PR #2 に `@vv-ai --session_mode new この PR の内容を一行で要約して` → reply コメント投稿確認
- [x] E-70: PR #2 に `@vv-ai implement この PR に改善を追加して` → head ブランチに追コミット push
- [x] E-60: PR #2 に `@vv-ai review` → レビューコメント投稿確認

### 24. Claude Provider GitHub テスト

Codex テスト全通過後、余力があれば実施する。コマンドはセクション 22 と同じで `provider=claude` に変更する。

- [x] G-CL-10: reply GitHub Issue
- [x] G-CL-20: plan GitHub Issue
- [x] G-CL-30: review GitHub PR

### extra. 追加タスク

- [x] https://github.com/Hiroshiba/vv-ai/issues/8
- [ ] Github WorkflowでのClaudeをlocalhost proxy経由にして完全に秘匿する
