# Dossier Analyzer

**Dossier Analyzer** is a small [Streamlit](https://streamlit.io) app for working with **folders of documents** (“dossiers”): browse nested folders, open PDFs and Markdown in the browser, and **rank subfolders** by how well their text matches a list of keywords (case-insensitive substring search with weighted ordering).

The core logic lives in the `dossier_analyzer` Python package (tree scan, text extraction, matching).

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

### Windows launchers

In [`windows-launchers/`](windows-launchers/):

| File | Role |
| ---- | ---- |
| [`Dossier-Analyzer.bat`](windows-launchers/Dossier-Analyzer.bat) | Double‑click launcher: runs embedded PowerShell to `git pull`, `uv sync`, and start Streamlit. |
| [`Dossier-Analyzer.ps1`](windows-launchers/Dossier-Analyzer.ps1) | Same flow as a `.ps1` script you can run from PowerShell. |

**Setup:** Edit the **`$folder`** path in each script so it matches **your** clone of this repository (absolute path to the project root). **Git** and [**uv**](https://docs.astral.sh/uv/) must be installed and available on your `PATH`.

## Configuration

| Variable | Meaning |
| -------- | ------- |
| `DOSSIER_ANALYZER_ROOT` | Optional. Default folder path shown when you first open the app. If unset, the app uses your user home directory. |

You can change the working folder from the first screen (folder browser) or later via **Changer de dossier** in the sidebar.

**Note:** Point the app at any directory on disk that contains your dossier layout.

## Project layout

| Path | Role |
| ---- | ---- |
| `app.py` | Streamlit UI |
| `dossier_analyzer/` | `scan` (tree), `extract` (PDF/MD/image text), `match` (ranked keyword matches) |
| `windows-launchers/` | Optional Windows `.bat` / `.ps1` helpers to pull, sync, and run the app |
| `.streamlit/config.toml` | Streamlit theme / server options |
