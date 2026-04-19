# PROJECT REQUIREMENTS - vv-ai Workflow Viewer

## SOFTWARE TYPE
Static Web Application / Internal Tool / Analytics Viewer

単一利用者向けの静的 Web ビューアー。GitHub Actions 上で実行された `vv-ai` workflow の結果を取得し、artifact をブラウザ内で復号・集計・閲覧するための機能優先プロトタイプ。

## PROJECT OVERVIEW
`vv-ai` workflow の run 結果を、GitHub API と browser-only の復号処理だけで可視化する専用ビューアーを新規リポジトリとして作る。主目的は、run ごとの token 消費量、コスト、所要時間、失敗イベント、AI エージェントの進行ログを GitHub Actions 標準 UI より見やすく確認すること。

配布形態は GitHub Pages 相当の静的ホスティングとし、サーバーは置かない。利用者は GitHub PAT と age 秘密鍵をブラウザに入力し、owner / repo を指定して `vv-ai` workflow の run・artifact を直接取得する。初版では public repository を対象とし、1 リポジトリ単位で閲覧する。

## PRODUCT VISION & GOALS
### 何を解決するか
- `vv-ai` の run 結果を GitHub Actions UI だけで追うのは横断確認がしづらく、artifact の復号・比較・失敗分析に手間がかかる。
- token / cost / duration のような統計を run 横断で把握しづらい。
- 特に Codex 側は metrics だけでは十分に流れを追いにくく、session を読んで失敗要因を見たい。

### 成功時の姿
- run 一覧、期間集計、run 詳細、失敗イベント詳細、session timeline が安定して見られる。
- 日常的な確認作業の多くを GitHub Actions UI ではなくこのビューアーで済ませられる。
- 「何にどれだけ token がかかったか」「どこで失敗したか」「AI がどう進んだか」が短時間で追える。

### 中長期の拡張イメージ
- グラフや比較機能の追加
- 複数リポジトリ対応
- Codex / Claude の session 解析強化
- 失敗パターンの自動分類

### 失敗とみなす条件
- token / cost / duration / 失敗イベントの基本情報が見えない。
- session timeline がほとんど出せず、AI の流れを追えない。
- artifact の復号が不安定で、実用に耐えない。
- 静的サイトなのに毎回の読み込みが重すぎて常用できない。

### 代替手段と差別化
既存の代替手段は GitHub Actions UI と手動 artifact ダウンロード + ローカル復号。これらは run 横断集計、artifact 間の一貫表示、失敗イベントの探索、session の時系列確認に弱い。本ビューアーは `vv-ai` 専用として artifact 命名規約と保存形式を前提にし、静的サイトだけで取得・復号・可視化まで完結する点を差別化ポイントとする。

## TARGET USERS
### 主ユーザー
- 利用者は依頼者本人のみ
- 技術的には十分に詳しく、GitHub Actions / artifact / API キー / age の運用に抵抗がない
- 主用途は `vv-ai` 実行結果の確認、失敗分析、消費量監視、デバッグ補助

### 利用環境
- デスクトップブラウザが主
- スマホブラウザでは最低限読める程度を目標とする
- デザインよりも情報量と探索性を優先する

### 重視する価値
- token / cost / duration を横断で見たい
- 失敗したイベントや AI エージェント内部の流れを見たい
- metrics / report / session / metadata を一箇所で見たい
- 一度設定した接続情報や取得済みデータを再利用したい

## CORE FEATURES
### Must Have
- owner / repo の入力 UI
- workflow 名を `vv-ai` 固定で扱う実装
- GitHub PAT と age 秘密鍵の入力・ブラウザ内保存
- GitHub API から workflow run 一覧、run 詳細、artifact 一覧、artifact ダウンロードを実行できる
- age で暗号化された metrics / report / session artifact をブラウザ内で復号できる
- ダッシュボードの固定数値カード表示
  - 総 run 数
  - 総 input token
  - 総 output token
  - 総 cost
  - 平均実行時間
  - 失敗 run 数
  - 失敗イベント数
