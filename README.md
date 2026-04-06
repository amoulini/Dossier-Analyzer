# Dossierkit

**Dossierkit** is a small [Streamlit](https://streamlit.io) app for working with **folders of documents** (“dossiers”): browse nested folders, open PDFs and Markdown in the browser, and **rank subfolders** by how well their text matches a list of keywords (case-insensitive substring search with weighted ordering).

The core logic lives in the `dossierkit` Python package (tree scan, text extraction, matching).

## Features

- **Explorer** — Expandable tree of folders; click a file to preview it.
- **Previews** — PDF (rendered pages), Markdown, common image types; other files are listed but not previewed.
- **Analysis** — Dynamic keyword rows; results show matching subfolders with hit counts and ranking (total hits, distinct keywords, weight of keywords by order).

## Requirements

- **Python 3.13+**
- Dependencies are listed in [`pyproject.toml`](pyproject.toml) (Streamlit, PyMuPDF, pdfminer-six).

## Setup

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Or with pip, in a virtual environment:

```bash
pip install -e .
```

## Run the app

From the project root:

```bash
uv run streamlit run app.py
```

Then open the URL Streamlit prints (usually [http://localhost:8501](http://localhost:8501)).

## Configuration

| Variable | Meaning |
| -------- | ------- |
| `DOSSIERKIT_ROOT` | Optional. Default folder path for dossiers. If unset, the app uses `data/dossiers` next to `app.py`. |

You can also change the root anytime in the sidebar text field.

**Note:** In this repo, `data/` may be gitignored. Point the app at any directory on disk that contains your dossier layout.

## Project layout

| Path | Role |
| ---- | ---- |
| `app.py` | Streamlit UI |
| `dossierkit/` | `scan` (tree), `extract` (PDF/MD/image text), `match` (ranked keyword matches) |
| `.streamlit/config.toml` | Streamlit theme / server options |
