"""
Dossier Analyzer — Streamlit app: dossier exploration (tree + viewer) and keyword-based folder analysis.
"""

from __future__ import annotations

import csv
import hashlib
import io
import mimetypes
import os
import re
import tomllib
import uuid
from html import escape
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st
from openpyxl import Workbook

from dossier_analyzer.extract import extract_text_from_bytes
from dossier_analyzer.gcs_ops import (
    DOSSIER_FOLDER_PLACEHOLDER,
    create_subfolder_placeholder,
    folder_gcs_prefix,
    safe_delete_blob,
    safe_delete_folder_prefix,
    safe_upload_bytes,
    sanitize_upload_filename,
    user_arborescence_prefix,
)
from dossier_analyzer.gcs_tree import (
    build_tree_from_gcs_entries,
    gcs_index_fingerprint,
    list_user_blob_entries,
)
from dossier_analyzer.keyword_lists_gcs import (
    DEFAULT_LIST_LABEL,
    DEFAULT_LIST_SLUG,
    MAX_LISTS_PER_USER,
    delete_keyword_list_csv,
    download_keyword_list_csv,
    format_keyword_list_label,
    list_keyword_list_slugs,
    rename_keyword_list_csv,
    sanitize_keyword_list_slug,
    upload_keyword_list_csv,
)
from dossier_analyzer.match import (
    KeywordEntry,
    RankedFolderMatch,
    normalize_keyword_entries,
    ranked_folder_matches,
)
from dossier_analyzer.scan import TreeNode, count_leaf_folders

# Bumps Streamlit disk cache when the indexing strategy changes (see ``file_text_index_gcs``).
_CACHE_SERIAL = "dossier-analyzer-6"
_GCS_WORKSPACE_MARKER = "__dossier_gcs_workspace__"
_MAX_GCS_INDEX_BYTES = int(os.environ.get("GCS_MAX_INDEX_BYTES", str(50 * 1024 * 1024)))
_DEFAULT_BATCH_UPLOAD_MB = 100


def _gcs_batch_upload_limit_bytes() -> int:
    """Max total size (bytes) for one multi-file upload from the tree menu."""
    try:
        raw = dict(st.secrets.get("gcp", {}) or {}).get("max_batch_upload_mb")
    except Exception:
        raw = None
    if raw is None:
        raw = os.environ.get("GCS_MAX_BATCH_UPLOAD_MB")
    try:
        mb = int(raw) if raw is not None else _DEFAULT_BATCH_UPLOAD_MB
    except (TypeError, ValueError):
        mb = _DEFAULT_BATCH_UPLOAD_MB
    return max(1, mb) * 1024 * 1024

# Microsoft Excel product green (#217346); scoped to key ``export_matches_xlsx`` (class ``st-key-export_matches_xlsx``).
_EXCEL_EXPORT_BTN_CSS = """
<style>
    div.st-key-export_matches_xlsx[data-testid="stDownloadButton"] button,
    div.st-key-export_matches_xlsx button {
        background-color: rgb(33, 115, 70) !important;
        border: 1px solid rgb(27, 95, 59) !important;
        color: rgb(255, 255, 255) !important;
    }
    div.st-key-export_matches_xlsx[data-testid="stDownloadButton"] button:hover,
    div.st-key-export_matches_xlsx button:hover {
        background-color: rgb(27, 95, 59) !important;
        border-color: rgb(22, 80, 50) !important;
        color: rgb(255, 255, 255) !important;
    }
    div.st-key-export_matches_xlsx[data-testid="stDownloadButton"] button:focus-visible,
    div.st-key-export_matches_xlsx button:focus-visible {
        box-shadow: rgb(255, 255, 255) 0px 0px 0px 2px, rgb(33, 115, 70) 0px 0px 0px 4px !important;
    }
</style>
"""

# Bleu distinct pour l’export CSV des mots-clés (key ``kw_csv_export_btn``).
_KW_CSV_EXPORT_BTN_CSS = """
<style>
    div.st-key-kw_csv_export_btn[data-testid="stDownloadButton"] button,
    div.st-key-kw_csv_export_btn button {
        background-color: rgb(30, 64, 175) !important;
        border: 1px solid rgb(30, 58, 138) !important;
        color: rgb(255, 255, 255) !important;
    }
    div.st-key-kw_csv_export_btn[data-testid="stDownloadButton"] button:hover,
    div.st-key-kw_csv_export_btn button:hover {
        background-color: rgb(30, 58, 138) !important;
        border-color: rgb(23, 37, 84) !important;
        color: rgb(255, 255, 255) !important;
    }
    div.st-key-kw_csv_export_btn[data-testid="stDownloadButton"] button:focus-visible,
    div.st-key-kw_csv_export_btn button:focus-visible {
        box-shadow: rgb(255, 255, 255) 0px 0px 0px 2px, rgb(37, 99, 235) 0px 0px 0px 4px !important;
    }
</style>
"""

# Hide Streamlit’s top chrome without hiding ``stToolbar``: the expand-sidebar control
# (``stExpandSidebarButton``) lives inside the toolbar — hiding the whole toolbar made
# the sidebar impossible to reopen when collapsed.
_HIDE_STREAMLIT_TOP_CHROME_CSS = """
<style>
    div[data-testid="stDecoration"] {
        display: none !important;
    }
    div[data-testid="stAppDeployButton"],
    div[data-testid="stToolbarActions"],
    div[data-testid="stMainMenu"] {
        display: none !important;
    }
    header[data-testid="stHeader"] {
        background: transparent;
        box-shadow: none;
    }
    button[data-testid="stExpandSidebarButton"],
    button[data-testid="stSidebarCollapseButton"] {
        background-color: rgb(235, 238, 242) !important;
        color: rgb(71, 85, 105) !important;
        border: 1px solid rgb(203, 213, 225) !important;
        border-radius: 8px !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06) !important;
    }
    button[data-testid="stExpandSidebarButton"]:hover,
    button[data-testid="stSidebarCollapseButton"]:hover {
        background-color: rgb(226, 232, 240) !important;
        border-color: rgb(186, 199, 216) !important;
    }
    button[data-testid="stExpandSidebarButton"]:focus-visible,
    button[data-testid="stSidebarCollapseButton"]:focus-visible {
        box-shadow: rgb(255, 255, 255) 0 0 0 2px, rgb(148, 163, 184) 0 0 0 4px !important;
    }
    .stMain .block-container {
        padding-top: 1.25rem !important;
    }
</style>
"""

# Arborescence file rows: ellipsis follows the real column width (server-side code cannot read button px).
_ARB_FILE_TREE_FILE_BTN_CSS = """
<style>
    div.st-key-gcs_arborescence_tree div[data-testid="stColumn"]:has([class*="st-key-pick_file_"]),
    div.st-key-gcs_arborescence_tree div[data-testid="column"]:has([class*="st-key-pick_file_"]) {
        min-width: 0 !important;
    }
    div.st-key-gcs_arborescence_tree div[class*="st-key-pick_file_"] {
        min-width: 0;
        width: 100%;
    }
    div.st-key-gcs_arborescence_tree div[class*="st-key-pick_file_"] button {
        width: 100%;
        max-width: 100%;
        min-width: 0;
    }
    div.st-key-gcs_arborescence_tree div[class*="st-key-pick_file_"] button p {
        display: block;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        max-width: 100%;
        min-width: 0;
    }
</style>
"""


def _safe_widget_key(path_str: str, prefix: str) -> str:
    h = hashlib.sha256(path_str.encode()).hexdigest()[:24]
    return f"{prefix}_{h}"


_GCS_TREE_POPOVER_EPOCH = "_gcs_tree_popover_epoch"

