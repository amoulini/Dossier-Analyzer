"""Dossier Analyzer: document dossier tree scanning, text extraction, and keyword matching."""

from dossier_analyzer.match import RankedFolderMatch, match_folders, ranked_folder_matches
from dossier_analyzer.scan import TreeNode, build_tree, count_folders, count_leaf_folders
from dossier_analyzer.extract import extract_text_for_path

__all__ = [
    "TreeNode",
    "build_tree",
    "count_folders",
    "count_leaf_folders",
    "extract_text_for_path",
    "RankedFolderMatch",
    "match_folders",
    "ranked_folder_matches",
]
