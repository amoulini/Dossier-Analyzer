"""Build an in-memory tree of folders and supported files from a local root."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".md",
        ".markdown",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".tif",
    }
)


@dataclass
class TreeNode:
    name: str
    rel: Path  # relative to scan root; "." for root
    path: Path  # absolute path to this directory
    children: list[TreeNode] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def _build_node(dir_path: Path, root: Path) -> TreeNode:
    rel = Path(".") if dir_path == root else dir_path.relative_to(root)
    name = dir_path.name if dir_path != root else (root.name or str(root))
    node = TreeNode(name=name, rel=rel, path=dir_path)
    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return node
    for p in entries:
        if p.is_dir():
            node.children.append(_build_node(p, root))
        elif p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            node.files.append(p)
    node.children.sort(key=lambda c: c.name.lower())
    node.files.sort(key=lambda f: f.name.lower())
    return node


def build_tree(root: Path) -> TreeNode | None:
    root = root.resolve()
    if not root.is_dir():
        return None
    return _build_node(root, root)


def count_folders(node: TreeNode | None) -> int:
    if node is None:
        return 0
    return 1 + sum(count_folders(c) for c in node.children)


def count_leaf_folders(node: TreeNode | None) -> int:
    """Folders that have no subfolders (terminal dossiers); excludes the scan root itself."""
    if node is None:
        return 0
    if not node.children:
        if node.rel == Path("."):
            return 0
        return 1
    return sum(count_leaf_folders(c) for c in node.children)


def iter_leaf_folder_nodes(node: TreeNode) -> list[TreeNode]:
    """Terminal dossiers under this tree (no subfolders); excludes the scan root directory itself."""
    if not node.children:
        if node.rel == Path("."):
            return []
        return [node]
    out: list[TreeNode] = []
    for c in node.children:
        out.extend(iter_leaf_folder_nodes(c))
    return out


def iter_folder_nodes(node: TreeNode) -> list[TreeNode]:
    out: list[TreeNode] = [node]
    for c in node.children:
        out.extend(iter_folder_nodes(c))
    return out
