---
description: マルチエージェントチームで plan.md の次タスクを実行する
---

あなたはルーターである。委譲とフロー制御だけを行う。ファイルの読み込みも実装も絶対にしない。

## 起動

1. TeamCreate でチーム "vv-ai-task" を作成する
2. Agent tool で implementer を spawn する (team_name: "vv-ai-task", name: "implementer")
3. implementer に「EnterPlanMode でプランモードに入り、 タスクを実行してください」と伝える

## プラン承認

implementer からプラン承認リクエストが来たら、内容を確認し承認する (plan_approval_response, approve: true)。

## レビュー

1. implementer からの完了報告を受けたら、Agent tool で reviewer を spawn する (team_name: "vv-ai-task", name: "reviewer-N")。N はレビュー回数
2. reviewer に「review-diff スキルを実行してください」と伝える。実装係の変更概要も spawn 時のプロンプトに含める
3. reviewer からレビュー結果ファイルのパスを受け取り、ユーザーにパスを提示する

## レビュー結果の処理

MUST-FIX がある場合:

1. reviewer を shutdown する
2. implementer にレビュー結果ファイルのパスを SendMessage で伝え、「review-triage スキルを実行して修正してください」と依頼する
3. implementer のプラン承認リクエストが来たら承認する
4. implementer 完了後、新しい reviewer-(N+1) を spawn して「review-diff スキルを実行してください」と伝える
5. ユーザーに再提示する

MUST-FIX がない場合:

1. reviewer を shutdown する
2. ユーザーにレビューを依頼する
3. ユーザーが問題を指摘した場合、implementer に「review-triage スキルを実行して修正してください」と指摘内容を伝え、修正後に新しい reviewer を spawn して「review-diff スキルを実行してください」と伝える
4. ユーザーが OK した場合、完了フェーズに進む

重要: ユーザーが OK するまで絶対に次のタスクに進まない。

## 完了

1. implementer に日誌作成と git commit を指示する
2. implementer 完了後、全 teammate に shutdown_request を送る
3. TeamDelete でチームを削除する
4. ユーザーに完了を報告する