# Folder menu: ``st.popover`` inside the fixed-height arborescence panel was clipped; use ``st.dialog``.
_SESSION_FOLDER_MENU_REL = "_gcs_folder_menu_rel"
_SESSION_TREE_ROOT = "_gcs_tree_root"
_SESSION_FOLDER_MENU_BUCKET = "_gcs_folder_menu_bucket"
_SESSION_FOLDER_MENU_USER_PREFIX = "_gcs_folder_menu_user_prefix"
_SESSION_KW_LIST_MENU_OPEN = "_kw_list_menu_open"


def _gcs_bump_tree_popover_epoch() -> None:
    """Remount folder popovers with a new widget key so they render closed after an action."""
    st.session_state[_GCS_TREE_POPOVER_EPOCH] = int(st.session_state.get(_GCS_TREE_POPOVER_EPOCH, 0)) + 1


_KW_LISTS_POPOVER_EPOCH = "_kw_lists_popover_epoch"


def _bump_kw_lists_popover_epoch() -> None:
    """Remount the keyword-list name button after a menu action (new widget key)."""
    st.session_state[_KW_LISTS_POPOVER_EPOCH] = int(st.session_state.get(_KW_LISTS_POPOVER_EPOCH, 0)) + 1


def _dismiss_kw_list_menu() -> None:
    st.session_state.pop(_SESSION_KW_LIST_MENU_OPEN, None)
    st.session_state.pop("kw_list_rename_slug", None)
    st.session_state.pop("kw_list_show_create", None)


def _close_kw_list_menu_after_action() -> None:
    """Close the list-management dialog and remount the trigger button (same idea as the arborescence ⋮ menu)."""
    _dismiss_kw_list_menu()
    _bump_kw_lists_popover_epoch()


# Une couleur de fond par note −5…+5 (index = grade + 5), rouge → gris neutre → vert.
_POSITIVITY_CHIP_BG: tuple[str, ...] = (
    "#5c1010",  # −5
    "#7b241c",  # −4
    "#a93226",  # −3
    "#cb4335",  # −2
    "#e59866",  # −1
    "#aeb6bf",  # 0 neutre
    "#7dcea0",  # +1
    "#52be80",  # +2
    "#28b463",  # +3
    "#1e8449",  # +4
    "#145a32",  # +5
)


def _positivity_chip_colors(grade: int) -> tuple[str, str]:
    """Fond et texte pour une pastille : teinte fixe par note −5 à +5 (rouge → gris → vert)."""
    p = max(-5, min(5, int(grade)))
    bg = _POSITIVITY_CHIP_BG[p + 5]
    hx = bg.removeprefix("#")
    rr = int(hx[0:2], 16)
    gg = int(hx[2:4], 16)
    bb = int(hx[4:6], 16)
    lum = (0.299 * rr + 0.587 * gg + 0.114 * bb) / 255
    fg = "#1a1a1a" if lum > 0.52 else "#ffffff"
    return bg, fg


def _empty_ranked_row(folder_key: str) -> RankedFolderMatch:
    """Placeholder row for a file path with no keyword hits."""
    return RankedFolderMatch(
        folder_key=folder_key,
        keyword_hits=(),
        total_occurrences=0,
        distinct_match_count=0,
        weighted_rank_avg=0.0,
        grade_sum=0,
    )


def _matches_to_excel_bytes(ranked: list[RankedFolderMatch], columns: list[KeywordEntry]) -> bytes:
    """Sheet 'Analyse': fichiers × counts; sheet 'Mots-clés': keyword ↔ grade (sorted by grade ↓)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Analyse"
    headers = [e.text for e in columns]
    ws.append(["Fichier", *headers])
    ws.append(["Note (−5 à +5)", *[str(e.positivity) for e in columns]])
    for row in ranked:
        hits = dict(row.keyword_hits)
        ws.append([row.folder_key] + [hits.get(kw, 0) for kw in headers])

    ws_kw = wb.create_sheet("Mots-clés — positivité", 1)
    ws_kw.append(["Mot-clé", "Note (−5 à +5)"])
    for e in sorted(columns, key=lambda x: (-x.positivity, x.text.lower())):
        ws_kw.append([e.text, e.positivity])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@st.cache_resource
def _gcs_client_app():
    from google.cloud import storage

    return storage.Client()


def _secrets_gcp() -> dict:
    try:
        raw = st.secrets.get("gcp", {})
        return dict(raw) if raw is not None else {}
    except Exception:
        return {}


def _gcs_bucket_name() -> str:
    name = str(_secrets_gcp().get("bucket_name", "") or "").strip()
    if name:
        return name
    return str(os.environ.get("GCS_BUCKET_NAME", "") or "").strip()


def _user_storage_prefix() -> str | None:
    """Stable per-user prefix: OIDC ``sub`` slug, else SHA-256 of email."""
    try:
        info = dict(st.user)
    except Exception:
        return None
    sub = info.get("sub")
    if sub:
        seg = re.sub(r"[^a-zA-Z0-9._@-]", "_", str(sub).strip())[:128]
        return seg if seg else None
    email = info.get("email")
    if email:
        digest = hashlib.sha256(str(email).strip().lower().encode("utf-8")).hexdigest()
        return f"h_{digest}"
    return None


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("dossier-analyzer")
    except Exception:
        pass
    try:
        pyproject = Path(__file__).resolve().parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        ver = (data.get("project") or {}).get("version")
        if isinstance(ver, str) and ver.strip():
            return ver.strip()
    except Exception:
        pass
    return "dev"


def _render_title_with_version() -> None:
    v = escape(_app_version())
    left, right = st.columns([5, 1], gap="small")
    with left:
        st.title("Dossier Analyzer")
    with right:
        st.markdown(
            f'<p style="text-align:right;margin:0;padding-top:1rem;font-size:0.7rem;line-height:1.2;'
            f'color:rgba(49,51,63,0.5);">v{v}</p>',
            unsafe_allow_html=True,
        )


def _render_login_gate() -> None:
    _render_title_with_version()
    st.header("Connexion requise")
    st.caption("Connectez-vous pour accéder à vos dossiers dans le stockage cloud.")
    st.button("Se connecter avec Google", on_click=st.login, type="primary")


@st.cache_data(show_spinner="Indexation des fichiers (cloud)…")
def file_text_index_gcs(
    cache_version: str, bucket: str, user_prefix: str, fingerprint: str
) -> dict[str, str]:
    """Relative object path (posix) → extracted text for supported types."""
    del fingerprint  # part of cache key only
    client = _gcs_client_app()
    entries = list_user_blob_entries(client, bucket, user_prefix)
    b = client.bucket(bucket)
    corpus: dict[str, str] = {}
    for e in entries:
        rel = str(e["rel"])
        if Path(rel).name == DOSSIER_FOLDER_PLACEHOLDER:
            continue
        blob = b.blob(str(e["object_name"]))
        try:
            data = blob.download_as_bytes()
        except Exception:
            continue
        if len(data) > _MAX_GCS_INDEX_BYTES:
            continue
        corpus[rel] = extract_text_from_bytes(
            data, Path(rel).suffix, Path(rel).name
        )
    return corpus


def _default_keyword_seed_rows() -> list[dict]:
    """Liste initiale (nouveau compte / premier CSV cloud) : exemples sur l’échelle −5…+5 (0 = neutre)."""
    return [
        {"id": uuid.uuid4().hex[:10], "text": "Excellent niveau", "positivity": 5},
        {"id": uuid.uuid4().hex[:10], "text": "Bon niveau", "positivity": 2},
        {"id": uuid.uuid4().hex[:10], "text": "Irrégulier", "positivity": -2},
    ]


def _ensure_session() -> None:
    if "browse_root" not in st.session_state:
        st.session_state.browse_root = None
    if "selected_file" not in st.session_state:
        st.session_state.selected_file = None
    if "kw_active_slug" not in st.session_state:
        st.session_state.kw_active_slug = DEFAULT_LIST_SLUG
    if "kw_rows" not in st.session_state:
        st.session_state.kw_rows = _default_keyword_seed_rows()
    for r in st.session_state.kw_rows:
        if "positivity" not in r:
            r["positivity"] = 0


def _apply_keywords_csv_to_session(raw: bytes) -> tuple[bool, str]:
    """Remplace les mots-clés par le contenu d’un CSV type data/keywords.csv (mot, note −5 à +5)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, "Encodage non reconnu : utilisez UTF-8."

    f = io.StringIO(text)
    reader = csv.DictReader(f)
    if reader.fieldnames is None:
        return False, "Fichier CSV vide ou sans en-tête."

    cols = [c.strip() for c in reader.fieldnames if c and str(c).strip()]
    if not cols:
        return False, "Aucune colonne dans le CSV."

    lower_map = {c.lower(): c for c in cols}
    word_col: str | None = None
    for cand in ("word", "keyword", "mot", "mot-clé", "mot_cle", "texte"):
        if cand in lower_map:
            word_col = lower_map[cand]
            break
    if word_col is None:
        word_col = cols[0]

    grade_col: str | None = None
    for cand in ("grade", "positivity", "positif", "note", "niveau"):
        if cand in lower_map:
            grade_col = lower_map[cand]
            break
    if grade_col is None:
        grade_col = cols[1] if len(cols) >= 2 else None
    if grade_col is None:
        return False, "Deux colonnes attendues : mot-clé et note (−5 à +5)."

    new_rows: list[dict] = []
    for row in reader:
        w = str(row.get(word_col, "") or "").strip()
        if not w:
            continue
        raw_g = str(row.get(grade_col, "") or "").strip().replace(",", ".")
        try:
            g = int(float(raw_g)) if raw_g else 0
        except ValueError:
            g = 0
        g = max(-5, min(5, g))
        new_rows.append({"id": uuid.uuid4().hex[:10], "text": w, "positivity": g})

    for r in st.session_state.kw_rows:
        rid = r["id"]
        st.session_state.pop(f"kw_{rid}", None)
        st.session_state.pop(f"kw_pos_{rid}", None)
        st.session_state.pop(f"del_{rid}", None)

    st.session_state.kw_rows = new_rows
    if not new_rows:
        return True, "Liste vide (aucun mot-clé)."
    return True, f"{len(new_rows)} mot(s)-clé chargé(s)."


