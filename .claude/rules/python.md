---
paths: "**/*.py"
---

## Python のコーディング規約

- `pip`ではなく`uv`を使え
- 新しい記法を使え
  - typing.List 等を使うな、list 等を使え
- `os.path`を使うな
  - `from pathlib import Path`を使え
- `with Path.open()`を使うな
  - `Path.read_text()`や`Path.read_bytes()`を使え
- import 文はコード最上部にまとめよ
- `def main():`は最上部に書け
  - `if __main__:`は最下部に書け
