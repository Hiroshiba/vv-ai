"""age を使う artifact 暗号化と復号。"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping
from pathlib import Path

AGE_PUBLIC_KEY_ENV = "VV_AI_AGE_PUBLIC_KEY"
AGE_PUBLIC_KEY_FILE_ENV = "VV_AI_AGE_PUBLIC_KEY_FILE"
AGE_SECRET_KEY_ENV = "VV_AI_AGE_SECRET_KEY"
AGE_SECRET_KEY_FILE_ENV = "VV_AI_AGE_SECRET_KEY_FILE"


class ArtifactCryptoError(Exception):
    """artifact 暗号化と復号の失敗を表す例外。"""


def resolve_age_public_key(env: Mapping[str, str]) -> str:
    """暗号化用の公開鍵を環境変数から返す。"""
    return _resolve_required_secret(env, AGE_PUBLIC_KEY_FILE_ENV, AGE_PUBLIC_KEY_ENV)


def resolve_age_secret_key(env: Mapping[str, str]) -> str:
    """復号用の秘密鍵を環境変数から返す。"""
    return _resolve_required_secret(env, AGE_SECRET_KEY_FILE_ENV, AGE_SECRET_KEY_ENV)


def encrypt_file(
    source_path: Path,
    destination_path: Path,
    age_public_key: str,
) -> None:
    """単一 file を age で暗号化する。"""
    _ensure_age_command()
    if not source_path.is_file():
        raise ArtifactCryptoError(f"`{source_path}` は暗号化対象の file ではありません")
    if destination_path.exists():
        raise ArtifactCryptoError(f"`{destination_path}` は既に存在します")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_destination_path = destination_path.parent / f".{destination_path.name}.tmp"
    if temp_destination_path.exists():
        raise ArtifactCryptoError(f"`{temp_destination_path}` が残っています")

    try:
        _run_age_command(
            [
                "age",
                "--encrypt",
                "--recipient",
                _normalize_secret(age_public_key, AGE_PUBLIC_KEY_ENV),
                "--output",
                str(temp_destination_path),
                str(source_path),
            ]
        )
        temp_destination_path.replace(destination_path)
    except Exception:
        _cleanup_path(temp_destination_path)
        raise


def decrypt_file(
    source_path: Path,
    destination_path: Path,
    age_secret_key: str,
) -> None:
    """暗号化済み file を復号する。"""
    _ensure_age_command()
    if not source_path.is_file():
        raise ArtifactCryptoError(f"`{source_path}` は復号対象の file ではありません")
    if destination_path.exists():
        raise ArtifactCryptoError(f"`{destination_path}` は既に存在します")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_destination_path = destination_path.parent / f".{destination_path.name}.tmp"
    if temp_destination_path.exists():
        raise ArtifactCryptoError(f"`{temp_destination_path}` が残っています")

    try:
        _run_age_command(
            [
                "age",
                "--decrypt",
                "--identity",
                "-",
                "--output",
                str(temp_destination_path),
                str(source_path),
            ],
            stdin_text=_normalize_secret(age_secret_key, AGE_SECRET_KEY_ENV) + "\n",
        )
        temp_destination_path.replace(destination_path)
    except Exception:
        _cleanup_path(temp_destination_path)
        raise


def encrypt_directory(
    source_dir: Path,
    destination_path: Path,
    age_public_key: str,
) -> None:
    """directory を tar bundle 化して age で暗号化する。"""
    if not source_dir.is_dir():
        raise ArtifactCryptoError(f"`{source_dir}` は暗号化対象の directory ではありません")

    with tempfile.TemporaryDirectory(prefix="vv-ai-age-encrypt-") as temp_root:
        archive_path = Path(temp_root) / f"{source_dir.name}.tar"
        _create_tar_archive(source_dir, archive_path)
        encrypt_file(archive_path, destination_path, age_public_key)


def decrypt_directory(
    source_path: Path,
    destination_dir: Path,
    age_secret_key: str,
) -> None:
    """暗号化済み tar bundle を復号して directory へ展開する。"""
    if destination_dir.exists():
        raise ArtifactCryptoError(f"`{destination_dir}` は既に存在します")

    with tempfile.TemporaryDirectory(prefix="vv-ai-age-decrypt-") as temp_root:
        archive_path = Path(temp_root) / "artifact.tar"
        decrypt_file(source_path, archive_path, age_secret_key)
        temp_destination_dir = destination_dir.parent / f".{destination_dir.name}.tmp"
        if temp_destination_dir.exists():
            raise ArtifactCryptoError(f"`{temp_destination_dir}` が残っています")
        try:
            temp_destination_dir.mkdir(parents=True, exist_ok=False)
            _extract_tar_archive(archive_path, temp_destination_dir)
            temp_destination_dir.replace(destination_dir)
        except Exception:
            _cleanup_path(temp_destination_dir)
            raise


def decrypt_file_text(
    source_path: Path,
    age_secret_key: str,
) -> str:
    """暗号化済み text file を UTF-8 文字列へ復号する。"""
    with tempfile.TemporaryDirectory(prefix="vv-ai-age-text-") as temp_root:
        plaintext_path = Path(temp_root) / "plaintext"
        decrypt_file(source_path, plaintext_path, age_secret_key)
        try:
            return plaintext_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArtifactCryptoError(
                f"`{source_path}` の復号結果を読み込めませんでした"
            ) from exc


def _resolve_required_secret(
    env: Mapping[str, str],
    file_env: str,
    value_env: str,
) -> str:
    """ファイルパス env 優先、生キー値 env フォールバックで秘密値を返す。"""
    file_path = env.get(file_env, "").strip()
    if file_path:
        path = Path(file_path)
        if not path.is_file():
            raise ArtifactCryptoError(
                f"`{file_env}` で指定されたファイル `{file_path}` が見つかりません"
            )
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ArtifactCryptoError(
                f"`{file_env}` で指定されたファイル `{file_path}` が空です"
            )
        return content
    raw_value = env.get(value_env)
    if raw_value is None:
        raise ArtifactCryptoError(
            f"環境変数 `{file_env}` または `{value_env}` が必要です"
        )
    return _normalize_secret(raw_value, value_env)


def _normalize_secret(value: str, env_name: str) -> str:
    """空文字でない秘密値を返す。"""
    normalized = value.strip()
    if normalized == "":
        raise ArtifactCryptoError(f"環境変数 `{env_name}` が空です")
    return normalized


def _ensure_age_command() -> None:
    """age コマンドが実行可能か確認する。"""
    if shutil.which("age") is not None:
        return
    raise ArtifactCryptoError("`age` コマンドが見つかりません")


def _run_age_command(command: list[str], *, stdin_text: str | None = None) -> None:
    """age コマンドを実行する。stdin_text を渡すと標準入力経由で流し込む。"""
    result = subprocess.run(
        command,
        input=stdin_text,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return

    stderr = result.stderr.strip()
    detail = f": {stderr}" if stderr else ""
    raise ArtifactCryptoError(
        f"`{' '.join(command[:2])}` の実行に失敗しました{detail}"
    )


def _create_tar_archive(
    source_dir: Path,
    archive_path: Path,
) -> None:
    """directory から tar archive を作る。"""
    try:
        with tarfile.open(archive_path, mode="w") as archive:
            archive.add(source_dir, arcname=".")
    except OSError as exc:
        raise ArtifactCryptoError(f"`{source_dir}` の bundle 化に失敗しました") from exc
    except tarfile.TarError as exc:
        raise ArtifactCryptoError(f"`{source_dir}` の bundle 化に失敗しました") from exc


def _extract_tar_archive(
    archive_path: Path,
    destination_dir: Path,
) -> None:
    """tar archive を directory へ展開する。"""
    try:
        with tarfile.open(archive_path, mode="r") as archive:
            archive.extractall(destination_dir, filter="data")
    except OSError as exc:
        raise ArtifactCryptoError(f"`{archive_path}` の展開に失敗しました") from exc
    except tarfile.TarError as exc:
        raise ArtifactCryptoError(f"`{archive_path}` の展開に失敗しました") from exc


def _cleanup_path(path: Path) -> None:
    """途中生成した path を削除する。"""
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink()
