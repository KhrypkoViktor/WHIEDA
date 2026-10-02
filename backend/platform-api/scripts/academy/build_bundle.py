"""Build an Academy course bundle from an Obsidian folder.

    python scripts/academy/build_bundle.py "D:/Obsidian/WHIEDA/Basic_course" > bundle.json

The folder holds Markdown lessons and `_academy_course.json` (order, modules,
slugs). Per lesson:
- title    — the H1 without «Урок N.»;
- result   — the «**Результат урока:**» line; minutes — the «**Время:**» line;
- checklist — «- [ ] …» items of the «## Чек-лист» section (rendered by the
  site as checkboxes, removed from the body);
- «> [!note] Для Виктора …» callouts and «Версия:/Формат:/Основа:» preamble
  lines never reach the site;
- «урок N» references become «урок «Короткое название»» — lesson numbers change
  with the course order, names do not;
- `from_heading` in the manifest keeps only the part from that H2 onwards;
- pictures (`![…](img/…)`) travel inside the bundle (`media`: path → mime + base64);
  `load_bundle.py` puts them into `academy_media` (Academy v2, 02.10.2026) — the site
  repository no longer keeps lesson screenshots. A missing picture stops the build.

Needs `pip install markdown` on the machine that builds; Core only loads JSON.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import markdown

OLD_NUMBER_RE = re.compile(r"^#\s*Урок\s+(\d+)\.", re.M)
H1_RE = re.compile(r"^#\s+(.+)$", re.M)
RESULT_RE = re.compile(r"^\*\*Результат урока:\*\*\s*(.+)$", re.M)
TIME_RE = re.compile(r"^\*\*Время:\*\*\s*(.+)$", re.M)
PREAMBLE_RE = re.compile(r"^(Версия|Формат|Основа|Статус)\b.*$", re.M)
LESSON_REF_RE = re.compile(r"\b([Уу]рок(?:а|е|у|ом|и)?)\s+(\d{1,2})\b")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="(img/[^"]+)"')
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


def _embed_pictures(folder: Path, source_file: str, body_html: str, media: dict) -> None:
    for path in IMG_SRC_RE.findall(body_html):
        if path in media:
            continue
        file = folder / path
        mime = IMAGE_MIME.get(file.suffix.lower())
        if not file.is_file():
            raise SystemExit(f"{source_file}: нет картинки {path}")
        if not mime:
            raise SystemExit(f"{source_file}: картинка {path} — не png/jpg/webp/gif")
        media[path] = {"mime": mime, "data_b64": base64.b64encode(file.read_bytes()).decode("ascii")}


def _strip_owner_callouts(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines():
        if re.match(r"^>\s*\[!\w+\]\s*Для Виктора", line):
            skipping = True
            continue
        if skipping:
            if line.startswith(">"):
                continue
            skipping = False
        out.append(re.sub(r"^>\s*\[!\w+\]\s*", "> ", line))
    return "\n".join(out)


def _split_checklist(text: str) -> tuple[str, list[str]]:
    match = re.search(r"^#{2,3}\s+Чек-лист\s*$", text, re.M)
    if not match:
        return text, []
    rest = text[match.end():]
    nxt = re.search(r"^#{2,3}\s+", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest
    items = [m.group(1).strip() for m in re.finditer(r"^\s*-\s*\[[ xX]\]\s*(.+)$", section, re.M)]
    body = text[: match.start()] + (rest[nxt.start():] if nxt else "")
    return body, items


def _minutes(raw: str | None) -> int | None:
    if not raw:
        return None
    found = re.search(r"(\d+)\s*мин", raw)
    return int(found.group(1)) if found else None


def build(folder: Path) -> dict:
    manifest = json.loads((folder / "_academy_course.json").read_text(encoding="utf-8"))
    entries = []
    for module in manifest["modules"]:
        for item in module["lessons"]:
            source = (folder / item["file"]).read_text(encoding="utf-8")
            entries.append((module["title"], item, source))

    # Старые номера уроков → короткие названия для ссылок «урок N».
    short_by_old: dict[int, str] = {}
    short_by_file: dict[str, str] = {}
    for _, item, source in entries:
        h1 = H1_RE.search(source)
        title = re.sub(r"^Урок(?:\s+\d+)?\.\s*", "", h1.group(1).strip()) if h1 else item["slug"]
        short = item.get("short") or title.split(":")[0].strip()
        item["_title"] = item.get("title") or title
        item["_short"] = short
        old = OLD_NUMBER_RE.search(source)
        if old:
            short_by_old[int(old.group(1))] = short
        short_by_file[Path(item["file"]).stem] = short

    lessons = []
    media: dict[str, dict] = {}
    for position, (module_title, item, source) in enumerate(entries, start=1):
        text = source
        if item.get("from_heading"):
            start = re.search(rf"^##\s+{re.escape(item['from_heading'])}.*$", text, re.M)
            if not start:
                raise SystemExit(f"{item['file']}: нет раздела «{item['from_heading']}»")
            # Раздел был H2 с подразделами H3 — на сайте подразделы становятся H2.
            text = re.sub(r"^###(?=\s)", "##", text[start.end():], flags=re.M)
        result = RESULT_RE.search(source)
        minutes = TIME_RE.search(source)
        text = H1_RE.sub("", text, count=1)
        text = RESULT_RE.sub("", text)
        text = TIME_RE.sub("", text)
        text = PREAMBLE_RE.sub("", text)
        text = _strip_owner_callouts(text)
        text, checklist = _split_checklist(text)

        def _ref(m: re.Match) -> str:
            name = short_by_old.get(int(m.group(2)))
            return f"{m.group(1)} «{name}»" if name else m.group(0)

        text = LESSON_REF_RE.sub(_ref, text)
        text = WIKILINK_RE.sub(lambda m: f"«{short_by_file.get(m.group(1).strip(), m.group(2) or m.group(1))}»", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        # Разделители «---» в начале/конце урока на сайте лишние.
        text = re.sub(r"^(?:---\s*\n)+|(?:\n---\s*)+$", "", text).strip()
        body_html = markdown.markdown(text, extensions=["tables", "sane_lists"], output_format="html")
        # Скриншоты урока лежат в Obsidian рядом (img/…) и едут в бандле → academy_media;
        # внешние ссылки — в новой вкладке.
        _embed_pictures(folder, item["file"], body_html, media)
        body_html = re.sub(r'<a href="(https?://[^"]+)">', r'<a href="\1" target="_blank" rel="noopener">', body_html)
        lessons.append(
            {
                "slug": item["slug"],
                "module_title": module_title,
                "position": position,
                "title": item["_title"],
                "short_title": item["_short"],
                "result_text": result.group(1).strip() if result else None,
                "est_minutes": _minutes(minutes.group(1) if minutes else None),
                "body_html": body_html,
                "checklist": checklist,
                "video": item.get("video"),
            }
        )
    return {
        "course": {
            "slug": manifest["slug"],
            "title": manifest["title"],
            "subtitle": manifest.get("subtitle"),
            "access_rule": manifest.get("access", "pro"),
        },
        "lessons": lessons,
        "media": media,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_bundle.py <obsidian course folder>")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(build(Path(sys.argv[1])), ensure_ascii=False, indent=1))
