"""
Dossier Analyzer — Streamlit app: dossier exploration (tree + viewer) and keyword-based folder analysis.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import uuid
from html import escape
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st
from openpyxl import Workbook

from dossier_analyzer.extract import collect_all_file_paths, extract_text_for_path
from dossier_analyzer.match import (
    KeywordEntry,
    RankedFolderMatch,
    normalize_keyword_entries,
    ranked_folder_matches,
)
from dossier_analyzer.scan import (
    SUPPORTED_EXTENSIONS,
    TreeNode,
    build_tree,
    count_leaf_folders,
)

# Pass as first arg to file_text_index so Streamlit cache keys never match stale disk entries.
_CACHE_SERIAL = "dossier-analyzer-5"

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


# Une couleur de fond distincte par note (0 → 5), progression chromatique rouge → vert.
_POSITIVITY_CHIP_BG: tuple[str, ...] = (
    "#a93226",  # 0 rouge
    "#cb4335",  # 1 rouge brique
    "#e67e22",  # 2 orange
    "#d4ac0d",  # 3 or / jaune soutenu
    "#28b463",  # 4 vert clair
    "#1e8449",  # 5 vert
)


def _positivity_chip_colors(grade: int) -> tuple[str, str]:
    """Fond et texte pour une pastille : teinte fixe par note de 0 à 5 (rouge → vert)."""
    p = max(0, min(5, int(grade)))
    bg = _POSITIVITY_CHIP_BG[p]
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
        positivity_weighted_avg=0.0,
    )


def _matches_to_excel_bytes(ranked: list[RankedFolderMatch], columns: list[KeywordEntry]) -> bytes:
    """Sheet 'Analyse': fichiers × counts; sheet 'Mots-clés': keyword ↔ grade (sorted by grade ↓)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Analyse"
    headers = [e.text for e in columns]
    ws.append(["Fichier", *headers])
    ws.append(["Positivité (0–5)", *[str(e.positivity) for e in columns]])
    for row in ranked:
        hits = dict(row.keyword_hits)
        ws.append([row.folder_key] + [hits.get(kw, 0) for kw in headers])

    ws_kw = wb.create_sheet("Mots-clés — positivité", 1)
    ws_kw.append(["Mot-clé", "Positivité (0–5)"])
    for e in sorted(columns, key=lambda x: (-x.positivity, x.text.lower())):
        ws_kw.append([e.text, e.positivity])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@st.cache_data(show_spinner="Indexation des fichiers…")
def file_text_index(cache_version: str, root_str: str) -> dict[str, str]:
    """Chemin relatif (posix) → texte extrait pour chaque fichier pris en charge sous la racine."""
    root = Path(root_str).resolve()
    tree = build_tree(root)
    if tree is None:
        return {}
    corpus: dict[str, str] = {}
    for fpath in collect_all_file_paths(tree):
        if fpath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        rel = fpath.relative_to(root).as_posix()
        corpus[rel] = extract_text_for_path(fpath)
    return corpus


def _ensure_session() -> None:
    if "browse_root" not in st.session_state:
        st.session_state.browse_root = None
    if "selected_file" not in st.session_state:
        st.session_state.selected_file = None
    if "kw_rows" not in st.session_state:
        st.session_state.kw_rows = [
            {"id": "init-1", "text": "Excellent niveau", "positivity": 5},
            {"id": "init-2", "text": "Bon niveau", "positivity": 4},
            {"id": "init-3", "text": "Irrégulier", "positivity": 1},
        ]
    for r in st.session_state.kw_rows:
        if "positivity" not in r:
            r["positivity"] = 3


def _native_file_dialog_usable() -> bool:
    """Tk file dialog only works where the Streamlit process has a desktop."""
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _is_path_under(child: Path, base: Path) -> bool:
    try:
        child.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _user_home_path() -> Path:
    """Dossier utilisateur : ~ sous Linux/macOS, profil utilisateur sous Windows (%USERPROFILE%)."""
    return Path.home().resolve()


