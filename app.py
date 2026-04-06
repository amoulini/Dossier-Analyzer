"""
Dossier Analyzer — Streamlit app: dossier exploration (tree + viewer) and keyword-based folder analysis.
"""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from html import escape
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st
from openpyxl import Workbook

from dossier_analyzer.extract import aggregate_folder_text
from dossier_analyzer.match import RankedFolderMatch, normalize_keywords, ranked_folder_matches
from dossier_analyzer.scan import (
    TreeNode,
    build_tree,
    count_leaf_folders,
    iter_folder_nodes,
)

DEFAULT_DATA_ROOT = Path(__file__).resolve().parent / "data" / "dossiers"

# Pass as first arg to folder_text_index so Streamlit cache keys never match stale disk entries.
_CACHE_SERIAL = "dossier-analyzer-3"

# Microsoft Excel product green (#217346); applied via CSS to the sole st.download_button in this app.
_EXCEL_EXPORT_BTN_CSS = """
<style>
    div[data-testid="stDownloadButton"] button {
        background-color: rgb(33, 115, 70) !important;
        border: 1px solid rgb(27, 95, 59) !important;
        color: rgb(255, 255, 255) !important;
    }
    div[data-testid="stDownloadButton"] button:hover {
        background-color: rgb(27, 95, 59) !important;
        border-color: rgb(22, 80, 50) !important;
        color: rgb(255, 255, 255) !important;
    }
    div[data-testid="stDownloadButton"] button:focus-visible {
        box-shadow: rgb(255, 255, 255) 0px 0px 0px 2px, rgb(33, 115, 70) 0px 0px 0px 4px !important;
    }
</style>
"""


def _safe_widget_key(path_str: str, prefix: str) -> str:
    h = hashlib.sha256(path_str.encode()).hexdigest()[:24]
    return f"{prefix}_{h}"


def _folder_key(node: TreeNode) -> str:
    if node.rel == Path("."):
        return "."
    return node.rel.as_posix()


def _folder_label(node: TreeNode) -> str:
    if node.rel == Path("."):
        return node.name
    return node.rel.as_posix()


