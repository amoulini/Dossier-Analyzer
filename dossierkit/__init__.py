"""Dossierkit: document dossier tree scanning, text extraction, and keyword matching."""

from dossierkit.match import RankedFolderMatch, match_folders, ranked_folder_matches
from dossierkit.scan import TreeNode, build_tree, count_folders
from dossierkit.extract import extract_text_for_path

__all__ = [
    "TreeNode",
    "build_tree",
    "count_folders",
    "extract_text_for_path",
    "RankedFolderMatch",
    "match_folders",
    "ranked_folder_matches",
]
