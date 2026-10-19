"""MkDocs hooks: the README is the home page."""

import re
from pathlib import Path

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files

ROOT = Path(__file__).resolve().parents[2]
SITE = "docs/project/"
LINK = re.compile(r"\]\((?!https?://|#)([^)]+)\)")


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Serve README.md as index.md, its links pointed at the site or at GitHub."""
    blob = (config.repo_url or "").rstrip("/") + "/blob/main/"

    def link(match: re.Match[str]) -> str:
        target = match.group(1)
        if target.startswith(SITE):
            return f"]({target.removeprefix(SITE)})"
        return f"]({blob}{target})"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    page = "---\ntitle: Home\n---\n\n" + LINK.sub(link, readme)
    home = File.generated(config, "index.md", content=page)
    # edit_uri is relative to docs/project/, and "Edit on GitHub" has to open the README.
    home.edit_uri = "../../README.md"
    files.append(home)
    return files
