"""MkDocs hooks: the README is the home page."""

import re
from pathlib import Path

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page

ROOT = Path(__file__).resolve().parents[2]
SITE = "docs/project/"
LINK = re.compile(r"\]\((?!https?://|#)([^)]+)\)")


def _blob(config: MkDocsConfig) -> str:
    return (config.repo_url or "").rstrip("/") + "/blob/main/"


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    """Point links that leave docs/project at GitHub."""
    docs = Path(config.docs_dir).resolve()
    base = (docs / page.file.src_path).parent

    def link(match: re.Match[str]) -> str:
        target = match.group(1)
        path, _, anchor = target.partition("#")
        resolved = (base / path).resolve()
        if resolved.is_relative_to(docs):
            return match.group(0)
        url = _blob(config) + resolved.relative_to(ROOT).as_posix()
        return f"]({url}#{anchor})" if anchor else f"]({url})"

    return LINK.sub(link, markdown)


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Serve README.md as index.md, its links pointed at the site or at GitHub."""
    blob = _blob(config)

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
