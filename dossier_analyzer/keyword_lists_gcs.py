"""Keyword list CSV files under ``users/<id>/keyword_lists/`` (not shown in the dossier tree)."""

from __future__ import annotations

import re
from pathlib import Path

from google.api_core import exceptions as gcs_exceptions
from google.cloud import storage

from dossier_analyzer.gcs_ops import (
    safe_delete_blob,
    safe_upload_bytes,
    user_root_prefix,
)

KEYWORD_LISTS_DIR = "keyword_lists"
MAX_LISTS_PER_USER = 10
DEFAULT_LIST_SLUG = "default"
DEFAULT_LIST_LABEL = "liste par défaut"


def format_keyword_list_label(slug: str) -> str:
    """Libellé affiché pour une liste (le slug technique ``default`` a un nom lisible)."""
    if slug == DEFAULT_LIST_SLUG:
        return DEFAULT_LIST_LABEL
    return slug


def keyword_lists_prefix(user_storage_prefix: str) -> str:
    return f"{user_root_prefix(user_storage_prefix)}{KEYWORD_LISTS_DIR}/"


def sanitize_keyword_list_slug(raw: str) -> str:
    """Lowercase slug safe for object names: ``[a-z0-9][a-z0-9._-]*``."""
    s = str(raw).strip().lower()
    if not s:
        raise ValueError("Nom de liste requis.")
    if len(s) > 64:
        raise ValueError("Nom trop long (64 caractères max).")
    if not re.match(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$", s):
        raise ValueError(
            "Utilisez des lettres minuscules, chiffres, points, tirets et underscores "
            "(lettre ou chiffre en premier et en dernier)."
        )
    return s


def keyword_list_object_name(user_storage_prefix: str, slug: str) -> str:
    s = sanitize_keyword_list_slug(slug)
    return f"{keyword_lists_prefix(user_storage_prefix)}{s}.csv"


def list_keyword_list_slugs(
    client: storage.Client, bucket_name: str, user_storage_prefix: str
) -> list[str]:
    pref = keyword_lists_prefix(user_storage_prefix)
    bucket = client.bucket(bucket_name)
    slugs: set[str] = set()
    for blob in bucket.list_blobs(prefix=pref):
        if blob.name.endswith("/"):
            continue
        rel = blob.name[len(pref) :]
        if rel.endswith(".csv"):
            slug = Path(rel).stem
            if slug:
                slugs.add(slug)
    return sorted(slugs, key=str.lower)


def download_keyword_list_csv(
    client: storage.Client, bucket_name: str, user_storage_prefix: str, slug: str
) -> bytes | None:
    name = keyword_list_object_name(user_storage_prefix, slug)
    blob = client.bucket(bucket_name).blob(name)
    try:
        if not blob.exists(client=client):
            return None
        return blob.download_as_bytes()
    except gcs_exceptions.GoogleAPIError:
        return None


def upload_keyword_list_csv(
    client: storage.Client,
    bucket_name: str,
    user_storage_prefix: str,
    slug: str,
    data: bytes,
) -> None:
    name = keyword_list_object_name(user_storage_prefix, slug)
    safe_upload_bytes(
        client,
        bucket_name,
        name,
        user_storage_prefix,
        data,
        "text/csv; charset=utf-8",
    )


def delete_keyword_list_csv(
    client: storage.Client, bucket_name: str, user_storage_prefix: str, slug: str
) -> None:
    name = keyword_list_object_name(user_storage_prefix, slug)
    safe_delete_blob(client, bucket_name, name, user_storage_prefix)


def rename_keyword_list_csv(
    client: storage.Client,
    bucket_name: str,
    user_storage_prefix: str,
    old_slug: str,
    new_slug: str,
) -> None:
    old_name = keyword_list_object_name(user_storage_prefix, old_slug)
    new_name = keyword_list_object_name(user_storage_prefix, new_slug)
    if old_name == new_name:
        return
    bucket = client.bucket(bucket_name)
    old_b = bucket.blob(old_name)
    try:
        data = old_b.download_as_bytes()
    except gcs_exceptions.GoogleAPIError as exc:
        raise RuntimeError("Téléchargement de l’ancienne liste impossible.") from exc
    try:
        safe_upload_bytes(
            client,
            bucket_name,
            new_name,
            user_storage_prefix,
            data,
            "text/csv; charset=utf-8",
        )
    except Exception as exc:
        raise RuntimeError("Échec de l’enregistrement sous le nouveau nom.") from exc
    try:
        safe_delete_blob(client, bucket_name, old_name, user_storage_prefix)
    except Exception as exc:
        raise RuntimeError(
            "La nouvelle liste a été créée mais l’ancienne n’a pas pu être supprimée."
        ) from exc
