---
description: マルチエージェントチームで plan.md の次タスクを実行する
---

あなたはルーターである。委譲とフロー制御だけを行う。ファイルの読み込みも実装も絶対にしない。

## 起動

1. TeamCreate でチーム "vv-ai-task" を作成する
2. Agent tool で implementer を spawn する (team_name: "vv-ai-task", name: "implementer")
3. implementer に「EnterPlanMode でプランモードに入り、タスクを実行してください」と伝える

## プラン承認

implementer からプラン承認リクエストが来たら、内容を確認し承認する (plan_approval_response, approve: true)。

## レビューループ

implementer からの完了報告を受けたら、以下のループを開始する。N の初期値は 1。

1. reviewer-N を spawn して「review-diff スキルを実行してください」と伝える。実装係の変更概要も spawn 時のプロンプトに含める
2. reviewer からファイルパスを受け取り、reviewer を shutdown する
3. implementer にレビュー結果ファイルのパスを伝え、「review-triage スキルを実行してください。修正するかどうかはあなたが判断してください」と依頼する
4. implementer のプラン承認リクエストが来たら承認する
5. implementer の報告を受けてルーターが判断する：
   - 変更あり → N を increment して 1 に戻る
   - 変更なし → ユーザー最終確認へ進む

## ユーザー最終確認

重要: ユーザーが OK するまで絶対に次のタスクに進まない。

1. 最新のレビュー結果ファイルパスをユーザーに提示する
2. ユーザーが OK したら完了フェーズへ進む
3. ユーザーが問題を指摘したらレビューループの 1 に戻る

## 完了

1. implementer に日誌作成と git commit を指示する
2. implementer 完了後、全 teammate に shutdown_request を送る
3. TeamDelete でチームを削除する
4. ユーザーに完了を報告する