def _default_workspace_path_str() -> str:
    """Chemin par défaut pour le dossier racine : variable d’environnement ou dossier utilisateur."""
    env_root = (os.environ.get("DOSSIER_ANALYZER_ROOT") or "").strip()
    if env_root:
        return env_root
    return str(_user_home_path())


def _filesystem_browse_top() -> Path:
    """Racine haute du navigateur : `/` sous Linux/macOS, racine du lecteur (ex. `C:\\`) sous Windows."""
    if sys.platform == "win32":
        home = _user_home_path()
        anch = home.anchor
        if anch and anch != "\\":
            root = Path(anch)
            if root.exists():
                return root.resolve()
        return home
    return Path("/").resolve()


def _normalize_workspace_root_path() -> Path:
    """Garde `root_path_input` comme chemin dossier valide sous la racine disque ; renvoie le Path résolu."""
    top = _filesystem_browse_top()
    home = _user_home_path()
    raw = str(st.session_state.get("root_path_input", "") or "").strip()
    if not raw:
        cur = home if home.is_dir() else top
        st.session_state.root_path_input = str(cur)
        return cur.resolve()
    cur = Path(raw).expanduser().resolve()
    if not _is_path_under(cur, top):
        cur = top
        st.session_state.root_path_input = str(cur)
        return cur.resolve()
    if not cur.is_dir():
        cur = home if home.is_dir() else top
        st.session_state.root_path_input = str(cur)
        return cur.resolve()
    st.session_state.root_path_input = str(cur)
    return cur.resolve()


def _should_hide_folder_in_browser(p: Path) -> bool:
    """Masque les dossiers cachés et ceux dont le nom commence par un point."""
    if not p.is_dir():
        return True
    if p.name.startswith("."):
        return True
    try:
        st_info = p.stat()
    except OSError:
        return True
    if sys.platform == "win32" and hasattr(st_info, "st_file_attributes"):
        if st_info.st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN:
            return True
    return False


def _render_server_folder_browser() -> None:
    """Navigateur de dossiers : met à jour `root_path_input` (seule source pour le dossier choisi)."""
    top = _filesystem_browse_top()
    cur = _normalize_workspace_root_path()

    st.caption(
        "Navigation locale : vous ne pouvez pas remonter au‑dessus de la racine du disque "
        f"(`{top.as_posix()}`)."
    )

    home = _user_home_path()
    up_col, home_col, root_col = st.columns(3)
    with up_col:
        if cur != top and _is_path_under(cur.parent, top):
            if st.button("↑ Remonter", key="srv_browse_up", use_container_width=True):
                st.session_state.root_path_input = str(cur.parent.resolve())
                st.rerun()
    with home_col:
        if st.button(
            "Dossier utilisateur",
            key="srv_browse_home",
            use_container_width=True,
            help="Linux / macOS : ~ / $HOME ; Windows : profil utilisateur.",
        ):
            st.session_state.root_path_input = str(home if home.is_dir() else top)
            st.rerun()
    with root_col:
        if cur != top:
            if st.button("Racine du disque", key="srv_browse_root", use_container_width=True):
                st.session_state.root_path_input = str(top)
                st.rerun()

    try:
        subs = sorted(
            (p for p in cur.iterdir() if not _should_hide_folder_in_browser(p)),
            key=lambda p: p.name.lower(),
        )
    except PermissionError:
        st.error("Permission refusée pour lire ce dossier.")
        subs = []

    if not subs:
        st.caption("Aucun sous-dossier ici.")
        return

    st.caption("Sous-dossiers — cliquer pour entrer :")
    with st.container(height=280, border=True):
        ncols = 3
        for row_start in range(0, len(subs), ncols):
            chunk = subs[row_start : row_start + ncols]
            cols = st.columns(ncols, gap="small")
            for j, p in enumerate(chunk):
                with cols[j]:
                    bkey = f"sd_{hashlib.sha256(str(p).encode()).hexdigest()[:18]}"
                    if st.button(p.name, key=bkey, use_container_width=True):
                        st.session_state.root_path_input = str(p.resolve())
                        st.rerun()


