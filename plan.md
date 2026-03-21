# vv-ai Plan

## 運用ルール

1. `plan.md` を読む
2. 先頭の未チェックタスクを次の作業対象にする
3. そのタスク専用の短い Plan を考える
4. タスクを実行する
5. ユーザーにレビューを依頼する
6. レビューで問題があれば`3.`に戻る
7. 完了したら `plan.md` の該当タスクにチェックを入れる
8. `reports/YYYYMMDD-HHMMSS.md` にレポートを作成する
9. 終了する

## タスクリスト

### 1. プロジェクト基盤

- [x] `uv` ベースで Python プロジェクトを初期化する
- [ ] `vv-ai` CLI のエントリポイントを作る
- [ ] 開発用依存関係とテスト基盤を整える

### 2. 設定と入力正規化

- [ ] `vv-ai.yml` の設定モデルを定義する
- [ ] CLI / event payload を受ける入力モデルを定義する
- [ ] `RawInput` から `ResolvedCommand` への正規化を実装する
- [ ] `allowed_users` と provider 優先順位の解決を実装する

### 3. Target / Backend 解決

- [ ] GitHub URL を target として解決できるようにする
- [ ] ローカルパスを target として解決できるようにする
- [ ] Issue / PR / local target の共通表現を定義する
- [ ] target 不足時と入力不正時のエラー処理を実装する

### 4. ローカルデータ構造

- [ ] `.vv-ai/issues` と `.vv-ai/prs` の管理構造を実装する
- [ ] `meta.json` の最小スキーマを実装する
- [ ] ローカル comments 保存形式を実装する
- [ ] workflow_id の生成ルールを実装する

### 5. Provider / Session

- [ ] provider 抽象を定義する
- [ ] `codex` / `claude` の選択ロジックを実装する
- [ ] session key と lane の設計をコードに落とす
- [ ] session の保存対象と復元対象を実装する
- [ ] `inherit` / `compact` / `new` の振る舞いを実装する

### 6. Artifact / Metrics / Report

- [ ] session artifact の保存形式を実装する
- [ ] metrics artifact の保存形式を実装する
- [ ] report artifact の保存形式を実装する
- [ ] `age` による暗号化 / 復号処理を実装する
- [ ] report の Markdown テンプレートを実装する
- [ ] success / failure / cancel で必ず保存する処理を実装する

### 7. GitHub 連携

- [ ] `gh` ベースの GitHub 操作ラッパーを実装する
- [ ] Issue / PR / コメント取得を実装する
- [ ] comment reaction の付与 / 解除を実装する
- [ ] artifact の検索と復元を実装する
- [ ] Issue / PR / コメント作成を実装する

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