- run 一覧を新しい順で表示
- 集計単位を日 / 週 / 月で切り替え可能
- 任意期間フィルタ
- run 一覧の基本フィルタ
  - 期間
  - provider
  - command（`reply` / `plan` / `implement` / `review` / `issue`）
  - 失敗イベント有無
  - テキスト検索
- run 詳細ページ / パネルで以下を表示
  - metrics の構造化表示
  - report の Markdown 表示
  - artifact metadata の表示
  - session timeline
  - 失敗イベント一覧
  - 失敗イベント詳細
- 失敗イベント一覧から該当詳細へ移動可能
- session timeline で少なくとも以下を時系列表示
  - ユーザー指示
  - AI 応答
  - ツール実行
  - エラー / 失敗イベント
- AI エージェントが内部で実行したコマンド / ツールを、読める範囲で行単位に表示
- Codex / Claude の両対応
- Codex を初版の優先解析対象とする
- 読み込みや復号を非同期に行い、画面を固めず段階的に表示する
- 取得・復号済みデータのローカルキャッシュ
- 資格情報削除 UI とキャッシュ削除 UI

### Should Have
- run 詳細で git diff / staged diff / git status の簡易表示
- session の raw 表示へのフォールバック
- artifact 生データ閲覧
- session artifact が複数ある run での切り替え表示
- 欠損 metrics を `N/A` として明示表示
- スマホ向けの最低限のレスポンシブ対応
- 失敗イベント詳細から timeline 内の該当位置へジャンプ

### Could Have
- 折れ線グラフや棒グラフによる推移表示
- CSV / JSON エクスポート
- URL によるフィルタ条件共有
- セッション全文検索
- ダークモード
- 失敗パターンの自動分類

### Won't Have
- 専用バックエンドサーバー
- マルチユーザー対応
- 複数リポジトリ横断分析
- private repository を主対象にした要件最適化
- provider 比率の高度な可視化
- 高度に作り込んだブランド / マーケティング UI

## TECHNICAL REQUIREMENTS
### Platform / Delivery
- GitHub Pages 相当の静的ホスティング前提
- 完全クライアントサイド動作
- 新規リポジトリとして実装
- デスクトップ優先、スマホ最低限対応

### Scope
- 対象は `vv-ai` workflow のみ
- 対象は 1 リポジトリ単位
- owner / repo は UI 入力
- workflow 名は固定
- 初版の主対象は public repository

### GitHub Access
- GitHub PAT を使って Actions 系情報へアクセスする
- 必要な権限は最小権限を原則とし、具体的な scope は実装時に最終整理する
- owner / repo 入力により対象を切り替える
- 初期ロードの既定範囲は「直近 7 日」とする
- 追加読込または期間変更で過去データへ広げられるようにする

### Secret Storage Policy
- Cookie は使用しない
- 資格情報はブラウザ内だけに保存する
- 保存先は IndexedDB を第一候補とする
  - 理由: cookie のように送信されず、キャッシュ済み復号データも同じ基盤で扱いやすいため
- localStorage は非機密の軽量設定に限定してよい
- ただし IndexedDB / localStorage ともに XSS には弱いため、信頼できる単一利用者向け静的サイト前提で運用する
- 明示的な削除 UI を必須とする

### Caching Strategy
- 復号済み metrics / report / session 解析結果を IndexedDB に保存できる
- run 一覧や artifact metadata も再利用可能な形でキャッシュしてよい
- キャッシュはページ表示高速化のためのもので、真のデータソースは GitHub API と artifact 本体
- キャッシュ破棄をユーザーが明示的に実行できること

### Artifact Model
コード確認ベースで、viewer が扱う artifact は次の 3 種が中心。

1. metrics artifact
   - 暗号化 JSON
   - 主な構造: `summary`, `usage`, `behavior`, `tools`, `steps`, `provider_specific`