def _export_current_keywords_csv_bytes() -> bytes:
    """Exporte la liste active (widgets + ``kw_rows``) au même format que ``data/keywords.csv``."""
    _sync_kw_rows_from_widget_session_state()
    rows = _keyword_rows_snapshot_for_upload()
    return _kw_rows_to_csv_bytes(rows)


def _kw_rows_to_csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["word", "grade"])
    for r in rows:
        text = str(r.get("text", "") or "").strip()
        if not text:
            continue
        try:
            g = int(r.get("positivity", 0))
        except (TypeError, ValueError):
            g = 0
        g = max(-5, min(5, g))
        writer.writerow([text, g])
    return buf.getvalue().encode("utf-8-sig")


def _kw_digest_from_rows(rows: list[dict]) -> str:
    return hashlib.sha256(_kw_rows_to_csv_bytes(rows)).hexdigest()


def _sync_kw_rows_from_widget_session_state() -> None:
    """Aligne ``kw_rows`` sur les clés de widgets (utile avant sauvegarde : le menu s’affiche avant les champs)."""
    for row in st.session_state.kw_rows:
        wk = f"kw_{row['id']}"
        pk = f"kw_pos_{row['id']}"
        if wk in st.session_state:
            row["text"] = str(st.session_state[wk] or "")
        if pk in st.session_state:
            try:
                row["positivity"] = int(st.session_state[pk])
            except (TypeError, ValueError):
                row["positivity"] = 0


def _keyword_rows_snapshot_for_upload() -> list[dict]:
    """Latest keyword table from widgets + ``kw_rows`` ids (for autosync without a full script run)."""
    out: list[dict] = []
    for r in st.session_state.kw_rows:
        rid = r["id"]
        wk = f"kw_{rid}"
        pk = f"kw_pos_{rid}"
        text = str(st.session_state.get(wk, r.get("text", "")) or "")
        try:
            pos = int(st.session_state.get(pk, r.get("positivity", 0)))
        except (TypeError, ValueError):
            pos = 0
        pos = max(-5, min(5, pos))
        out.append({"id": rid, "text": text, "positivity": pos})
    return out


def _flush_keyword_list_to_gcs(
    client, bucket: str, user_prefix: str, list_slug: str
) -> None:
    """Envoie le tableau de mots-clés actuel (widgets / ``kw_rows``) vers le CSV ``list_slug``."""
    _sync_kw_rows_from_widget_session_state()
    rows = _keyword_rows_snapshot_for_upload()
    data = _kw_rows_to_csv_bytes(rows)
    upload_keyword_list_csv(client, bucket, user_prefix, list_slug, data)
    st.session_state.kw_last_uploaded_digest = _kw_digest_from_rows(rows)


def _flush_active_keyword_list_to_gcs(client, bucket: str, user_prefix: str) -> None:
    """Persist the active list immediately (e.g. before switching or renaming)."""
    active = str(st.session_state.get("kw_active_slug", DEFAULT_LIST_SLUG))
    _flush_keyword_list_to_gcs(client, bucket, user_prefix, active)


def _load_keyword_list_bytes_into_session(raw: bytes) -> tuple[bool, str]:
    ok, msg = _apply_keywords_csv_to_session(raw)
    if ok:
        rows = list(st.session_state.kw_rows)
        st.session_state.kw_last_uploaded_digest = _kw_digest_from_rows(rows)
        st.session_state.pop("_kw_debounce_digest", None)
        st.session_state.pop("_kw_debounce_start", None)
    return ok, msg


def _kw_ensure_at_least_one_list_in_gcs(
    client, bucket: str, user_prefix: str
) -> list[str]:
    """If GCS has no keyword list CSVs, create ``default`` with seed rows. Return current slugs."""
    slugs = list_keyword_list_slugs(client, bucket, user_prefix)
    if slugs:
        return slugs
    seed = _default_keyword_seed_rows()
    body = _kw_rows_to_csv_bytes(seed)
    upload_keyword_list_csv(client, bucket, user_prefix, DEFAULT_LIST_SLUG, body)
    return [DEFAULT_LIST_SLUG]


def _hydrate_keyword_lists_if_needed(client, bucket: str, user_prefix: str) -> None:
    if st.session_state.get("_kw_hydrated_for") == user_prefix:
        return

    slugs = list_keyword_list_slugs(client, bucket, user_prefix)
    if not slugs:
        slugs = _kw_ensure_at_least_one_list_in_gcs(client, bucket, user_prefix)

    active = str(st.session_state.get("kw_active_slug", DEFAULT_LIST_SLUG))
    if active not in slugs:
        active = slugs[0]
    st.session_state.kw_active_slug = active

    raw = download_keyword_list_csv(client, bucket, user_prefix, active)
    if raw is None:
        raw = _kw_rows_to_csv_bytes(_default_keyword_seed_rows())
    ok, _msg = _load_keyword_list_bytes_into_session(raw)
    if not ok:
        st.session_state.kw_rows = _default_keyword_seed_rows()
        st.session_state.kw_last_uploaded_digest = _kw_digest_from_rows(st.session_state.kw_rows)

    st.session_state["_kw_hydrated_for"] = user_prefix


