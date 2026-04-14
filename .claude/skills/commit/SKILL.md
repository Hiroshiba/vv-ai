---
description: 変更をコミットする。実装完了後の git commit に使う。
allowed-tools: Bash(git add:*), Bash(git commit:*), Bash(mktemp:*)
---

# コミット

引数のクォート事故を避けるため、コミットメッセージは一時ファイル経由で渡す。

## 処理フロー

1. `mktemp` で一時ファイルを作成する
2. `Write` ツールでコミットメッセージを一時ファイルに書く
3. `git add <paths>` で対象ファイルをステージングする
4. `git commit -F <tempfile>` でコミットする
