"""Build a :class:`TreeNode` from GCS object names under ``users/<storage_id>/``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.cloud import storage

from dossier_analyzer.scan import SUPPORTED_EXTENSIONS, TreeNode


def list_user_blob_entries(
    client: storage.Client, bucket_name: str, user_storage_prefix: str
) -> list[dict[str, Any]]:
    """Metadata for supported files under the user's prefix (relative keys as posix)."""
    user_root = f"users/{user_storage_prefix}/"
    bucket = client.bucket(bucket_name)
    out: list[dict[str, Any]] = []
    for blob in bucket.list_blobs(prefix=user_root):
        if blob.name.endswith("/"):
            continue
        rel = blob.name[len(user_root) :]
        if not rel or rel.startswith("/"):
            continue
        ext = Path(rel).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        out.append(
            {
                "object_name": blob.name,
                "rel": rel,
                "updated": blob.updated,
            }
        )
    return out


def _trie_to_node(
    trie: dict[str, Any],
    display_name: str,
    rel: Path,
    disk_path: Path,
    path_to_object: dict[str, str],
) -> TreeNode:
    tn = TreeNode(name=display_name, rel=rel, path=disk_path)
    for fname, fake_file, object_name in sorted(
        trie["files"], key=lambda x: x[0].lower()
    ):
        path_to_object[str(fake_file.resolve())] = object_name
        tn.files.append(fake_file)
    for subname, sub in sorted(trie["dirs"].items(), key=lambda x: x[0].lower()):
        child_rel = rel / subname
        tn.children.append(
            _trie_to_node(sub, subname, child_rel, disk_path / subname, path_to_object)
        )
    return tn


def build_tree_from_gcs_entries(
    entries: list[dict[str, Any]],
    bucket_name: str,
    user_storage_prefix: str,
) -> tuple[TreeNode | None, dict[str, str]]:
    """
    Synthetic :class:`TreeNode` whose file paths live under
    ``/__dossier_gcs__/<bucket>/users/<id>/…`` (paths need not exist on disk).
    """
    if not entries:
        return None, {}

    fake_base = Path("/") / "__dossier_gcs__" / bucket_name / "users" / user_storage_prefix
    root_trie: dict[str, Any] = {"dirs": {}, "files": []}

    for e in entries:
        rel = str(e["rel"])
        parts = [p for p in rel.split("/") if p]
        if not parts:
            continue
        fname = parts[-1]
        d_parts = parts[:-1]
        node = root_trie
        for d in d_parts:
            node = node["dirs"].setdefault(d, {"dirs": {}, "files": []})
        fake_file = fake_base / rel
        node["files"].append((fname, fake_file, str(e["object_name"])))

    path_to_object: dict[str, str] = {}
    root = TreeNode(name="Espace cloud", rel=Path("."), path=fake_base)
    for fname, fake_file, object_name in sorted(root_trie["files"], key=lambda x: x[0].lower()):
        path_to_object[str(fake_file.resolve())] = object_name
        root.files.append(fake_file)
    for subname, sub in sorted(root_trie["dirs"].items(), key=lambda x: x[0].lower()):
        root.children.append(
            _trie_to_node(sub, subname, Path(subname), fake_base / subname, path_to_object)
        )
    return root, path_to_object


def gcs_index_fingerprint(entries: list[dict[str, Any]]) -> str:
    import hashlib

    h = hashlib.sha256()
    for e in sorted(entries, key=lambda x: x["object_name"]):
        h.update(e["object_name"].encode())
        h.update(str(e.get("updated")).encode())
    return h.hexdigest()
