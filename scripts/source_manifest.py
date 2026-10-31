"""Build an auditable source manifest for the Clough/Dean corpus tree.

Every file under the corpus root is listed exactly once with its relative path,
SHA-256, corpus, processing tag, and an include/exclude decision with a reason.
Exact-checksum duplicates and same-stem alternate formats are reported; distinct
teaching material is never dropped silently.

Usage:
  python scripts/source_manifest.py --root "<corpus root>" --output manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

# Files known to carry no lesson content. Keyed by lowercase filename.
AUXILIARY_FILES = {
    "bf_intro_to_transcripts.doc": "auxiliary: transcriber's acknowledgement note, no lesson content or metadata",
    "scripture_index_new.htm": "auxiliary: generated scripture index page",
    "jude.html": "auxiliary: Jude series index page (lesson titles/dates only, ~217 words)",
    "1995-bf-coursenotes-01.pdf": (
        "auxiliary: 3-page Part 1 cover sheet (~315 words: cover, blank page, one rationale "
        "paragraph); below course-notes minimum content, Part 1 is covered by intro transcripts"
    ),
}
# Same lesson exported twice under different names (not byte-identical, so the
# checksum pass cannot see them). Keyed by manifest path -> (kept copy, evidence).
NEAR_DUPLICATES = {
    "dean/transcripts_books_bible/New_Test/Gal/1999-Galatians-063.htm": (
        "dean/transcripts_books_bible/New_Test/Gal/1998-Galatians-063.htm",
        "same Lesson #063 (Aug 29, 1999); 5-gram Jaccard 1.00",
    ),
    "dean/transcripts_books_bible/New_Test/Heb/Heb_152.htm": (
        "dean/transcripts_books_bible/New_Test/Heb/Heb152.htm",
        "same Hebrews Lesson 152; 5-gram Jaccard 1.00",
    ),
    "dean/transcripts_books_bible/Old_Test/Samuel/2015-Samuel-065c.htm": (
        "dean/transcripts_books_bible/Old_Test/Samuel/2015-Samuel-065b.htm",
        "revision of Samuel Lesson #065 (Sep 27, 2016); 5-gram Jaccard 0.99",
    ),
}
SYSTEM_NAMES = {".ds_store", "thumbs.db", "filelist.xml"}

DEAN_TAG_BY_SUFFIX = {
    ".htm": "dean_topical_html",
    ".html": "dean_topical_html",
    ".pdf": "dean_topical_pdfs",
    ".doc": "dean_topical_doc",
    ".docx": "dean_topical_doc",
    ".rtf": "dean_topical_rtf",
}

# When identical files appear under several tags, keep the copy whose tag carries
# the richest citation metadata (e.g. Bible-book lessons over topical re-filings).
DUPLICATE_KEEP_PRIORITY = [
    "transcripts",
    "course_notes",
    "dean_bible_books",
    "dean_topical_html",
    "dean_topical_pdfs",
    "dean_topical_doc",
    "dean_topical_rtf",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


_PLACEHOLDER_RE = re.compile(r"this\s+lesson\s+is\s+not\s+available", re.IGNORECASE)


def _is_unavailable_placeholder(path: Path) -> bool:
    """HTML pages that only say the lesson is unavailable (no teaching content)."""
    if path.suffix.lower() not in {".htm", ".html"}:
        return False
    raw = path.read_bytes().decode("utf-8", "ignore")
    raw = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return bool(_PLACEHOLDER_RE.search(text)) and len(text.split()) < 40


def classify(rel: PurePosixPath) -> tuple[str | None, str | None, str]:
    """Return (corpus, tag, reason). tag None means excluded."""
    parts = rel.parts
    name = rel.name.lower()
    suffix = rel.suffix.lower()

    if name in SYSTEM_NAMES or name.startswith("._"):
        return None, None, "system: OS/editor metadata file"
    if any(part.endswith("_files") for part in parts[:-1]):
        return None, None, "auxiliary: HTML export asset folder"
    if parts[0] == "tmp":
        return None, None, "scratch: temporary working directory"
    if parts[0] == "clough_merged":
        return "clough", None, "derived: merged/split copy of the individual transcripts"
    if str(rel) in NEAR_DUPLICATES:
        kept, evidence = NEAR_DUPLICATES[str(rel)]
        return "dean", None, f"duplicate: near-identical re-export of {kept} ({evidence})"
    if name in AUXILIARY_FILES:
        corpus = "clough" if parts[0] == "clough" else "dean"
        return corpus, None, AUXILIARY_FILES[name]

    if parts[0] == "clough":
        if parts[1:3] == ("transcripts", "docs") and suffix in {".doc", ".docx"}:
            return "clough", "transcripts", "original transcript (canonical DOC representation)"
        if parts[1:3] == ("transcripts", "pdf"):
            return "clough", None, "alternate_format: PDF rendition of a DOC transcript"
        if parts[1] == "course_notes" and suffix == ".pdf":
            return "clough", "course_notes", "course notes volume"
        return "clough", None, f"unsupported: no Clough strategy for {suffix or 'extensionless'} here"

    if parts[0] == "dean":
        if len(parts) > 1 and parts[1] == "transcripts_books_bible":
            if suffix in {".htm", ".html"}:
                return "dean", "dean_bible_books", "Bible-book lesson HTML"
            return "dean", None, f"unsupported: {suffix or 'extensionless'} in Bible-book tree"
        if len(parts) > 1 and parts[1] == "topical_studies":
            tag = DEAN_TAG_BY_SUFFIX.get(suffix)
            if tag:
                return "dean", tag, f"topical study ({suffix.lstrip('.')})"
            return "dean", None, f"unsupported: {suffix or 'extensionless'} in topical tree"
    return None, None, "unknown: outside known corpus layout"


def build_manifest(root: Path) -> dict:
    entries: list[dict] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = PurePosixPath(path.relative_to(root).as_posix())
        corpus, tag, reason = classify(rel)
        if tag is not None and _is_unavailable_placeholder(path):
            tag, reason = None, "auxiliary: placeholder page ('This lesson is not available'), no teaching content"
        near_dup = NEAR_DUPLICATES.get(str(rel))
        entries.append(
            {
                "path": str(rel),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "corpus": corpus,
                "tag": tag,
                "include": tag is not None,
                "reason": reason,
                **({"duplicate_of": near_dup[0]} if near_dup else {}),
            }
        )

    # Exact duplicates among included sources: keep the first, exclude the rest.
    by_checksum: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        if entry["include"]:
            by_checksum[entry["sha256"]].append(entry)
    for group in by_checksum.values():
        group.sort(key=lambda e: (DUPLICATE_KEEP_PRIORITY.index(e["tag"]), e["path"]))
        for dup in group[1:]:
            dup["include"] = False
            dup["duplicate_of"] = group[0]["path"]
            dup["reason"] = f"duplicate: identical checksum to {group[0]['path']}"

    # Same directory + same stem in different formats: flag for review, keep both.
    by_stem: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for entry in entries:
        if entry["include"]:
            rel = PurePosixPath(entry["path"])
            by_stem[(str(rel.parent), rel.stem.lower())].append(entry)
    for group in by_stem.values():
        if len(group) > 1:
            for entry in group:
                entry["suspected_duplicates"] = [other["path"] for other in group if other is not entry]

    included = [e for e in entries if e["include"]]
    summary: dict[str, dict[str, int]] = {"included_by_tag": {}, "excluded_by_reason": {}}
    for entry in entries:
        if entry["include"]:
            key = f"{entry['corpus']}/{entry['tag']}"
            summary["included_by_tag"][key] = summary["included_by_tag"].get(key, 0) + 1
        else:
            key = entry["reason"].split(":", 1)[0]
            summary["excluded_by_reason"][key] = summary["excluded_by_reason"].get(key, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "total_files": len(entries),
        "included_files": len(included),
        "summary": summary,
        "suspected_duplicate_groups": sum(1 for g in by_stem.values() if len(g) > 1),
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, help="Corpus root containing clough/ and dean/")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest = build_manifest(Path(args.root).resolve())
    Path(args.output).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("total_files", "included_files", "summary", "suspected_duplicate_groups")}, indent=2))


if __name__ == "__main__":
    main()
