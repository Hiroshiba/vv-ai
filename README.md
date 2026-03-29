# vv-ai

Codex CLI と Claude Code CLI を活用し、GitHub Actions とローカル実行の両方で動かす AI 自動化ツールキットです。

Issue / PR へのコメントやワークフロー手動実行から AI を起動し、計画・実装・レビュー・Issue 作成などのタスクを自動実行します。

## セットアップ

前提条件:

- Python 3.12
- uv
- age（artifact の暗号化・復号に使用）
- Codex CLI または Claude Code CLI のいずれか
- gh（GitHub 操作に使用）

依存関係のインストール:

```sh
uv sync
```

`vv-ai` コマンドが使えるようになります。

## 設定

### vv-ai.yml

リポジトリルートに `vv-ai.yml` を配置します。

```yaml
allowed_users:
  - Hiroshiba

provider_priority:
  - codex
  - claude
```

| 項目 | 必須 | 説明 |
| --- | --- | --- |
| `allowed_users` | 必須 | コマンド実行を許可する GitHub ユーザーの一覧 |
| `provider_priority` | 任意 | プロバイダの優先順。デフォルトは `[codex, claude]` |

### Secrets

4 つの Secrets を登録します。

| 名前 | 用途 |
| --- | --- |
| `VV_OPENAI_API_KEY` | Codex CLI 用 API キー |
| `VV_ANTHROPIC_API_KEY` | Claude Code CLI 用 API キー |
| `VV_AI_AGE_PUBLIC_KEY` | artifact 暗号化に使う公開鍵 |
| `VV_AI_AGE_SECRET_KEY` | artifact 復号に使う秘密鍵 |

ローカル実行では、環境変数に直接セットするか `_FILE` サフィックスでファイルパスを渡します。

```sh
export VV_OPENAI_API_KEY_FILE=/path/to/openai_key
export VV_ANTHROPIC_API_KEY_FILE=/path/to/anthropic_key
export VV_AI_AGE_PUBLIC_KEY_FILE=/path/to/age_public_key
export VV_AI_AGE_SECRET_KEY_FILE=/path/to/age_secret_key
```

## ローカル実行

`--event local` がデフォルトです。

```sh
uv run vv-ai --command plan --target-url https://github.com/org/repo/issues/123 --instruction "実装方針を3案ください"
uv run vv-ai --command implement --target-url https://github.com/org/repo/issues/123 --dry-run
uv run vv-ai --command reply --target-type issue --target-number 123 --instruction "このIssueの要点を教えて"
```

主要な引数:

| 引数 | 説明 |
| --- | --- |
| `--command` | `reply` / `plan` / `implement` / `review` / `issue` |
| `--instruction` | 自然言語の指示本文 |
| `--target-url` | 対象の Issue / PR URL またはローカルパス |
| `--target-type` | `issue` または `pr` |
| `--target-number` | Issue / PR 番号 |
| `--provider` | `codex` または `claude` |
| `--session` | `inherit` / `compact` / `new`。デフォルトは `inherit` |
| `--dry-run` | GitHub への外部反映を行わず、artifact のみ保存する |
| `--repo` | Issue 作成先の `org/repo`。`issue` コマンド専用 |
| `--event-file` | GitHub event payload JSON を読み込んで再現実行する |

## GitHub Actions

### コメント起動

Issue または PR のコメントで `@vv-ai` で始めると起動します。

```
@vv-ai plan 実装方針を3案ください
@vv-ai implement --provider codex このIssueを実装して
@vv-ai review --session new このPRをレビューして
```

`vv-ai.yml` の `allowed_users` に含まれるユーザーのコメントのみ反応します。未許可ユーザーには何も返しません。

### workflow_dispatch

`gh workflow run` で手動起動します。

```sh
gh workflow run vv-ai.yml \
  -f command=plan \
  -f target_url=https://github.com/org/repo/issues/123 \
  -f instruction="実装方針を3案ください"
```

入力項目: `command`, `target_type`, `target_number`, `target_url`, `instruction`, `provider`, `session_mode`, `dry_run`, `repo`

実行結果は GitHub Actions の Run と artifact から確認します。コメントやリアクションは返しません。

### 導入手順

1. `.github/workflows/vv-ai.yml` をリポジトリにコピーする
2. リポジトリの Settings > Secrets and variables > Actions に4つの Secret を登録する
3. リポジトリルートに `vv-ai.yml` を配置する

workflow には `contents: write`, `issues: write`, `pull-requests: write` の権限が必要です。

## Reusable Workflow 化の前提

現状は各リポジトリにソースをコピーするプロトタイプ方式です。将来 Reusable Workflow に切り出す際の設計前提:

- workflow は薄いオーケストレーターで、実処理は Python CLI に集約済み
- CLI の起動経路は `--event` と `--event-file` で抽象化済みのため、caller workflow から呼び出しやすい構造になっている
- Secrets は `_FILE` 環境変数でファイルパスとして渡す方式のため、caller workflow から secrets を受け渡しできる