2. report artifact
   - 暗号化 Markdown
   - 主な節: Summary / Changes / Decisions / Validation / Risks / Open Questions / Next Actions / Notes
3. session artifact
   - 暗号化 tar bundle
   - 主な内容: `meta.json`, `git-diff.patch`, `git-staged.patch`, `git-status.txt`, `untracked/`, `provider-session/`

artifact 名は `vv-ai-session__...`, `vv-ai-metrics__...`, `vv-ai-report__...` の接頭辞で識別される前提で扱う。workflow 実装上、run ごとに metrics / report は各 1 件、session は最大 2 件の可能性があるため、viewer は複数 session artifact を扱えるようにする。

### Session Parsing
- session timeline は `provider-session/` を解析して生成する
- 初版は Codex を優先して解析する
- Claude も対応する
- Codex は metrics が比較的薄いため、失敗イベントや流れの把握は session 解析が主になる
- Codex session は実データ依存の部分があるため、解析失敗時は raw 表示へフォールバックする
- 失敗イベント詳細では以下を可能な範囲で表示する
  - イベント種別
  - 関連コマンド名 / ツール名
  - エラー文
  - 入力 / 出力の抜粋
  - タイムスタンプ
  - 前後イベントへの導線
- 「コマンド」は workflow command（`reply` / `plan` など）と、AI エージェント内部のツール / shell / action 実行の両方を区別して表示する

### Metrics / Dashboard
- 指標は広めに出し、欠損値は欠損として扱う
- 特に重視する指標
  - input / output / cached token
  - cost_usd
  - active_time_seconds
  - total_turns / failed_turns / success_rate
  - command_execution_count
  - file_change_count
  - mcp_tool_call_count
  - web_search_count
  - lines_added / lines_removed
- provider 比率の高度な可視化は不要
- 成功率は補助情報であり、主眼は失敗イベントの具体像と流れの把握

### Performance / UX Behavior
- 初期表示ではダッシュボードと run 一覧を優先表示
- 重い session 解析は段階的に進め、解析完了後に詳細を埋める
- 一部 artifact の取得や復号に失敗しても、他の読める情報は表示する
- リロードのたびに全件フル再読込しない
- 新しい run が上に並ぶ
- 失敗 run の強い強調表示は不要。軽い状態表示で足りる

### Error Handling
- GitHub API エラー
- artifact 欠落
- 復号失敗
- session parse 失敗
- metrics 欠損

上記を個別に識別して UI に表示する。1 箇所の失敗で画面全体を壊さない。

### Security
- PAT と age 秘密鍵をブラウザで扱うことを UI 上で明示する
- 復号済みデータがローカル端末に残ることを明示する
- 保存をオフにする将来拡張余地は残すが、初版は保存前提でよい
- クリア操作を分かりやすく提供する

### Data Retention Constraint
- workflow 定義上、artifact の retention-days は 90 日
- そのためビューアーで見られる履歴は GitHub 側に残っている範囲に依存する

## DESIGN REQUIREMENTS
### Visual Style
- 機能優先
- 実務的で読みやすい UI
- 過度な装飾は不要
- 情報密度と探索性を優先

### Layout Direction
- ダッシュボードを主画面とする
- 数値カード + 一覧 + 詳細の 3 層構成を基本とする
- run 一覧と詳細を行き来しやすくする
- グラフは初版必須ではない

### Interaction Principles
- セットアップ後はすぐ一覧が見える
- 詳細では metrics と session timeline を最優先表示
- artifact metadata と report は補助情報として見やすくまとめる
- session raw、git diff、生データはデバッグ補助として折りたたみ表示でよい

### Language
- UI の既定言語は日本語
- 技術用語や一部ラベルは英語併記可

## USER EXPERIENCE FLOWS
### 1. 初回設定
1. ビューアーを開く
2. owner / repo を入力する
3. GitHub PAT と age 秘密鍵を入力する
4. ブラウザ保存を有効にする
5. 接続確認後、直近 7 日の run を読み込む

