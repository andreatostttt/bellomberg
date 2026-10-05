"""Check public handbook links, page inventory and safe synthetic SVG structure.

Standard library only; reads source/docs, never runtime data or configuration.
Run from any working directory: python docs/guide/verify_docs.py
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "guide"
ASSETS = ROOT / "docs" / "assets" / "product"
# Menu order of app/src/lib/navigation.ts with each destination's function key and chapter slug.
# Keys are not positional: a registry entry may carry a fixed key (Filing keeps F20 after F6), and
# chapters are numbered by key, so the slug prefix always repeats the key number.
EXPECTED = [
    ("dashboard", "F1", "01-command-center"), ("performance", "F2", "02-performance"),
    ("watchlist", "F3", "03-watchlist"), ("market", "F4", "04-global-markets"),
    ("news", "F5", "05-news-desk"), ("fundamentals", "F6", "06-fundamentals"),
    ("filing", "F20", "20-filing"),
    ("factors", "F7", "07-factor-lab"), ("montecarlo", "F8", "08-monte-carlo"),
    ("vol", "F9", "09-vol-deck"), ("edge", "F10", "10-edge-scanner"),
    ("chat", "F11", "11-agent-chat"), ("agents", "F12", "12-agents-live"),
    ("progress", "F13", "13-agent-progress"), ("memos", "F14", "14-memo-archive"),
    ("decisions", "F15", "15-decisions"), ("trades", "F16", "16-trade-entry"),
    ("movements", "F17", "17-movements"), ("mandato", "F18", "18-mandate-journal"),
    ("settings", "F19", "19-settings"),
]
COUNT = len(EXPECTED)
# One registry tuple: id, route, short name, label, group and an optional fixed key.
ENTRY = re.compile(
    r"^\s*\['([^']+)',\s*'[^']+',\s*'([^']+)',\s*'[^']*',\s*'[^']*'(?:,\s*'([^']*)')?\s*\],",
    re.MULTILINE,
)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def anchors(path: Path) -> set[str]:
    result: set[str] = set()
    seen: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^#{1,6}\s", line):
            continue
        title = re.sub(r"^#+\s+", "", line).strip().lower()
        title = re.sub(r"[^\w\- ]", "", title, flags=re.UNICODE).replace(" ", "-")
        n = seen.get(title, 0)
        seen[title] = n + 1
        result.add(title if n == 0 else f"{title}-{n}")
    return result


def check_links(doc: Path) -> int:
    text = doc.read_text(encoding="utf-8")
    count = 0
    if text.startswith("\ufeff"):
        fail(f"UTF-8 BOM: {doc.relative_to(ROOT)}")
    for match in LINK.finditer(text):
        url = urlsplit(match.group(1).strip("<>"))
        if url.scheme or url.netloc:
            continue
        count += 1
        target = (doc.parent / unquote(url.path)).resolve() if url.path else doc
        if not target.is_relative_to(ROOT):
            fail(f"Link leaves source tree: {doc.relative_to(ROOT)} → {match.group(1)}")
        elif not target.exists():
            fail(f"Missing target: {doc.relative_to(ROOT)} → {match.group(1)}")
        elif url.fragment and target.suffix.lower() == ".md" and unquote(url.fragment) not in anchors(target):
            fail(f"Missing anchor: {doc.relative_to(ROOT)} → {match.group(1)}")
    return count


def registry_entries(source: str) -> list[tuple[str, str, str]]:
    """(id, key, short name) in menu order, keys assigned as the app assigns them."""
    entries, progressive = [], 0
    for ident, short, fixed in ENTRY.findall(source):
        if not fixed:
            progressive += 1
        entries.append((ident, fixed or f"F{progressive}", short))
    return entries


def main() -> int:
    docs = [ROOT / "README.md", *sorted(GUIDE.rglob("*.md"))]
    links = sum(check_links(doc) for doc in docs)
    registry = (ROOT / "app/src/lib/navigation.ts").read_text(encoding="utf-8")
    entries = registry_entries(registry)
    if len(entries) != len(re.findall(r"^\s*\['", registry, re.MULTILINE)):
        fail("Unreadable navigation registry entry: update the handbook checker.")
    if [(ident, key) for ident, key, _ in entries] != [(ident, key) for ident, key, _ in EXPECTED]:
        fail("Navigation order or keys changed: update the handbook inventory.")
    keys = [key for _, key, _ in EXPECTED]
    if len(set(keys)) != COUNT or sorted(int(key[1:]) for key in keys) != list(range(1, COUNT + 1)):
        fail(f"Function keys must be unique and cover F1..F{COUNT}.")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    handbook = (GUIDE / "README.md").read_text(encoding="utf-8")
    pages = list((GUIDE / "pages").glob("*.md"))
    if len(pages) != COUNT:
        fail(f"Expected {COUNT} page chapters, found {len(pages)}.")
    readme_at, handbook_at = [], []
    for _, key, slug in EXPECTED:
        doc = GUIDE / "pages" / f"{slug}.md"
        svg = ASSETS / f"{slug}.svg"
        if not slug.startswith(f"{int(key[1:]):02d}-"):
            fail(f"Chapter slug does not repeat its key: {key} {slug}")
        if not doc.exists() or not svg.exists():
            fail(f"Missing {key} chapter or mock: {slug}")
            continue
        if not doc.read_text(encoding="utf-8").startswith(f"# {key} —"):
            fail(f"Incorrect function label: {slug}")
        row = re.compile(rf"^\| {key} \| .*\({re.escape('docs/guide/pages/' + slug + '.md')}\)", re.MULTILINE)
        found = row.search(readme)
        if not found:
            fail(f"README inventory mismatch for {key}")
        else:
            readme_at.append(found.start())
        found = re.search(rf"^\| {key} \| .*\({re.escape('pages/' + slug + '.md')}\)", handbook, re.MULTILINE)
        if not found:
            fail(f"Handbook omits {key}")
        else:
            handbook_at.append(found.start())
    if readme_at != sorted(readme_at) or handbook_at != sorted(handbook_at):
        fail("Inventory tables must list destinations in menu order.")
    svgs = sorted(ASSETS.glob("*.svg"))
    if len(svgs) != COUNT:
        fail(f"Expected {COUNT} synthetic SVG assets, found {len(svgs)}.")
    for path in svgs:
        raw = path.read_text(encoding="utf-8")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            fail(f"Invalid SVG {path.name}: {exc}")
            continue
        if root.tag != "{http://www.w3.org/2000/svg}svg" or root.get("viewBox") != "0 0 1280 900":
            fail(f"Unexpected SVG root or bounds: {path.name}")
        nav = root.find("{http://www.w3.org/2000/svg}g[@id='navigation']")
        labels = [] if nav is None else [node.text for node in nav.findall("{http://www.w3.org/2000/svg}text")]
        expected_labels = [label for _, key, short in entries for label in (key, short)]
        if labels != expected_labels:
            fail(f"Top navigation does not match source registry: {path.name}")
        title = root.find("{http://www.w3.org/2000/svg}title")
        desc = root.find("{http://www.w3.org/2000/svg}desc")
        if title is None or "DEMO" not in (title.text or "") or desc is None or "invented" not in (desc.text or ""):
            fail(f"Missing accessible synthetic-data declaration: {path.name}")
        for node in root.iter():
            name = node.tag.rsplit("}", 1)[-1]
            if name in {"script", "image", "foreignObject", "a", "use", "iframe"}:
                fail(f"Unexpected executable, linked or raster content: {path.name}/{name}")
            for attr, value in node.attrib.items():
                key = attr.rsplit("}", 1)[-1].lower()
                if key.startswith("on") or key == "href" or "url(" in value.lower():
                    fail(f"Unexpected link/event reference: {path.name}/{attr}")
        if "base64" in raw.lower() or re.search(r"(?i)(?:file:|https?:)//", raw.replace("http://www.w3.org/2000/svg", "")):
            fail(f"Unexpected external content: {path.name}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"OK: {len(docs)} Markdown documents, {links} local links, {COUNT} ordered destinations, {len(svgs)} accessible synthetic SVG assets.")
    print("This checks documentation structure; it does not certify app behavior, provider access or private-data exclusion across the repository.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
