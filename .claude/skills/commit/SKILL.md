---
description: 変更をコミットする。実装完了後の git commit に使う。
allowed-tools: Bash(git add:*), Bash(git commit:*)
---

# コミット

引数のクォート事故を避けるため、コミットメッセージは一時ファイル経由で渡す。

## 処理フロー

1. `mktemp -u` を引数なしで実行し、一時ファイルパスを取得する
2. `Write` ツールでコミットメッセージをそのパスに書く
3. `git add <paths>` で対象ファイルをステージングする
4. `git commit -F <tempfile>` でコミットする
