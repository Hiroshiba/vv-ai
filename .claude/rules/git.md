# Git ワークフロー

## コミット前のチェック

- commit 前に SSH 署名キャッシュを確認せよ
  - 署名待ちでハングするとユーザーが操作不能になる
  - `ssh-add -L | grep -Fqx "$(cat $(git config user.signingkey))"` でキャッシュ確認
  - キャッシュは署名後 1 日有効
  - キャッシュなしの場合は `ssh-add $(git config user.signingkey | sed 's/\.pub$//')` を実行するよう促せ
