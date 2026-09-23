"""One-shot, source-only migration on the dedicated GitHub work branch."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

BASE = "fc14acc8f1e17067e2d80de4b31cfdf0849da9b4"
TARGETS = (
    "bot.py", "vk_bot.py", "website_app.py",
    "shared/travel_crm.py", "shared/travel_crm_store.py",
)
IMPORT = "from shared.utc_time import utc_now_naive\n"


def rewrite(source: str) -> tuple[str, int]:
    """Edit AST-identified attributes, preserving all unrelated source bytes."""
    tree = ast.parse(source)
    if any(isinstance(n, ast.Name) and n.id == "utc_now_naive" for n in ast.walk(tree)):
        raise ValueError("helper name already exists; refuse ambiguous rewrite")
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module == "datetime"
               and any(a.name == "datetime" and a.asname is None for a in n.names)]
    if len(imports) != 1:
        raise ValueError("expected exactly one unaliased datetime import")
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and isinstance(n.value, ast.Name) and n.value.id == "datetime"
             and n.attr == "utcnow"]
    if not nodes:
        raise ValueError("no deprecated factories found; source differs")
    raw = source.encode("utf-8")
    lines = raw.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    for node in nodes:
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        edits.append((start, end, b"utc_now_naive"))
    shared_imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
                      and n.module and (n.module == "shared" or n.module.startswith("shared."))]
    if shared_imports:
        insertion = offsets[shared_imports[0].lineno - 1]
    else:
        top_imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        insertion = offsets[top_imports[-1].end_lineno]
    edits.append((insertion, insertion, IMPORT.encode("utf-8")))
    for start, end, replacement in sorted(edits, reverse=True):
        raw = raw[:start] + replacement + raw[end:]
    result = raw.decode("utf-8")
    result_tree = ast.parse(result)
    if any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
           and n.value.id == "datetime" and n.attr == "utcnow"
           for n in ast.walk(result_tree)):
        raise ValueError("deprecated factory remains")
    return result, len(nodes)


def main() -> None:
    if subprocess.check_output(["git", "status", "--porcelain"]).strip():
        raise SystemExit("Refusing to edit a dirty checkout")
    pending = []
    for name in TARGETS:
        path = Path(name)
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Unexpected source path: {name}")
        original = subprocess.check_output(["git", "show", f"{BASE}:{name}"])
        if path.read_bytes() != original:
            raise SystemExit(f"Source differs from pinned base: {name}")
        updated, count = rewrite(original.decode("utf-8"))
        pending.append((path, updated, count))
    # Validate every target before writing any of them.
    for path, updated, count in pending:
        path.write_bytes(updated.encode("utf-8"))
        print(f"{path}: replaced {count} deprecated UTC factories")


if __name__ == "__main__":
    main()