def _matches_to_excel_bytes(ranked: list[RankedFolderMatch], column_keywords: list[str]) -> bytes:
    """One sheet: dossier path (relative) × keyword occurrence counts."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Analyse"
    ws.append(["Dossier", *column_keywords])
    for row in ranked:
        hits = dict(row.keyword_hits)
        ws.append([row.folder_key] + [hits.get(kw, 0) for kw in column_keywords])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@st.cache_data(show_spinner="Indexation des dossiers…")
def folder_text_index(cache_version: str, root_str: str) -> dict[str, str]:
    root = Path(root_str).resolve()
    tree = build_tree(root)
    if tree is None:
        return {}
    # Exclude the configured root folder itself from analysis results (only subfolders).
    return {
        _folder_key(n): aggregate_folder_text(n)
        for n in iter_folder_nodes(tree)
        if n.rel != Path(".")
    }


def _ensure_session() -> None:
    if "selected_file" not in st.session_state:
        st.session_state.selected_file = None
    if "kw_rows" not in st.session_state:
        st.session_state.kw_rows = [
            {"id": "init-1", "text": "Excellent niveau"},
            {"id": "init-2", "text": "Bon niveau"},
            {"id": "init-3", "text": "Irrégulier"},
        ]


def _init_kw_row_widgets() -> None:
    for row in st.session_state.kw_rows:
        wk = f"kw_{row['id']}"
        if wk not in st.session_state:
            st.session_state[wk] = row["text"]


def _make_persist_keyword(row_id: str):
    def _persist() -> None:
        key = f"kw_{row_id}"
        val = str(st.session_state.get(key, "") or "")
        for r in st.session_state.kw_rows:
            if r["id"] == row_id:
                r["text"] = val
                break

    return _persist


def _render_keyword_inputs() -> list[str]:
    st.caption(
        "Saisie dynamique : barre rouge à gauche. "
        "Recherche insensible à la casse, sous-chaîne dans tout le texte du dossier. "
        "Ces champs restent en place lorsque vous passez à l’onglet Dossiers."
    )

    _init_kw_row_widgets()
    rows = list(st.session_state.kw_rows)
    remove_id: str | None = None

    for row in rows:
        c0, c1, c2 = st.columns([0.04, 4.7, 1], gap="small")
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
            st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
            if st.button(
                "🗑️",
                key=f"del_{row['id']}",
                help="Supprimer",
            ):
                remove_id = row["id"]

    c_add, _ = st.columns([1, 3])
    with c_add:
        if st.button("➕ Ajouter", width="stretch"):
            new_id = uuid.uuid4().hex[:10]
            st.session_state.kw_rows.append({"id": new_id, "text": ""})
            st.rerun()

    if remove_id is not None:
        st.session_state.kw_rows = [r for r in st.session_state.kw_rows if r["id"] != remove_id]
        st.session_state.pop(f"kw_{remove_id}", None)
        st.session_state.pop(f"del_{remove_id}", None)
        st.rerun()

    for row in st.session_state.kw_rows:
        wk = f"kw_{row['id']}"
        if wk in st.session_state:
            row["text"] = str(st.session_state[wk] or "")

    return [str(st.session_state.get(f"kw_{r['id']}", "") or "") for r in st.session_state.kw_rows]


def _render_match_cards(root_str: str, keywords: list[str]) -> None:
    kws_norm = normalize_keywords(keywords)
    if not kws_norm:
        st.markdown("##### Dossiers correspondants")
        st.caption("Entrez au moins un mot-clé non vide ci-dessus.")
        return

    corpus = folder_text_index(_CACHE_SERIAL, root_str)
    ranked = ranked_folder_matches(corpus, keywords)

    if ranked:
        st.markdown(_EXCEL_EXPORT_BTN_CSS, unsafe_allow_html=True)

    head_l, head_r = st.columns([4, 1])
    with head_l:
        st.markdown("##### Dossiers correspondants")
    with head_r:
        if ranked:
            st.download_button(
                "Export to Excel",
                data=_matches_to_excel_bytes(ranked, kws_norm),
                file_name="dossier_analyzer_matches.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="secondary",
                width="stretch",
                key="export_matches_xlsx",
            )

    if not ranked:
        st.info("Aucun dossier ne contient ces mots-clés (recherche insensible à la casse).")
        return

    tree = build_tree(Path(root_str).resolve())
    key_to_node: dict[str, TreeNode] = {}
    if tree is not None:
        key_to_node = {_folder_key(n): n for n in iter_folder_nodes(tree)}

    for row in ranked:
        fk = row.folder_key
        node = key_to_node.get(fk)
        label = _folder_label(node) if node else fk
        chips = "".join(
            f"<span style='display:inline-block;margin:2px 6px 2px 0;padding:2px 10px;"
            f"background:#ecf0f1;border-radius:999px;font-size:0.9em;'>"
            f"{escape(kw)} <strong>({cnt})</strong></span>"
            for kw, cnt in row.keyword_hits
        )
        badge = (
            f"<span style='display:inline-block;background:#1f77b4;color:white;padding:6px 14px;"
            f"border-radius:8px;font-weight:600;margin-right:10px;'>{escape(label)}</span>"
        )
        st.markdown(
            f"<div style='margin:12px 0;padding:12px 14px;border:1px solid #dfe6e9;border-radius:10px;"
            f"background:#fafbfc;'>{badge}{chips}</div>",
            unsafe_allow_html=True,
        )


def _render_file_tree(node: TreeNode) -> None:
    if node.children:
        for child in node.children:
            with st.expander(f"📁 {child.name}", expanded=False):
                _render_file_tree(child)
    for fpath in node.files:
        label = f"{fpath.name}"
        if fpath.suffix.lower() == ".pdf":
            label = f"📄 {label}"
        elif fpath.suffix.lower() in {".md", ".markdown"}:
            label = f"📝 {label}"
        else:
            label = f"🖼 {label}"
        resolved = str(fpath.resolve())
        is_selected = st.session_state.get("selected_file") == resolved
        key = _safe_widget_key(str(fpath), "pick_file")
        btn_type = "primary" if is_selected else "secondary"
        if st.button(label, key=key, type=btn_type, width="stretch"):
            st.session_state.selected_file = resolved
            st.rerun()


def _show_pdf(path: Path) -> None:
    try:
        doc = fitz.open(path)
    except Exception as e:
        st.error(f"Impossible d’ouvrir le PDF : {e}")
        return
    try:
        for i in range(len(doc)):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            data = pix.tobytes("png")
            st.image(io.BytesIO(data), width="stretch", caption=f"Page {i + 1} / {len(doc)}")
    finally:
        doc.close()


def _show_markdown(path: Path | None, text: str | None = None) -> None:
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except OSError as e:
            st.error(f"Impossible de lire le fichier : {e}")
            return
    st.markdown(text)


def _show_image(path: Path | None, image_bytes: bytes | None = None) -> None:
    try:
        if image_bytes is not None:
            st.image(io.BytesIO(image_bytes), width="stretch")
        else:
            st.image(str(path), width="stretch")
    except Exception as e:
        st.error(f"Impossible d’afficher l’image : {e}")


def _display_path_under_root(path: Path, browse_root: Path) -> None:
    """Show path relative to browse root (posix); avoids repeating the default dossier root."""
    abs_path = path.resolve()
    root_r = browse_root.resolve()
    try:
        rel = abs_path.relative_to(root_r)
        st.text(rel.as_posix())
    except ValueError:
        st.text(abs_path.as_posix())


def _render_document_viewer(browse_root: Path) -> None:
    st.markdown("##### Document visualizer")
    sel = st.session_state.selected_file
    if not sel:
        st.caption("Sélectionnez un fichier dans l’arborescence.")
        return

    path = Path(sel).expanduser()
    _display_path_under_root(path, browse_root)
    if not path.is_file():
        st.warning("Fichier introuvable.")
        return
    suffix = path.suffix.lower()
    with st.container(border=True):
        if suffix == ".pdf":
            _show_pdf(path)
        elif suffix in {".md", ".markdown"}:
            _show_markdown(path)
        elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}:
            _show_image(path)
        else:
            st.info("Format non pris en charge pour l’aperçu.")


def main() -> None:
    st.set_page_config(page_title="Dossier Analyzer", layout="wide")
    _ensure_session()

    st.title("Dossier Analyzer")
    st.caption("Exploration de dossiers et analyse par mots-clés.")
    default_root = os.environ.get("DOSSIER_ANALYZER_ROOT", str(DEFAULT_DATA_ROOT))
    with st.sidebar:
        st.markdown("### Racine des dossiers")
        root_input = st.text_input(
            "Chemin",
            value=default_root,
            label_visibility="collapsed",
            key="root_path_input",
        )
        root = Path(root_input).expanduser().resolve()
        if not root.is_dir():
            st.error("Ce chemin n’est pas un dossier valide.")

    if not root.is_dir():
        st.stop()

    root_str = str(root)
    tree = build_tree(root)
    n_final = count_leaf_folders(tree) if tree else 0

    with st.expander("Mots-clés — analyse", expanded=True):
        kw_list = _render_keyword_inputs()

    tab_explorer, tab_analyse = st.tabs(["Dossiers (exploration)", "Analyse"])

    with tab_explorer:
        left, right = st.columns([1, 2], gap="large")
        with left:
            st.markdown("### Arborescence")
            if tree is None:
                st.warning("Arborescence vide ou inaccessible.")
            else:
                st.caption("Développez les dossiers et cliquez sur un document.")
                with st.container(height=520, border=True):
                    _render_file_tree(tree)
            st.caption(f"**{n_final}** dossiers finaux indexés")
        with right:
            _render_document_viewer(root)

    with tab_analyse:
        _render_match_cards(root_str, kw_list)


if __name__ == "__main__":
    main()