def _kw_list_slug_key_suffix(slug: str) -> str:
    """Safe suffix for Streamlit widget keys derived from a list slug."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", slug).strip("_")
    return s or "slug"


def _kw_list_try_switch(
    client,
    bucket: str,
    user_prefix: str,
    from_slug: str,
    to_slug: str,
) -> None:
    """Load another keyword list from GCS into the session."""
    try:
        _flush_keyword_list_to_gcs(client, bucket, user_prefix, from_slug)
    except Exception as ex:
        st.session_state["_kw_list_toolbar_error"] = f"Enregistrement impossible : {ex}"
        st.rerun()
    raw = download_keyword_list_csv(client, bucket, user_prefix, to_slug)
    if raw is None:
        st.session_state["_kw_list_toolbar_error"] = "Liste introuvable dans le cloud."
        st.rerun()
    ok, msg = _load_keyword_list_bytes_into_session(raw)
    if not ok:
        st.session_state["_kw_list_toolbar_error"] = msg
        st.rerun()
    st.session_state.kw_active_slug = to_slug
    _close_kw_list_menu_after_action()
    st.rerun()


def _kw_list_try_delete(
    client,
    bucket: str,
    user_prefix: str,
    slugs: list[str],
    del_pick: str,
) -> None:
    """Remove a keyword list CSV from GCS and adjust active list if needed.

    If the last list is removed, a new ``default`` list with seed keywords is created in GCS.
    """
    try:
        active_slug = str(st.session_state.get("kw_active_slug", DEFAULT_LIST_SLUG))
        if del_pick == active_slug:
            others = [s for s in slugs if s != del_pick]
            if others:
                fallback = (
                    DEFAULT_LIST_SLUG
                    if DEFAULT_LIST_SLUG in others
                    else others[0]
                )
                _flush_active_keyword_list_to_gcs(client, bucket, user_prefix)
                raw_fb = download_keyword_list_csv(client, bucket, user_prefix, fallback)
                if raw_fb is None:
                    raise RuntimeError("Impossible de charger une liste de secours.")
                ok, msg = _load_keyword_list_bytes_into_session(raw_fb)
                if not ok:
                    raise RuntimeError(msg)
                st.session_state.kw_active_slug = fallback
            else:
                _flush_active_keyword_list_to_gcs(client, bucket, user_prefix)

        delete_keyword_list_csv(client, bucket, user_prefix, del_pick)

        remaining = list_keyword_list_slugs(client, bucket, user_prefix)
        if not remaining:
            remaining = _kw_ensure_at_least_one_list_in_gcs(client, bucket, user_prefix)
            load_slug = DEFAULT_LIST_SLUG if DEFAULT_LIST_SLUG in remaining else remaining[0]
            raw = download_keyword_list_csv(client, bucket, user_prefix, load_slug)
            if raw is None:
                raw = _kw_rows_to_csv_bytes(_default_keyword_seed_rows())
            ok, msg = _load_keyword_list_bytes_into_session(raw)
            if not ok:
                st.session_state.kw_rows = _default_keyword_seed_rows()
                st.session_state.kw_last_uploaded_digest = _kw_digest_from_rows(
                    st.session_state.kw_rows
                )
            st.session_state.kw_active_slug = load_slug
        st.session_state["_kw_list_toolbar_info"] = (
            f"Liste « {format_keyword_list_label(del_pick)} » supprimée."
        )
        _close_kw_list_menu_after_action()
        st.rerun()
    except Exception as ex:
        st.session_state["_kw_list_toolbar_error"] = str(ex)
        st.rerun()


def _render_keyword_list_menu_content(client, bucket: str, user_prefix: str) -> None:
    """Body of the keyword-list dialog: load / create / rename / delete keyword lists (GCS)."""
    slugs = list_keyword_list_slugs(client, bucket, user_prefix)
    slug_being_edited = str(st.session_state.get("kw_active_slug", DEFAULT_LIST_SLUG))
    if slug_being_edited not in slugs and slugs:
        slug_being_edited = slugs[0]
        st.session_state.kw_active_slug = slug_being_edited

    if slug_being_edited in slugs:
        others_sorted = sorted([s for s in slugs if s != slug_being_edited], key=str.lower)
        display_slugs = [slug_being_edited] + others_sorted
    else:
        display_slugs = sorted(slugs, key=str.lower)

    h_left, h_right = st.columns([3, 1.2], gap="small")
    with h_left:
        st.caption("Enregistrement automatique toutes les 10 secondes.")
    with h_right:
        st.caption(
            f"Listes : **{len(slugs)}** / {MAX_LISTS_PER_USER} max",
            help=(
                f"Nombre de listes actuellement initialisées et limite"
                f" maximale ({MAX_LISTS_PER_USER} par utilisateur)."
            ),
        )

    rename_target = st.session_state.get("kw_list_rename_slug")
    show_create = bool(st.session_state.get("kw_list_show_create"))

    for slug in display_slugs:
        sk = _kw_list_slug_key_suffix(slug)
        is_active = slug == slug_being_edited
        c_pill, c_ren, c_del = st.columns([3.2, 1.35, 0.7], gap="small")
        with c_pill:
            label = format_keyword_list_label(slug)
            if st.button(
                label,
                key=f"kw_dlg_pill_{sk}",
                type="primary" if is_active else "secondary",
                disabled=is_active,
                width="stretch",
                help="Liste chargée" if is_active else "Charger cette liste",
            ):
                if not is_active:
                    _kw_list_try_switch(client, bucket, user_prefix, slug_being_edited, slug)
        with c_ren:
            if st.button(
                "Renommer",
                key=f"kw_dlg_rename_{sk}",
                type="secondary",
                disabled=rename_target == slug,
                width="stretch",
                help="Saisie du nom ci-dessous." if rename_target == slug else None,
            ):
                st.session_state["kw_list_rename_slug"] = slug
                st.session_state["kw_list_show_create"] = False
                st.rerun()
        with c_del:
            if st.button(
                "🗑️",
                key=f"kw_dlg_del_{sk}",
                type="secondary",
                width="stretch",
                help="Supprimer cette liste",
            ):
                _kw_list_try_delete(client, bucket, user_prefix, slugs, slug)

        if rename_target == slug:
            ren_to_raw = st.text_input(
                "Nouveau nom",
                key="kw_rename_to_input",
                placeholder="nouveau_nom",
                help="Lettres minuscules, chiffres, espaces, points, tirets, underscores.",
            )
            r1, r2, _ = st.columns([1, 1, 2])
            with r1:
                if st.button("Enregistrer", key="kw_rename_save_btn", type="primary", width="stretch"):
                    try:
                        new_s = sanitize_keyword_list_slug(ren_to_raw)
                    except ValueError as ex:
                        st.session_state["_kw_list_toolbar_error"] = str(ex)
                        st.rerun()
                    if new_s == slug:
                        st.session_state["_kw_list_toolbar_error"] = "Le nom est identique."
                        st.rerun()
                    if new_s in slugs:
                        st.session_state["_kw_list_toolbar_error"] = "Ce nom est déjà utilisé."
                        st.rerun()
                    try:
                        if slug == str(st.session_state.get("kw_active_slug")):
                            _flush_active_keyword_list_to_gcs(client, bucket, user_prefix)
                        rename_keyword_list_csv(client, bucket, user_prefix, slug, new_s)
                        if slug == str(st.session_state.get("kw_active_slug")):
                            st.session_state.kw_active_slug = new_s
                        st.session_state.pop("kw_list_rename_slug", None)
                        st.session_state["_kw_list_toolbar_info"] = (
                            f"Liste renommée : « {format_keyword_list_label(slug)} » → "
                            f"« {format_keyword_list_label(new_s)} »."
                        )
                        _close_kw_list_menu_after_action()
                        st.rerun()
                    except Exception as ex:
                        st.session_state["_kw_list_toolbar_error"] = str(ex)
                        st.rerun()
            with r2:
                if st.button("Annuler", key="kw_rename_cancel_btn", width="stretch"):
                    st.session_state.pop("kw_list_rename_slug", None)
                    st.rerun()

    if show_create:
        new_slug_raw = st.text_input(
            "Nom de la nouvelle liste",
            key="kw_new_list_slug_input",
            placeholder="ex. contrat_2024",
            help="Lettres minuscules, chiffres, espaces, points, tirets, underscores.",
        )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Créer cette liste", key="kw_create_list_btn", type="primary", width="stretch"):
                if len(slugs) >= MAX_LISTS_PER_USER:
                    st.session_state["_kw_list_toolbar_error"] = (
                        f"Limite de {MAX_LISTS_PER_USER} listes atteinte. Supprimez une liste avant d’en créer une autre."
                    )
                    st.rerun()
                try:
                    new_s = sanitize_keyword_list_slug(new_slug_raw)
                except ValueError as ex:
                    st.session_state["_kw_list_toolbar_error"] = str(ex)
                    st.rerun()
                if new_s in slugs:
                    st.session_state["_kw_list_toolbar_error"] = "Une liste porte déjà ce nom."
                    st.rerun()
                try:
                    _flush_active_keyword_list_to_gcs(client, bucket, user_prefix)
                    empty_csv = _kw_rows_to_csv_bytes([])
                    upload_keyword_list_csv(client, bucket, user_prefix, new_s, empty_csv)
                    ok, msg = _load_keyword_list_bytes_into_session(empty_csv)
                    if not ok:
                        st.session_state["_kw_list_toolbar_error"] = msg
                        st.rerun()
                    st.session_state.kw_active_slug = new_s
                    st.session_state["kw_list_show_create"] = False
                    st.session_state["_kw_list_toolbar_info"] = (
                        f"Liste « {format_keyword_list_label(new_s)} » créée et chargée."
                    )
                    _close_kw_list_menu_after_action()
                    st.rerun()
                except Exception as ex:
                    st.session_state["_kw_list_toolbar_error"] = str(ex)
                    st.rerun()
        with b2:
            if st.button("Annuler", key="kw_create_cancel_btn", width="stretch"):
                st.session_state["kw_list_show_create"] = False
                st.rerun()

    cta_col, _ = st.columns([1.25, 2])
    with cta_col:
        at_limit = len(slugs) >= MAX_LISTS_PER_USER
        if st.button(
            "+ nouvelle liste",
            key="kw_list_new_list_btn",
            type="primary",
            disabled=at_limit,
            width="stretch",
            help=(
                f"Jusqu’à {MAX_LISTS_PER_USER} listes par compte."
                if not at_limit
                else f"Limite de {MAX_LISTS_PER_USER} listes atteinte."
            ),
        ):
            st.session_state["kw_list_show_create"] = not show_create
            st.session_state.pop("kw_list_rename_slug", None)
            st.rerun()


@st.fragment(run_every=10)
def _keyword_list_gcs_autosync_fragment() -> None:
    if not bool(getattr(st.user, "is_logged_in", False)):
        return
    bucket = _gcs_bucket_name()
    user_prefix = _user_storage_prefix()
    if not bucket or not user_prefix:
        return
    if st.session_state.get("_kw_hydrated_for") != user_prefix:
        return
    client = _gcs_client_app()
    active = str(st.session_state.get("kw_active_slug", DEFAULT_LIST_SLUG))
    rows = _keyword_rows_snapshot_for_upload()
    csv_bytes = _kw_rows_to_csv_bytes(rows)
    digest = hashlib.sha256(csv_bytes).hexdigest()
    if digest == st.session_state.get("kw_last_uploaded_digest"):
        return
    try:
        upload_keyword_list_csv(client, bucket, user_prefix, active, csv_bytes)
        st.session_state.kw_last_uploaded_digest = digest
    except Exception as ex:
        st.session_state["_kw_autosync_error"] = str(ex)


def _init_kw_row_widgets() -> None:
    for row in st.session_state.kw_rows:
        wk = f"kw_{row['id']}"
        pk = f"kw_pos_{row['id']}"
        if wk not in st.session_state:
            st.session_state[wk] = row["text"]
        if pk not in st.session_state:
            st.session_state[pk] = int(row.get("positivity", 0))


def _make_persist_keyword(row_id: str):
    def _persist() -> None:
        key = f"kw_{row_id}"
        val = str(st.session_state.get(key, "") or "")
        for r in st.session_state.kw_rows:
            if r["id"] == row_id:
                r["text"] = val
                break

    return _persist


def _make_persist_positivity(row_id: str):
    def _persist() -> None:
        pk = f"kw_pos_{row_id}"
        val = int(st.session_state.get(pk, 0))
        for r in st.session_state.kw_rows:
            if r["id"] == row_id:
                r["positivity"] = val
                break

    return _persist


def _render_keyword_inputs(client, bucket: str, user_prefix: str) -> list[KeywordEntry]:
    err = st.session_state.pop("_kw_list_toolbar_error", None)
    if err:
        st.warning(err)
    info = st.session_state.pop("_kw_list_toolbar_info", None)
    if info:
        st.success(info)
    sync_err = st.session_state.pop("_kw_autosync_error", None)
    if sync_err:
        st.warning(f"Synchronisation cloud (10 s) : {sync_err}")

    slug = str(st.session_state.get("kw_active_slug", DEFAULT_LIST_SLUG))
    body_col, csv_col = st.columns([4.25, 1], gap="medium", vertical_alignment="top")
    remove_id: str | None = None
    with body_col:
        meta_col, desc_col = st.columns([3.6, 8.4], vertical_alignment="center", gap="small")
        with meta_col:
            kw_btn_epoch = int(st.session_state.get(_KW_LISTS_POPOVER_EPOCH, 0))
            if st.button(
                format_keyword_list_label(slug) + "  ⏷",
                key=f"kw_list_name_btn_{kw_btn_epoch}",
                type="primary",
                width="stretch",
                help="Ouvrir la gestion des listes de mots-clés",
            ):
                st.session_state[_SESSION_KW_LIST_MENU_OPEN] = True
        with desc_col:
            st.caption(
                "Saisie dynamique : "
                "Recherche insensible à la casse, sous-chaîne dans le texte de chaque fichier (PDF, Markdown, nom pour les images). "
                "Tri Analyse : le curseur Note (−5 à +5) pondère le classement (−5 = très défavorable, +5 = très favorable). "
            )

        _init_kw_row_widgets()
        rows = list(st.session_state.kw_rows)

        for row in rows:
            c0, c1, c2, c3 = st.columns([0.04, 3.4, 1.4, 0.55], gap="small")
            with c0:
                st.markdown(
                    "<div style='background:#c0392b;width:4px;height:2.6rem;border-radius:3px;margin-top:2px'></div>",
                    unsafe_allow_html=True,
                )
            with c1:
                st.text_input(
                    "Mot-clé",
                    key=f"kw_{row['id']}",
                    placeholder="entrer mot clé",
                    label_visibility="collapsed",
                    on_change=_make_persist_keyword(row["id"]),
                )
            with c2:
                st.slider(
                    "Note",
                    min_value=-5,
                    max_value=5,
                    step=1,
                    key=f"kw_pos_{row['id']}",
                    help="−5 = très défavorable … +5 = très favorable — utilisé pour trier les fichiers correspondants",
                    label_visibility="collapsed",
                    on_change=_make_persist_positivity(row["id"]),
                )
            with c3:
                st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
                if st.button(
                    "🗑️",
                    key=f"del_{row['id']}",
                    help="Supprimer",
                ):
                    remove_id = row["id"]

        c_add, _ = st.columns([1, 3])
        with c_add:
            if st.button("➕ Ajouter mot-clé", width="stretch"):
                new_id = uuid.uuid4().hex[:10]
                st.session_state.kw_rows.append({"id": new_id, "text": "", "positivity": 0})
                st.rerun()

    with csv_col:
        st.markdown(_KW_CSV_EXPORT_BTN_CSS, unsafe_allow_html=True)
        active_kw_slug = str(st.session_state.get("kw_active_slug", DEFAULT_LIST_SLUG))
        export_name = f"keywords_{active_kw_slug}.csv"
        st.download_button(
            "Exporter vers CSV",
            data=_export_current_keywords_csv_bytes(),
            file_name=export_name,
            mime="text/csv; charset=utf-8",
            key="kw_csv_export_btn",
            use_container_width=True,
            width=200,
            help="Même format que data/keywords.csv (colonnes word, grade).",
        )
        uploaded_kw_csv = st.file_uploader(
            "Charger mots-clés depuis CSV",
            type=["csv"],
            key="kw_csv_uploader",
            help="Remplace la liste active. Format du CSV: 2 colonnes 'word' et 'grade' (note −5 à +5), première ligne pour les en-têtes, pas de ligne vide, ',' comme séparateur.",
            label_visibility="visible",
            width=200,
        )
        if uploaded_kw_csv is not None:
            body = uploaded_kw_csv.getvalue()
            digest = hashlib.sha256(body).hexdigest()
            if st.session_state.get("_kw_csv_import_hash") != digest:
                ok_csv, msg_csv = _apply_keywords_csv_to_session(body)
                st.session_state["_kw_csv_import_hash"] = digest
                if ok_csv:
                    try:
                        _flush_active_keyword_list_to_gcs(client, bucket, user_prefix)
                    except Exception as ex:
                        st.session_state["_kw_autosync_error"] = str(ex)
                    st.rerun()
                else:
                    st.warning(msg_csv)

    if remove_id is not None:
        st.session_state.kw_rows = [r for r in st.session_state.kw_rows if r["id"] != remove_id]
        st.session_state.pop(f"kw_{remove_id}", None)
        st.session_state.pop(f"kw_pos_{remove_id}", None)
        st.session_state.pop(f"del_{remove_id}", None)
        st.rerun()

    for row in st.session_state.kw_rows:
        wk = f"kw_{row['id']}"
        pk = f"kw_pos_{row['id']}"
        if wk in st.session_state:
            row["text"] = str(st.session_state[wk] or "")
        if pk in st.session_state:
            row["positivity"] = int(st.session_state[pk])

    return [
        KeywordEntry(
            str(st.session_state.get(f"kw_{r['id']}", "") or ""),
            int(st.session_state.get(f"kw_pos_{r['id']}", r.get("positivity", 0))),
        )
        for r in st.session_state.kw_rows
    ]


def _render_match_cards(corpus: dict[str, str], entries: list[KeywordEntry]) -> None:
    kws_norm = normalize_keyword_entries(entries)
    if not kws_norm:
        st.markdown("##### Fichiers correspondants")
        st.caption("Entrez au moins un mot-clé non vide ci-dessus.")
        return

    ranked = ranked_folder_matches(corpus, entries)

    head_l, head_c, head_r = st.columns([2.8, 2.2, 1], vertical_alignment="center")
    with head_l:
        st.markdown("##### Fichiers correspondants")
    with head_c:
        add_unmatched = st.checkbox(
            "Ajouter les fichiers sans correspondance",
            help="Inclure tous les fichiers indexés, même ceux sans aucune correspondance aux mots-clés (valeurs à zéro dans l’export).",
            key="add_unmatched_files",
        )

    matched_keys = {r.folder_key for r in ranked}
    if add_unmatched:
        extra_keys = sorted((k for k in corpus if k not in matched_keys), key=str.lower)
        display_rows: list[RankedFolderMatch] = list(ranked) + [
            _empty_ranked_row(k) for k in extra_keys
        ]
    else:
        display_rows = list(ranked)

    if display_rows:
        st.markdown(_EXCEL_EXPORT_BTN_CSS, unsafe_allow_html=True)
    with head_r:
        if display_rows:
            st.download_button(
                "Exporter vers Excel",
                data=_matches_to_excel_bytes(display_rows, kws_norm),
                file_name="analyse_fichiers.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="secondary",
                width="stretch",
                key="export_matches_xlsx",
            )

    if not display_rows:
        if not corpus:
            st.info("Aucun fichier indexé sous cette racine (PDF, Markdown, images prises en charge).")
        else:
            st.info("Aucun fichier ne contient ces mots-clés (recherche insensible à la casse).")
        return

    pos_by_kw = {e.text: e.positivity for e in kws_norm}

    for row in display_rows:
        fk = row.folder_key
        if row.keyword_hits:
            chip_parts: list[str] = []
            for kw, cnt in row.keyword_hits:
                bg, fg = _positivity_chip_colors(pos_by_kw.get(kw, 0))
                chip_parts.append(
                    f"<span style='display:inline-block;margin:2px 6px 2px 0;padding:2px 10px;"
                    f"background:{bg};color:{fg};border-radius:999px;font-size:0.9em;"
                    f"border:1px solid rgba(0,0,0,0.08);'>"
                    f"{escape(kw)} <strong>({cnt})</strong></span>"
                )
            chips = "".join(chip_parts)
        else:
            chips = (
                "<span style='display:inline-block;color:#7f8c8d;font-size:0.9em;font-style:italic;'>"
                "Aucune correspondance</span>"
            )
        badge = (
            f"<span style='display:inline-block;background:#1f77b4;color:white;padding:6px 14px;"
            f"border-radius:8px;font-weight:600;margin-right:10px;'>{escape(fk)}</span>"
        )
        st.markdown(
            f"<div style='margin:12px 0;padding:12px 14px;border:1px solid #dfe6e9;border-radius:10px;"
            f"background:#fafbfc;'>{badge}{chips}</div>",
            unsafe_allow_html=True,
        )


def _gcs_invalidate_index() -> None:
    file_text_index_gcs.clear()


def _gcs_arborescence_dialogs(bucket: str, user_prefix: str) -> None:
    client = _gcs_client_app()
    pending_file = st.session_state.get("_gcs_dlg_delete_file")
    if pending_file:

        @st.dialog("🗑️ Supprimer le fichier ?")
        def _confirm_file_delete() -> None:
            st.caption("Cette action est irréversible.")
            st.code(Path(str(pending_file)).name)
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Confirmer", type="primary", key="gcs_del_file_ok"):
                    try:
                        safe_delete_blob(client, bucket, str(pending_file), user_prefix)
                        st.session_state.pop("_gcs_dlg_delete_file", None)
                        pm = st.session_state.get("_gcs_path_to_object", {})
                        sel = st.session_state.get("selected_file")
                        if sel and pm.get(sel) == pending_file:
                            st.session_state.selected_file = None
                        _gcs_invalidate_index()
                        st.rerun()
                    except Exception as ex:
                        st.error(str(ex))
            with c2:
                if st.button("Annuler", key="gcs_del_file_cancel"):
                    st.session_state.pop("_gcs_dlg_delete_file", None)
                    st.rerun()

        _confirm_file_delete()
        return

    pending_folder = st.session_state.get("_gcs_dlg_delete_folder_rel")
    if pending_folder is not None and str(pending_folder) not in (".", ""):

        @st.dialog("🗑️ Supprimer le dossier ?")
        def _confirm_folder_delete() -> None:
            st.warning("Tous les fichiers et sous-dossiers de ce dossier seront supprimés.")
            st.code(str(pending_folder))
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Confirmer", type="primary", key="gcs_del_fol_ok"):
                    try:
                        safe_delete_folder_prefix(
                            client,
                            bucket,
                            user_prefix,
                            Path(str(pending_folder)),
                        )
                        st.session_state.pop("_gcs_dlg_delete_folder_rel", None)
                        st.session_state.selected_file = None
                        _gcs_invalidate_index()
                        _gcs_bump_tree_popover_epoch()
                        st.rerun()
                    except Exception as ex:
                        st.error(str(ex))
            with c2:
                if st.button("Annuler", key="gcs_del_fol_cancel"):
                    st.session_state.pop("_gcs_dlg_delete_folder_rel", None)
                    st.rerun()

        _confirm_folder_delete()


def _find_tree_node_by_rel(root: TreeNode, rel_posix: str) -> TreeNode | None:
    if root.rel.as_posix() == rel_posix:
        return root
    for ch in root.children:
        found = _find_tree_node_by_rel(ch, rel_posix)
        if found is not None:
            return found
    return None


def _dismiss_folder_menu() -> None:
    st.session_state.pop(_SESSION_FOLDER_MENU_REL, None)


def _render_folder_menu_content(
    folder_node: TreeNode,
    bucket: str,
    user_prefix: str,
    *,
    allow_delete_folder: bool,
) -> None:
    client = _gcs_client_app()
    rel_key = folder_node.rel.as_posix()

    if allow_delete_folder and st.button(
        "🗑️ Supprimer le dossier",
        key=_safe_widget_key(rel_key, "gcs_f_del"),
        width="stretch",
    ):
        st.session_state["_gcs_dlg_delete_folder_rel"] = rel_key
        _dismiss_folder_menu()
        _gcs_bump_tree_popover_epoch()
        st.rerun()

    st.markdown("📝 **Envoyer des fichiers**")
    batch_limit = _gcs_batch_upload_limit_bytes()
    st.caption(
        f"Sélection multiple autorisée — taille totale max : **{batch_limit // (1024 * 1024)} Mo** "
    )
    up = st.file_uploader(
        "Fichiers",
        type=["pdf", "md", "markdown", "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif"],
        accept_multiple_files=True,
        key=_safe_widget_key(rel_key, "gcs_f_up"),
        label_visibility="collapsed",
    )
    if up and st.button(
        "Envoyer vers ce dossier",
        key=_safe_widget_key(rel_key, "gcs_f_up_do"),
        width="stretch",
    ):
        files = list(up)
        total_bytes = sum(len(f.getvalue()) for f in files)
        if total_bytes > batch_limit:
            st.error(
                f"Taille totale ({total_bytes / (1024 * 1024):.1f} Mo) dépasse la limite "
                f"({batch_limit / (1024 * 1024):.0f} Mo). Réduisez la sélection ou augmentez la limite."
            )
        else:
            pref = folder_gcs_prefix(user_prefix, folder_node.rel)
            ok = 0
            err_msgs: list[str] = []
            for f in files:
                try:
                    safe_name = sanitize_upload_filename(f.name)
                    object_name = f"{pref}{safe_name}"
                    ctype, _ = mimetypes.guess_type(safe_name)
                    safe_upload_bytes(
                        client,
                        bucket,
                        object_name,
                        user_prefix,
                        f.getvalue(),
                        ctype,
                    )
                    ok += 1
                except Exception as ex:
                    err_msgs.append(f"{f.name}: {ex}")
            for msg in err_msgs:
                st.warning(msg)
            if ok:
                st.success(f"{ok} fichier(s) envoyé(s).")
                _gcs_invalidate_index()
                _dismiss_folder_menu()
                _gcs_bump_tree_popover_epoch()
                st.rerun()
            elif not err_msgs:
                st.warning("Aucun fichier sélectionné.")

    st.markdown("📁 **Nouveau sous-dossier**")
    sub = st.text_input(
        "Nom",
        key=_safe_widget_key(rel_key, "gcs_f_sub_txt"),
        placeholder="nom-du-dossier",
        label_visibility="collapsed",
    )
    if st.button(
        "📁 Créer le sous-dossier",
        key=_safe_widget_key(rel_key, "gcs_f_sub_btn"),
        width="stretch",
    ):
        if not (sub or "").strip():
            st.warning("Entrez un nom.")
        else:
            try:
                create_subfolder_placeholder(
                    client, bucket, user_prefix, folder_node.rel, sub
                )
                st.success("Dossier créé.")
                _gcs_invalidate_index()
                _dismiss_folder_menu()
                _gcs_bump_tree_popover_epoch()
                st.rerun()
            except Exception as ex:
                st.error(str(ex))


@st.dialog("Actions sur le dossier", on_dismiss=_dismiss_folder_menu, width="medium")
def _folder_menu_dialog() -> None:
    rel = st.session_state.get(_SESSION_FOLDER_MENU_REL)
    root = st.session_state.get(_SESSION_TREE_ROOT)
    bucket = st.session_state.get(_SESSION_FOLDER_MENU_BUCKET)
    user_prefix = st.session_state.get(_SESSION_FOLDER_MENU_USER_PREFIX)
    if not rel or not bucket or not user_prefix or root is None:
        return
    node = _find_tree_node_by_rel(root, rel)
    if node is None:
        return
    _render_folder_menu_content(
        node,
        bucket,
        user_prefix,
        allow_delete_folder=(node.rel != Path(".")),
    )


def _maybe_open_folder_menu_dialog() -> None:
    """Open the folder menu dialog when the user clicked ⋮ (popover was clipped in the scroll panel)."""
    rel = st.session_state.get(_SESSION_FOLDER_MENU_REL)
    if not rel:
        return
    root = st.session_state.get(_SESSION_TREE_ROOT)
    if root is None:
        _dismiss_folder_menu()
        return
    if _find_tree_node_by_rel(root, rel) is None:
        _dismiss_folder_menu()
        return
    _folder_menu_dialog()


@st.dialog("Listes de mots-clés", on_dismiss=_dismiss_kw_list_menu, width="medium")
def _kw_list_menu_dialog() -> None:
    bucket = st.session_state.get(_SESSION_FOLDER_MENU_BUCKET)
    user_prefix = st.session_state.get(_SESSION_FOLDER_MENU_USER_PREFIX)
    if not bucket or not user_prefix:
        return
    client = _gcs_client_app()
    _render_keyword_list_menu_content(client, bucket, user_prefix)


def _maybe_open_kw_list_menu_dialog() -> None:
    """Open the keyword-list dialog when the user clicked the active list name button."""
    if not st.session_state.get(_SESSION_KW_LIST_MENU_OPEN):
        return
    bucket = st.session_state.get(_SESSION_FOLDER_MENU_BUCKET)
    user_prefix = st.session_state.get(_SESSION_FOLDER_MENU_USER_PREFIX)
    if not bucket or not user_prefix:
        _dismiss_kw_list_menu()
        return
    _kw_list_menu_dialog()


def _folder_menu_open_button(folder_node: TreeNode) -> None:
    rel_key = folder_node.rel.as_posix()
    pop_epoch = int(st.session_state.get(_GCS_TREE_POPOVER_EPOCH, 0))
    btn_key = f"gcs_tree_pop_{pop_epoch}_{hashlib.sha256(rel_key.encode()).hexdigest()[:16]}"
    
    button_label = "\u22EE"
    if st.button(button_label, help="Actions sur le dossier", key=btn_key):
        st.session_state[_SESSION_FOLDER_MENU_REL] = rel_key


def _file_tree_file_button_label(fpath: Path) -> tuple[str, str]:
    """Return (button label, full basename for ``st.button(..., help=…)``).

    Truncation to one line is handled in CSS (``_ARB_FILE_TREE_FILE_BTN_CSS``) so it tracks
    the actual button width; Streamlit does not expose viewport or element width on the server.
    """
    name = fpath.name
    if fpath.suffix.lower() == ".pdf":
        prefix = "\U0001f4c4 "
    elif fpath.suffix.lower() in {".md", ".markdown"}:
        prefix = "\U0001f4dd "
    else:
        prefix = "\U0001f5bc "
    return prefix + name, name


def _render_file_tree(
    node: TreeNode,
    bucket: str,
    user_prefix: str,
    path_to_object: dict[str, str],
) -> None:
    if node.rel == Path("."):
        head = st.columns([2, 1])
        with head[0]:
            st.caption("Racine de votre espace")
        with head[1]:
            _folder_menu_open_button(node)

    if node.children:
        for child in node.children:
            hdr_col, menu_col = st.columns([8, 1])
            with hdr_col:
                with st.expander(f"\U0001f4c1 {child.name}", expanded=False):
                    _render_file_tree(child, bucket, user_prefix, path_to_object)
            with menu_col:
                _folder_menu_open_button(child)

    for fpath in node.files:
        label, name_for_help = _file_tree_file_button_label(fpath)
        resolved = str(fpath.resolve())
        is_selected = st.session_state.get("selected_file") == resolved
        key_pick = _safe_widget_key(str(fpath), "pick_file")
        key_bin = _safe_widget_key(str(fpath), "del_file")
        btn_type = "primary" if is_selected else "secondary"
        fc, fb = st.columns([0.82, 0.18])
        with fc:
            if st.button(
                label,
                key=key_pick,
                type=btn_type,
                width="stretch",
                help=name_for_help,
            ):
                st.session_state.selected_file = resolved
                st.rerun()
        with fb:
            if st.button(
                "🗑️",
                key=key_bin,
                width="stretch",
                help="Supprimer ce fichier",
            ):
                oid = path_to_object.get(resolved)
                if oid:
                    st.session_state["_gcs_dlg_delete_file"] = oid
                    st.rerun()




def _show_pdf_bytes(data: bytes) -> None:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        st.error(f"Impossible d’ouvrir le PDF : {e}")
        return
    try:
        for i in range(len(doc)):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            pdata = pix.tobytes("png")
            st.image(io.BytesIO(pdata), width="stretch", caption=f"Page {i + 1} / {len(doc)}")
    finally:
        doc.close()


def _show_markdown(text: str) -> None:
    st.markdown(text)


def _show_image(image_bytes: bytes) -> None:
    try:
        st.image(io.BytesIO(image_bytes), width="stretch")
    except Exception as e:
        st.error(f"Impossible d’afficher l’image : {e}")


def _render_gcs_document_viewer(bucket: str) -> None:
    st.markdown("##### Visualisation du document")
    sel = st.session_state.selected_file
    if not sel:
        st.caption("Sélectionnez un fichier dans l’arborescence.")
        return

    path_map: dict[str, str] = st.session_state.get("_gcs_path_to_object", {})
    object_name = path_map.get(sel)
    if not object_name:
        st.warning("Fichier introuvable.")
        return

    user_prefix = _user_storage_prefix()
    if user_prefix:
        arbo = user_arborescence_prefix(user_prefix)
        if object_name.startswith(arbo):
            st.text(object_name[len(arbo) :])
        else:
            st.text(object_name)
    else:
        st.text(object_name)

    try:
        data = _gcs_client_app().bucket(bucket).blob(object_name).download_as_bytes()
    except Exception as e:
        st.error(f"Téléchargement impossible : {e}")
        return
    if len(data) > max(_MAX_GCS_INDEX_BYTES, 80 * 1024 * 1024):
        st.warning("Fichier trop volumineux pour l’aperçu.")
        return

    suffix = Path(object_name).suffix.lower()
    with st.container(border=True):
        if suffix == ".pdf":
            _show_pdf_bytes(data)
        elif suffix in {".md", ".markdown"}:
            _show_markdown(data.decode("utf-8", errors="replace"))
        elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}:
            _show_image(data)
        else:
            st.info("Format non pris en charge pour l’aperçu.")


def main() -> None:
    st.set_page_config(page_title="Dossier Analyzer", layout="wide")
    st.markdown(_HIDE_STREAMLIT_TOP_CHROME_CSS, unsafe_allow_html=True)
    st.markdown(_ARB_FILE_TREE_FILE_BTN_CSS, unsafe_allow_html=True)
    _ensure_session()

    if not bool(getattr(st.user, "is_logged_in", False)):
        _render_login_gate()
        st.stop()

    bucket = _gcs_bucket_name()
    user_prefix = _user_storage_prefix()
    if not bucket:
        _render_title_with_version()
        st.error(
            "Stockage cloud non configuré. Définissez `gcp.bucket_name` dans `.streamlit/secrets.toml` "
            "ou la variable d’environnement `GCS_BUCKET_NAME`."
        )
        st.stop()
    if not user_prefix:
        _render_title_with_version()
        st.error("Impossible de résoudre l’identité utilisateur pour le stockage.")
        st.stop()

    if st.session_state.browse_root != _GCS_WORKSPACE_MARKER:
        st.session_state.browse_root = _GCS_WORKSPACE_MARKER
        st.session_state.selected_file = None

    _render_title_with_version()
    st.caption("Exploration cloud et analyse par mots-clés.")

    client = _gcs_client_app()
    _hydrate_keyword_lists_if_needed(client, bucket, user_prefix)
    _keyword_list_gcs_autosync_fragment()
    entries = list_user_blob_entries(client, bucket, user_prefix)
    fp = gcs_index_fingerprint(entries)
    tree, path_map = build_tree_from_gcs_entries(entries, bucket, user_prefix)
    st.session_state["_gcs_path_to_object"] = path_map
    st.session_state[_SESSION_TREE_ROOT] = tree
    st.session_state[_SESSION_FOLDER_MENU_BUCKET] = bucket
    st.session_state[_SESSION_FOLDER_MENU_USER_PREFIX] = user_prefix
    corpus = file_text_index_gcs(_CACHE_SERIAL, bucket, user_prefix, fp)

    with st.sidebar:
        st.markdown("### Dossier cloud")
        if st.button(
            "Actualiser l’index",
            use_container_width=True,
            help="Recharge la liste et réindexe les fichiers.",
        ):
            file_text_index_gcs.clear()
            st.rerun()
        st.button("Se déconnecter", on_click=st.logout, use_container_width=True)

    n_final = count_leaf_folders(tree) if tree else 0

    with st.expander("Mots-clés — analyse", expanded=True):
        kw_entries = _render_keyword_inputs(client, bucket, user_prefix)

    _maybe_open_kw_list_menu_dialog()

    tab_explorer, tab_analyse = st.tabs(["Dossiers (exploration)", "Analyse"])

    with tab_explorer:
        left, right = st.columns([1, 2], gap="large")
        with left:
            st.markdown("### Arborescence")
            if tree is not None and not tree.children and not tree.files:
                st.info(
                    "Espace vide pour l’instant."
                )
            else:
                st.caption("Développez les dossiers et cliquez sur un document.")
            with st.container(height=520, border=True, key="gcs_arborescence_tree"):
                _gcs_arborescence_dialogs(bucket, user_prefix)
                if tree is not None:
                    _render_file_tree(tree, bucket, user_prefix, path_map)
            st.caption(f"**{n_final}** dossiers finaux indexés")
        with right:
            _render_gcs_document_viewer(bucket)

        _maybe_open_folder_menu_dialog()

    with tab_analyse:
        _render_match_cards(corpus, kw_entries)


if __name__ == "__main__":
    main()
