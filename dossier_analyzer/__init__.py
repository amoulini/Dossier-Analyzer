"""Dossier Analyzer: keyword matching and tree types used by the GCS-backed app.

Local directory scanning (``build_tree``, etc.) lives in ``dossier_analyzer.scan``.
Text extraction for bytes streams lives in ``dossier_analyzer.extract``.
"""

from dossier_analyzer.extract import extract_text_from_bytes
from dossier_analyzer.match import (
    KeywordEntry,
    RankedFolderMatch,
    match_folders,
    normalize_keyword_entries,
    ranked_folder_matches,
)
from dossier_analyzer.scan import TreeNode, count_leaf_folders

__all__ = [
    "TreeNode",
    "count_leaf_folders",
    "extract_text_from_bytes",
    "KeywordEntry",
    "RankedFolderMatch",
    "match_folders",
    "normalize_keyword_entries",
    "ranked_folder_matches",
]
