"""Safe GCS mutations under ``users/<storage_id>/``."""

from __future__ import annotations

import re
from pathlib import Path

from google.api_core import exceptions as gcs_exceptions
from google.cloud import storage

DOSSIER_FOLDER_PLACEHOLDER = ".dossier_placeholder"


def user_root_prefix(user_storage_prefix: str) -> str:
    return f"users/{user_storage_prefix}/"


def _normalize_folder_rel(rel: Path) -> str:
    s = rel.as_posix().strip("/")
    if s in (".", ""):
        return ""
    return s


def folder_gcs_prefix(user_storage_prefix: str, folder_rel: Path) -> str:
    """Prefix ending with ``/`` for all objects inside this virtual folder."""
    root = user_root_prefix(user_storage_prefix)
    sub = _normalize_folder_rel(folder_rel)
    if not sub:
        return root
    return f"{root}{sub}/"


def assert_object_in_user_workspace(object_name: str, user_storage_prefix: str) -> None:
    root = user_root_prefix(user_storage_prefix)
    if not object_name.startswith(root):
        raise ValueError("Object hors de l’espace utilisateur.")
    remainder = object_name[len(root) :]
    if not remainder or remainder.startswith("/") or ".." in Path(remainder).parts:
        raise ValueError("Chemin d’objet invalide.")


def assert_prefix_in_user_workspace(prefix: str, user_storage_prefix: str) -> None:
    root = user_root_prefix(user_storage_prefix)
    if not prefix.startswith(root):
        raise ValueError("Préfixe hors de l’espace utilisateur.")


def sanitize_upload_filename(name: str) -> str:
    """Safe object basename for uploads (letters, digits, dot, dash, underscore)."""
    base = Path(str(name).strip()).name.replace("\x00", "")
    if not base or base in (".", ".."):
        raise ValueError("Nom de fichier invalide.")
    base = re.sub(r"[^a-zA-Z0-9._-]", "_", base)
    if not base:
        raise ValueError("Nom de fichier invalide.")
    return base[:180]


def sanitize_new_segment(name: str) -> str:
    base = Path(str(name).strip()).name.replace("\x00", "")
    if not base or base in (".", ".."):
        raise ValueError("Nom invalide.")
    if not re.match(r"^[a-zA-Z0-9._-]+$", base):
        raise ValueError("Utilisez uniquement lettres, chiffres, points, tirets et underscores.")
    if base == DOSSIER_FOLDER_PLACEHOLDER:
        raise ValueError("Ce nom est réservé.")
    return base[:180]


def delete_blob(client: storage.Client, bucket_name: str, object_name: str) -> None:
    bucket = client.bucket(bucket_name)
    bucket.blob(object_name).delete()


def delete_all_with_prefix(
    client: storage.Client, bucket_name: str, prefix: str, user_storage_prefix: str
) -> int:
    assert_prefix_in_user_workspace(prefix, user_storage_prefix)
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    bucket = client.bucket(bucket_name)
    n = 0
    for blob in bucket.list_blobs(prefix=prefix):
        blob.delete()
        n += 1
    return n


def upload_bytes_validated(
    client: storage.Client,
    bucket_name: str,
    object_name: str,
    user_storage_prefix: str,
    data: bytes,
    content_type: str | None,
) -> None:
    assert_object_in_user_workspace(object_name, user_storage_prefix)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(data, content_type=content_type)


def safe_upload_bytes(
    client: storage.Client,
    bucket_name: str,
    object_name: str,
    user_storage_prefix: str,
    data: bytes,
    content_type: str | None,
) -> None:
    try:
        upload_bytes_validated(
            client, bucket_name, object_name, user_storage_prefix, data, content_type
        )
    except gcs_exceptions.GoogleAPIError as exc:
        raise RuntimeError("Envoi du fichier impossible.") from exc


def create_subfolder_placeholder(
    client: storage.Client,
    bucket_name: str,
    user_storage_prefix: str,
    parent_folder_rel: Path,
    new_folder_name: str,
) -> str:
    seg = sanitize_new_segment(new_folder_name)
    base = folder_gcs_prefix(user_storage_prefix, parent_folder_rel)
    object_name = f"{base}{seg}/{DOSSIER_FOLDER_PLACEHOLDER}"
    assert_object_in_user_workspace(object_name, user_storage_prefix)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    try:
        blob.upload_from_string(b"", content_type="application/octet-stream")
    except gcs_exceptions.GoogleAPIError as exc:
        raise RuntimeError("Échec de la création du dossier.") from exc
    return object_name


def safe_delete_blob(
    client: storage.Client, bucket_name: str, object_name: str, user_storage_prefix: str
) -> None:
    assert_object_in_user_workspace(object_name, user_storage_prefix)
    try:
        delete_blob(client, bucket_name, object_name)
    except gcs_exceptions.GoogleAPIError as exc:
        raise RuntimeError("Suppression impossible.") from exc


def safe_delete_folder_prefix(
    client: storage.Client,
    bucket_name: str,
    user_storage_prefix: str,
    folder_rel: Path,
) -> int:
    if _normalize_folder_rel(folder_rel) == "":
        raise ValueError("Impossible de supprimer la racine du dossier cloud.")
    prefix = folder_gcs_prefix(user_storage_prefix, folder_rel)
    try:
        return delete_all_with_prefix(client, bucket_name, prefix, user_storage_prefix)
    except gcs_exceptions.GoogleAPIError as exc:
        raise RuntimeError("Suppression du dossier impossible.") from exc
