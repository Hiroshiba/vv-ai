# onetask 要件定義

## 概要

Claude Code の Agent Teams 機能を使わず、Python + tmux + 複数の claude CLI プロセスで team-task.md のフローを再現するツール。

## 主目的

Agent Teams の TeamCreate / SendMessage / TaskCreate 等のツールに依存せず、Python がteam-lead役を担い implementer と reviewer を claude -p プロセスとして tmux 内で起動・協調させる。

## 実行方法

`uv run -m onetask` でターミナルから起動する。

## 役割分担

- team-lead: Python スクリプト。フロー制御、tmux 管理、結果判定を行う
- implementer: claude -p プロセス。plan.md の次タスクを実行する。セッションを --resume で維持する
- reviewer: claude -p プロセス。review-diff スキルで差分レビューを行う。毎回新規セッション

## フロー

1. implementer が plan モードでタスクのプランを作成する
2. implementer が acceptEdits モードで実装する
3. レビューループ: reviewer が review-diff を実行し、implementer が review-triage で対応する
4. changes_made == false になるか上限回数に達するまでループする
5. ユーザーが確認し、問題があればフィードバックを入力 → implementer が修正 → 再レビューのループ
6. implementer が日誌作成と git commit を行う

## 技術要件

- claude CLI の --output-format stream-json と --json-schema で structured output を取得する
- tmux セッション内で claude を直接実行し、ユーザーがリアルタイムに監視できるようにする
- スクリプトファイルを生成して tmux send-keys で実行し、シェルエスケープ問題を回避する
- 結果はファイル経由で Python に返す
- implementer のセッションは --resume で継続する
- plan フェーズは --permission-mode plan、実装以降は --permission-mode acceptEdits を使用する
- --settings で sandbox 設定を注入し、autoAllowBashIfSandboxed: true でサンドボックス内の Bash コマンドを自動許可する
- implementer.md と reviewer.md の指示内容をプロンプトに埋め込む

## 実装先

src/onetask/ ディレクトリに Python パッケージとして配置する。