### 2. ダッシュボード確認
1. 数値カードで期間内の総 run 数、総 token、総 cost、平均実行時間、失敗数を確認する
2. 日 / 週 / 月 / 任意期間で集計を切り替える
3. 必要に応じて provider / command / failure / 検索で絞る
4. 気になる run を一覧から開く

### 3. run 詳細確認
1. run の概要情報を確認する
2. metrics の構造化表示を見る
3. artifact metadata と report を確認する
4. session timeline を開いて AI の流れを見る
5. 必要なら raw / diff / status を確認する

### 4. 失敗分析
1. run 詳細で失敗イベント一覧を表示する
2. イベントを選んでエラー内容と関連コマンドを確認する
3. timeline の該当位置へ移動する
4. 必要なら raw session にフォールバックする

### 5. 反復利用
1. 次回アクセス時は保存済み設定で自動ロードする
2. キャッシュがあれば高速表示する
3. 取得差分だけを追加読み込みする

## CONSTRAINTS
- Budget: 未確定。プロトタイプとして小さく始める
- Timeline: 強い締切なし。まず使えることが重要
- Team: 基本的に単独利用・単独運用
- Technical:
  - サーバーは置かない
  - 静的ホスティングで完結する
  - owner / repo は入力式
  - workflow 名は固定
  - `vv-ai` の artifact 形式に最適化する
  - 実装技術の詳細指定は本要件には含めない

## SUCCESS METRICS
### 合格ライン
- run 一覧が見られる
- token 集計が見られる
- 失敗イベント詳細が見られる
- session の時系列表示が見られる

### 継続利用判断
- GitHub Actions UI を開く頻度が下がる
- 失敗原因の特定時間が短縮する
- token / cost / duration の傾向確認が楽になる

## POST-LAUNCH
- まずは自分用プロトタイプとして運用開始
- 実データを見ながら Codex session 解析ルールを強化
- 必要に応じてグラフや検索を拡張
- private repository 対応や複数 repo 対応は後続検討事項

## OUT OF SCOPE
- バックエンド API / サーバーサイド復号
- 認証基盤、ユーザー管理、組織権限制御
- 複数リポジトリ横断ダッシュボード
- 本格的なモバイル最適化
- BI ツール級の分析機能
- 一般公開向けの polished UI

## OPEN QUESTIONS
- Codex の実データにおける event schema を見たうえで、どこまで安定して timeline 化できるか
- session artifact が 2 件ある run の見せ方をタブ方式とするか、統合表示とするか
- キャッシュの保持期限を固定するか、手動削除のみとするか
- グラフを初版に含めるか、後続フェーズに分けるか

## NOTES & INSIGHTS
- `.github/workflows/vv-ai.yml` では `issue_comment` と `workflow_dispatch` の両方で `vv-ai` workflow が起動する。
- `workflow_dispatch` の command 入力は `reply / plan / implement / review / issue`。
- workflow は session artifact を最大 2 件、metrics artifact を 1 件、report artifact を 1 件アップロードする設計。
- session artifact には `meta.json`, `git-diff.patch`, `git-staged.patch`, `git-status.txt`, `untracked`, `provider-session` が含まれる。
- metrics artifact は `summary`, `usage`, `behavior`, `tools`, `steps`, `provider_specific` を持つ。
- report artifact は Markdown で、Summary / Changes / Decisions / Validation / Risks / Open Questions / Next Actions / Notes を含む。
- Codex は `CODEX_HOME` 配下を session artifact に広く保存する設計で、viewer 側の解析が重要。
- Claude は `~/.claude/projects/<sanitized-cwd>/` 配下の session JSONL と付随ディレクトリを扱う設計。
- `src/vv_ai/provider_execution.py` 上でも Codex JSONL 解析には TODO が残っており、Codex metrics は best-effort である。したがって、Codex の詳細分析は metrics より session 重視で設計するのが妥当。
- 依頼者の関心は「成功率」そのものより、「何が失敗したか」「AI がどう進んだか」に強く寄っている。
