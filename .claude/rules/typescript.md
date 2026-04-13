---
paths: "**/*.{ts,tsx}"
---

## TypeScript のコーディング規約

- `npm`ではなく`pnpm`を使え
- exportは最小限にせよ
  - 使われていない関数・型はexportするな
- ルールは TypeScript 設定か ESLint 設定で強制せよ
  - 型アサーション `as` 禁止、`satisfies` を使うかロジックを修正せよ
  - `!` non-null assertion 禁止
  - `null`/`undefined` の `===`/`!==` 禁止、`==`/`!=` を使え
- 複数変数の同期が必要な場合は Discriminated Union を使え
- Zod バリデーションを必ず行え
