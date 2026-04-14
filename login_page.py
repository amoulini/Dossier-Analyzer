import base64
import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

PDF_MAGIC = b"%PDF"
_DEFAULT_MAX_UPLOAD_MB = 50
_DEFAULT_LIST_CACHE_SEC = 90.0


@st.cache_resource
def _gcs_client():
    """Reuse one client per process — avoids repeated ADC / HTTP pool setup."""
    from google.cloud import storage

    return storage.Client()


def login_screen() -> None:
    st.header("This app is private.")
    st.subheader("Please log in.")
    st.button("Log in with Google", on_click=st.login)


def render_pdf_preview(file_bytes: bytes) -> None:
    pdf_b64 = base64.b64encode(file_bytes).decode("utf-8")
    st.markdown(
        (
            '<iframe src="data:application/pdf;base64,'
            f"{pdf_b64}"
            '" width="100%" height="800" type="application/pdf"></iframe>'
        ),
        unsafe_allow_html=True,
    )


def _secrets_gcp() -> dict:
    try:
        raw = st.secrets.get("gcp", {})
        return dict(raw) if raw is not None else {}
    except Exception:
        return {}


def _bucket_name() -> str:
    import os

    name = str(_secrets_gcp().get("bucket_name", "") or "").strip()
    if name:
        return name
    return str(os.environ.get("GCS_BUCKET_NAME", "") or "").strip()


def _max_upload_bytes() -> int:
    import os

    raw = _secrets_gcp().get("max_upload_mb")
    if raw is None:
        raw = os.environ.get("GCS_MAX_UPLOAD_MB")
    try:
        mb = int(raw) if raw is not None else _DEFAULT_MAX_UPLOAD_MB
    except (TypeError, ValueError):
        mb = _DEFAULT_MAX_UPLOAD_MB
    return max(1, mb) * 1024 * 1024


def _list_cache_ttl_sec() -> float:
    import os

    raw = _secrets_gcp().get("list_cache_seconds")
    if raw is None:
        raw = os.environ.get("GCS_LIST_CACHE_SECONDS")
    try:
        sec = float(raw) if raw is not None else _DEFAULT_LIST_CACHE_SEC
    except (TypeError, ValueError):
        sec = _DEFAULT_LIST_CACHE_SEC
    return max(0.0, sec)


def _user_dict() -> dict:
    try:
        return dict(st.user)
    except Exception:
        return {}


def _user_storage_prefix() -> str | None:
    """Stable, path-safe segment per user. Prefer OIDC `sub`; else hashed email."""
    info = _user_dict()
    sub = info.get("sub")
    if sub:
        seg = _slug_path_segment(str(sub))
        return seg if seg else None
    email = info.get("email")
    if email:
        digest = hashlib.sha256(str(email).strip().lower().encode("utf-8")).hexdigest()
        return f"h_{digest}"
    return None


def _saved_pdfs_cache_key(bucket_name: str) -> str | None:
    user_prefix = _user_storage_prefix()
    if not user_prefix:
        return None
    return f"_saved_pdfs_list_v1:{bucket_name}:{user_prefix}"


def _invalidate_saved_pdfs_cache(bucket_name: str) -> None:
    key = _saved_pdfs_cache_key(bucket_name)
    if key and key in st.session_state:
        del st.session_state[key]
    _invalidate_stored_pdf_bytes_cache(bucket_name)


def _invalidate_stored_pdf_bytes_cache(bucket_name: str) -> None:
    prefix = f"_gcs_pdf_bytes_cache:{bucket_name}:"
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith(prefix):
            del st.session_state[k]


def _expected_user_pdfs_prefix() -> str | None:
    user_prefix = _user_storage_prefix()
    if not user_prefix:
        return None
    return f"users/{user_prefix}/pdfs/"


