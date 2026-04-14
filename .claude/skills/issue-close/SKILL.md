---
description: GitHub Issue をクローズする。コメント追加も行う。
allowed-tools: Bash(gh issue close:*), Bash(gh issue comment:*), Bash(mktemp:*)
---

# Issue クローズ

引数のクォート事故を避けるため、コメント本文は一時ファイル経由で渡す。

## 処理フロー

1. `mktemp` で一時ファイルを作成する
2. `Write` ツールでコメント本文を一時ファイルに書く
3. `gh issue comment <num> --body-file <tempfile>` でコメントを投稿する
4. `gh issue close <num>` でクローズする

コメント不要なら 1〜3 を省略して 4 のみ実行する。