def _render_workspace_picker() -> None:
    """Premier écran : choisir explicitement le dossier racine avant le reste de l’application."""
    if "root_path_input" not in st.session_state:
        st.session_state.root_path_input = _default_workspace_path_str()
    cur = _normalize_workspace_root_path()

    use_tk = _native_file_dialog_usable()
    st.title("Dossier Analyzer")
    st.markdown(
        "Bienvenue. **Choisissez le dossier racine** uniquement via **Parcourir les dossiers sur cette machine** "
        "ci‑dessous, puis cliquez sur **Ouvrir ce dossier**."
    )

    st.markdown("##### Chemin du dossier racine")
    st.code(str(cur), language=None)

    with st.expander(
        "Parcourir les dossiers sur cette machine",
        expanded=not use_tk,
    ):
        _render_server_folder_browser()

    if st.button("Ouvrir ce dossier", type="primary"):
        if cur.is_dir():
            st.session_state.browse_root = str(cur)
            st.session_state.selected_file = None
            st.rerun()
        else:
            st.error("Ce chemin n’est pas un dossier valide.")


def _init_kw_row_widgets() -> None:
    for row in st.session_state.kw_rows:
        wk = f"kw_{row['id']}"
        pk = f"kw_pos_{row['id']}"
        if wk not in st.session_state:
            st.session_state[wk] = row["text"]
        if pk not in st.session_state:
            st.session_state[pk] = int(row.get("positivity", 3))


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
        val = int(st.session_state.get(pk, 3))
        for r in st.session_state.kw_rows:
            if r["id"] == row_id:
                r["positivity"] = val
                break

    return _persist


def _render_keyword_inputs() -> list[KeywordEntry]:
    st.caption(
        "Saisie dynamique : barre rouge à gauche. "
        "Recherche insensible à la casse, sous-chaîne dans le texte de chaque fichier (PDF, Markdown, nom pour les images). "
        "Tri Analyse : le curseur Positif (0–5) pondère le classement (0 = pas positif, 5 = très positif). "
        "Ces champs restent en place lorsque vous passez à l’onglet Dossiers."
    )

    _init_kw_row_widgets()
    rows = list(st.session_state.kw_rows)
    remove_id: str | None = None

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
                "Positif",
                min_value=0,
                max_value=5,
                step=1,
                key=f"kw_pos_{row['id']}",
                help="0 = pas positif … 5 = très positif — utilisé pour trier les fichiers correspondants",
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
        if st.button("➕ Ajouter", width="stretch"):
            new_id = uuid.uuid4().hex[:10]
            st.session_state.kw_rows.append({"id": new_id, "text": "", "positivity": 3})
            st.rerun()

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
            int(st.session_state.get(f"kw_pos_{r['id']}", r.get("positivity", 3))),
        )
        for r in st.session_state.kw_rows
    ]


def _render_match_cards(root_str: str, entries: list[KeywordEntry]) -> None:
    kws_norm = normalize_keyword_entries(entries)
    if not kws_norm:
        st.markdown("##### Fichiers correspondants")
        st.caption("Entrez au moins un mot-clé non vide ci-dessus.")
        return

    corpus = file_text_index(_CACHE_SERIAL, root_str)
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
                bg, fg = _positivity_chip_colors(pos_by_kw.get(kw, 3))
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

    if st.session_state.browse_root is None:
        _render_workspace_picker()
        st.stop()

    root = Path(st.session_state.browse_root).resolve()

    st.title("Dossier Analyzer")
    st.caption("Exploration de dossiers et analyse par mots-clés.")
    with st.sidebar:
        st.markdown("### Dossier de travail")
        st.text(str(root))
        if st.button("Changer de dossier", use_container_width=True):
            st.session_state.browse_root = None
            st.session_state.selected_file = None
            st.session_state.pop("root_path_input", None)
            st.rerun()

    if not root.is_dir():
        st.error("Le dossier de travail n’est plus accessible. Choisissez-en un autre (barre latérale).")
        st.stop()

    root_str = str(root)
    tree = build_tree(root)
    n_final = count_leaf_folders(tree) if tree else 0

    with st.expander("Mots-clés — analyse", expanded=True):
        kw_entries = _render_keyword_inputs()

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
        _render_match_cards(root_str, kw_entries)


if __name__ == "__main__":
    main()
