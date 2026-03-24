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
9. レビューで問題があれば`5.`に戻る
10. `diary/YYYYMMDD-HHMMSS.md` に日誌を作成する
11. 追加したファイルを全てgit add && git commitする
12. 終了する

## 日誌

日誌には受けたレビューと、レビューを反映した場合になぜそれを見逃してしまったのかの考察と、手こずったことを書く。

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

- [ ] Codex CLI 実行ラッパーを実装する
- [ ] Claude Code CLI 実行ラッパーを実装する
- [ ] provider ごとの metrics 収集を実装する
- [ ] provider 実行時のセキュリティ前提をコードに落とす

### 9. コマンド実装

- [ ] `reply` コマンドを実装する
- [ ] `plan` コマンドを実装する
- [ ] `review` コマンドを実装する
- [ ] `implement` の Issue 起点フローを実装する
- [ ] `implement` の PR 起点フローを実装する
- [ ] fork PR での patch fallback と案内制御を実装する
- [ ] `issue` コマンドを実装する

### 10. Workflow / Runner

- [ ] `issue_comment` と `workflow_dispatch` を持つ単一 workflow を実装する
- [ ] `workflow_dispatch` の inputs を requirements に沿って定義する
- [ ] `concurrency` による同一 target の直列化を実装する
- [ ] runner セットアップ手順を実装する
- [ ] secret を分離した workflow step 構成を実装する

### 11. Dry-run / Security

- [ ] dry-run で GitHub への外部反映を止める制御を実装する
- [ ] AI プロセスに GitHub token を渡さない構成を実装する
- [ ] Codex の環境変数伝播制御を実装する
- [ ] Claude Code の sandbox / denyRead / apiKeyHelper 前提を実装する

### 12. テストと検証

- [ ] 入力正規化と validation の単体テストを実装する
- [ ] target 解決と backend 判定の単体テストを実装する
- [ ] session / artifact 保存復元のテストを実装する
- [ ] dry-run と finally-save 保証のテストを実装する
- [ ] local 実行の統合テストを実装する
- [ ] 主要シナリオの受け入れ確認を行う

### 13. ドキュメント

- [ ] セットアップ手順をまとめる
- [ ] Secrets / `vv-ai.yml` / ローカル実行方法をまとめる
- [ ] GitHub Actions からの実行方法をまとめる
- [ ] 今後 Reusable Workflow に切り出す前提の整理を残す