def _slug_path_segment(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    return re.sub(r"[^a-zA-Z0-9._@-]", "_", value)[:128]


def _sanitize_upload_filename(name: str) -> str:
    base = Path(str(name)).name.replace("\x00", "")
    if not base or base in (".", ".."):
        base = "document.pdf"
    base = re.sub(r"[^a-zA-Z0-9._-]", "_", base)
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base[:180]


def _validate_pdf_bytes(data: bytes) -> None:
    if len(data) > _max_upload_bytes():
        raise ValueError(
            f"File too large (max {_max_upload_bytes() // (1024 * 1024)} MB)."
        )
    if not data.startswith(PDF_MAGIC):
        raise ValueError("Only valid PDF files are accepted.")


def upload_pdf_to_gcs(data: bytes, original_filename: str, bucket_name: str) -> str:
    from google.api_core import exceptions as gcs_exceptions

    user_prefix = _user_storage_prefix()
    if not user_prefix:
        raise RuntimeError("Cannot resolve user identity for storage.")

    _validate_pdf_bytes(data)
    safe_name = _sanitize_upload_filename(original_filename)
    # Object name is the sanitized original filename (same basename twice overwrites).
    object_path = f"users/{user_prefix}/pdfs/{safe_name}"

    try:
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_path)
        blob.upload_from_string(data, content_type="application/pdf")
    except gcs_exceptions.GoogleAPIError as exc:
        raise RuntimeError("Could not upload to storage. Please try again later.") from exc

    return f"gs://{bucket_name}/{object_path}"


def download_pdf_from_gcs(bucket_name: str, object_path: str) -> bytes:
    """Fetch object bytes only if it belongs to the current user prefix."""
    from google.api_core import exceptions as gcs_exceptions

    allowed = _expected_user_pdfs_prefix()
    if not allowed:
        raise RuntimeError("Cannot resolve user identity for storage.")
    if not object_path.startswith(allowed):
        raise ValueError("Invalid file selection.")
    if "\x00" in object_path or "\n" in object_path or "\r" in object_path:
        raise ValueError("Invalid file selection.")
    remainder = object_path[len(allowed) :]
    if not remainder or remainder.startswith("/") or ".." in Path(remainder).parts:
        raise ValueError("Invalid file selection.")

    try:
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_path)
        data = blob.download_as_bytes()
    except gcs_exceptions.GoogleAPIError as exc:
        raise RuntimeError("Could not load file from storage.") from exc

    if len(data) > _max_upload_bytes():
        raise ValueError(
            f"File too large to display (max {_max_upload_bytes() // (1024 * 1024)} MB)."
        )
    if not data.startswith(PDF_MAGIC):
        raise ValueError("Stored object is not a valid PDF.")
    return data


def _gcs_auto_upload_session_key(
    bucket_name: str, pdf_bytes: bytes, original_filename: str
) -> str | None:
    """Session key so we upload once per rerun cycle, not once per identical bytes only."""
    user_prefix = _user_storage_prefix()
    if not user_prefix:
        return None
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    safe = _sanitize_upload_filename(original_filename)
    return f"_gcs_auto_up:{bucket_name}:{user_prefix}:{digest}:{safe}"


