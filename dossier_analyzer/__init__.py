"""Dossier Analyzer: document dossier tree scanning, text extraction, and keyword matching."""

from dossier_analyzer.match import (
    KeywordEntry,
    RankedFolderMatch,
    match_folders,
    normalize_keyword_entries,
    ranked_folder_matches,
)
from dossier_analyzer.scan import (
    TreeNode,
    build_tree,
    count_folders,
    count_leaf_folders,
    iter_leaf_folder_nodes,
)
from dossier_analyzer.extract import extract_text_for_path

__all__ = [
    "TreeNode",
    "build_tree",
    "count_folders",
    "count_leaf_folders",
    "iter_leaf_folder_nodes",
    "extract_text_for_path",
    "KeywordEntry",
    "RankedFolderMatch",
    "match_folders",
    "normalize_keyword_entries",
    "ranked_folder_matches",
]
