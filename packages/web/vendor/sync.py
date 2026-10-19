"""Copy the npm packages of package-lock.json into static/vendor/<name>-<version>/."""

import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parent
VENDOR = WEB / "src" / "jatszoter_web" / "static" / "vendor"
PACKAGES = {
    "htmx.org": ("htmx", ["dist/htmx.min.js"]),
    "@xterm/xterm": ("xterm", ["css/xterm.css", "lib/xterm.js"]),
    "asciinema-player": (
        "asciinema-player",
        ["dist/bundle/asciinema-player.css", "dist/bundle/asciinema-player.min.js"],
    ),
    "@fontsource/source-sans-pro": (
        "source-sans-pro",
        [
            "files/source-sans-pro-latin-400-normal.woff2",
            "files/source-sans-pro-latin-600-normal.woff2",
        ],
    ),
    "@fontsource/source-code-pro": (
        "source-code-pro",
        ["files/source-code-pro-latin-400-normal.woff2"],
    ),
}
REFERENCES = ("src/**/*.html", "src/**/*.css", "tests/**/*.py")
QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


def escaped(text: str) -> str:
    """Non-ASCII characters as \\u escapes; the vendored scripts hold them only in strings."""

    def units(match: re.Match[str]) -> str:
        code = match.group().encode("utf-16-be").hex()
        return "".join(f"\\u{code[i:i + 4]}" for i in range(0, len(code), 4))

    return re.sub(r"[^\x00-\x7f]", units, text)


def copy(source: Path, target: Path) -> None:
    """Copy a file the way the pre-commit hooks want it: ASCII, ending in a newline."""
    if source.suffix == ".woff2":
        shutil.copyfile(source, target)
        return
    text = source.read_text(encoding="utf-8")
    text = escaped(text) if source.suffix == ".js" else text.translate(QUOTES)
    target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def retarget(old: str, new: str) -> None:
    """Point every template, stylesheet and test that names `old` at `new`."""
    for pattern in REFERENCES:
        for path in WEB.glob(pattern):
            text = path.read_text(encoding="utf-8")
            if old in text:
                path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    lock = json.loads((HERE / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    for package, (name, files) in PACKAGES.items():
        target = VENDOR / f"{name}-{lock[f'node_modules/{package}']['version']}"
        for old in VENDOR.glob(f"{name}-[0-9]*"):
            if old != target:
                shutil.rmtree(old)
                retarget(old.name, target.name)
        target.mkdir(exist_ok=True)
        for file in [*files, "LICENSE"]:
            copy(HERE / "node_modules" / package / file, target / Path(file).name)


if __name__ == "__main__":
    main()