def list_user_saved_pdfs(bucket_name: str) -> tuple[list[dict[str, Any]], str | None]:
    """List PDF objects under this user's prefix. Returns (rows, error_message)."""
    from google.api_core import exceptions as gcs_exceptions

    user_prefix = _user_storage_prefix()
    if not user_prefix:
        return [], "Cannot resolve user identity for storage."

    prefix = f"users/{user_prefix}/pdfs/"
    try:
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        rows: list[dict[str, Any]] = []
        for blob in bucket.list_blobs(prefix=prefix):
            if blob.name.endswith("/"):
                continue
            updated = blob.updated
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            rows.append(
                {
                    "file": blob.name.rsplit("/", 1)[-1],
                    "path": blob.name,
                    "size_kb": round((blob.size or 0) / 1024, 1),
                    "updated": updated,
                }
            )
        rows.sort(
            key=lambda r: r["updated"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return rows, None
    except gcs_exceptions.GoogleAPIError:
        return [], "Could not list saved files. The service account needs permission to list objects in the bucket."


def _get_saved_pdfs_cached(
    bucket_name: str,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Return (rows, error, used_cache). Uses session cache to skip GCS on quick reruns."""
    ttl = _list_cache_ttl_sec()
    cache_key = _saved_pdfs_cache_key(bucket_name)
    if cache_key is None:
        rows, err = list_user_saved_pdfs(bucket_name)
        return rows, err, False

    now = time.monotonic()
    entry = st.session_state.get(cache_key)
    if ttl > 0 and isinstance(entry, dict):
        cached_at = entry.get("mono_t")
        if isinstance(cached_at, (int, float)) and (now - float(cached_at)) < ttl:
            return entry.get("rows", []), entry.get("err"), True

    rows, err = list_user_saved_pdfs(bucket_name)
    st.session_state[cache_key] = {"rows": rows, "err": err, "mono_t": now}
    return rows, err, False


# `is_logged_in` is only available when Streamlit auth is configured.
is_logged_in = bool(getattr(st.user, "is_logged_in", False))

if not is_logged_in:
    login_screen()
else:
    st.header(f"Welcome, {getattr(st.user, 'name', 'User')}!")

    bucket = _bucket_name()
    if not bucket:
        st.warning(
            "Cloud storage is not configured. Set `gcp.bucket_name` in `.streamlit/secrets.toml` "
            "or the `GCS_BUCKET_NAME` environment variable to enable uploads."
        )
    else:
        st.subheader("Your saved PDFs")
        _, col_refresh = st.columns([4, 1])
        with col_refresh:
            refresh = st.button("Refresh list", key="refresh_saved_pdfs")
        if refresh:
            _invalidate_saved_pdfs_cache(bucket)
            st.rerun()

        saved_rows, list_err, from_cache = _get_saved_pdfs_cached(bucket)
        if list_err:
            st.warning(list_err)
        elif not saved_rows:
            st.info("No saved PDFs yet.")
        else:
            if from_cache and _list_cache_ttl_sec() > 0:
                st.caption(
                    "List loaded from cache — use **Refresh list** for the latest from storage."
                )
            display = []
            for r in saved_rows:
                u = r["updated"]
                u_str = u.strftime("%Y-%m-%d %H:%M UTC") if u else "—"
                display.append(
                    {
                        "File": r["file"],
                        "Size (KB)": r["size_kb"],
                        "Updated (UTC)": u_str,
                        "Location": f"gs://{bucket}/{r['path']}",
                    }
                )
            st.dataframe(display, use_container_width=True, hide_index=True)

            st.subheader("View a stored PDF")

            def _stored_row_label(i: int) -> str:
                r = saved_rows[i]
                u = r["updated"]
                u_str = u.strftime("%Y-%m-%d %H:%M UTC") if u else "—"
                return f"{r['file']} — {u_str} ({r['size_kb']} KB)"

            pick_idx = st.selectbox(
                "Choose a file to display",
                options=list(range(len(saved_rows))),
                format_func=_stored_row_label,
                index=None,
                placeholder="Select a stored PDF…",
                key="pick_stored_pdf_idx",
            )
            if pick_idx is None:
                st.caption("Select a file above to load the preview.")
            else:
                selected_path = saved_rows[pick_idx]["path"]
                pdf_cache_key = f"_gcs_pdf_bytes_cache:{bucket}:{selected_path}"
                if pdf_cache_key not in st.session_state:
                    try:
                        st.session_state[pdf_cache_key] = download_pdf_from_gcs(
                            bucket, selected_path
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    except RuntimeError as exc:
                        st.error(str(exc))
                if pdf_cache_key in st.session_state:
                    render_pdf_preview(st.session_state[pdf_cache_key])

    uploaded_pdf = st.file_uploader("Upload a PDF document", type=["pdf"])
    if uploaded_pdf is not None:
        pdf_bytes = uploaded_pdf.getvalue()
        st.success(f"Received: {uploaded_pdf.name}")
        render_pdf_preview(pdf_bytes)

        if bucket:
            dedupe_key = _gcs_auto_upload_session_key(
                bucket, pdf_bytes, uploaded_pdf.name
            )
            if dedupe_key is None:
                st.error("Cannot resolve user identity for storage.")
            else:
                status = st.session_state.get(dedupe_key)
                retry_key = "retry_gcs_" + hashlib.sha256(
                    dedupe_key.encode("utf-8")
                ).hexdigest()[:16]

                if status == "uploaded":
                    st.success("Saved to your private folder in the bucket.")
                elif isinstance(status, dict) and status.get("error"):
                    st.error(str(status["error"]))
                    if st.button("Retry upload", key=retry_key):
                        del st.session_state[dedupe_key]
                        st.rerun()
                else:
                    try:
                        uri = upload_pdf_to_gcs(pdf_bytes, uploaded_pdf.name, bucket)
                        st.session_state[dedupe_key] = "uploaded"
                        _invalidate_saved_pdfs_cache(bucket)
                        st.success("Saved to your private folder in the bucket.")
                        st.caption(uri)
                        st.rerun()
                    except ValueError as exc:
                        st.session_state[dedupe_key] = {"error": str(exc)}
                        st.error(str(exc))
                    except RuntimeError as exc:
                        st.session_state[dedupe_key] = {"error": str(exc)}
                        st.error(str(exc))

    st.button("Log out", on_click=st.logout)
