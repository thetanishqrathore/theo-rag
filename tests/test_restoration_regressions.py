"""Regression tests for defects repaired while restoring the Phase 1 baseline."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Portable paths -----------------------------------------------------------


def test_no_blanked_machine_paths_in_tests_or_eval_wrappers() -> None:
    offenders = []
    for path in list((ROOT / "tests").glob("*.py")) + list((ROOT / "scripts").glob("*.sh")):
        if path.resolve() == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8")
        if 'ROOT = Path("")' in text or 'ROOT_DIR=""' in text:
            offenders.append(path.name)
    assert offenders == []


# --- Model response handling ----------------------------------------------------


def test_stream_answer_events_keeps_reasoning_out_of_answer() -> None:
    import src.generation.generate as gen

    chunks = [
        {"message": {"role": "assistant", "content": "", "thinking": "The user asks"}},
        {"message": {"role": "assistant", "content": "", "thinking": " about faith-rest."}},
        {"message": {"role": "assistant", "content": "Faith-rest is "}},
        {"message": {"role": "assistant", "content": "a drill [chunk:abc]."}},
        {"message": {"role": "assistant", "content": ""}, "done": True},
    ]
    events = list(gen._stream_answer_events(chunks, heartbeat_sec=0))

    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "Faith-rest is a drill [chunk:abc]."
    assert all("user asks" not in str(e) for e in events)
    statuses = [e for e in events if e["type"] == "status"]
    assert statuses and all(e["phase"] == "generating" for e in statuses)
    assert {e["type"] for e in events} <= {"token", "status"}


def test_stream_answer_events_rate_limits_reasoning_heartbeats() -> None:
    import src.generation.generate as gen

    chunks = [{"message": {"content": "", "thinking": "x"}} for _ in range(50)]
    events = list(gen._stream_answer_events(chunks, heartbeat_sec=3600))
    assert events == []


def test_helper_calls_reuse_primary_context_to_avoid_reloads(monkeypatch) -> None:
    import src.utils.ollama_client as oc

    cfg = SimpleNamespace(ollama_model="gpt-oss:120b", ollama_num_ctx=32768)
    monkeypatch.setattr(oc, "get_config", lambda: cfg)
    assert oc.helper_num_ctx("gpt-oss:120b", 2048) == 32768
    assert oc.helper_num_ctx("llama3.1:8b", 2048) == 2048


def test_warmup_is_bounded_and_uses_primary_context(monkeypatch) -> None:
    import src.utils.ollama_client as oc

    calls: list[dict] = []

    class FakeClient:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return {"response": ""}

    cfg = SimpleNamespace(ollama_model="gpt-oss:120b", ollama_num_ctx=32768, ollama_timeout=900)
    monkeypatch.setattr(oc, "get_config", lambda: cfg)
    monkeypatch.setattr(oc, "get_ollama_client", lambda timeout=None: FakeClient())
    oc.warm_ollama_models(["gpt-oss:120b", ""], keep_alive="30m")

    assert len(calls) == 1  # empty secondary model is skipped
    assert calls[0]["options"]["num_ctx"] == 32768
    assert calls[0]["options"]["num_predict"] == 1


def test_normalize_search_text_folds_model_typography() -> None:
    from src.utils.text import normalize_search_text

    assert normalize_search_text("faith‑rest drill in Hebrews 4") == "faith-rest drill in Hebrews 4"
    assert normalize_search_text("plain-ascii text") == "plain-ascii text"


# --- Static / API routing -------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>shell</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "vite.svg").write_text("<svg/>")
    canonical = tmp_path / "canonical"
    (canonical / "clough").mkdir(parents=True)
    (canonical / "clough" / "lesson.html").write_text("<p>evidence</p>")
    (tmp_path / "secret.txt").write_text("nope")

    monkeypatch.setenv("FRONTEND_DIST_PATH", str(dist))
    monkeypatch.setenv("CANONICAL_PATH", str(canonical))
    import src.utils.config as config_module

    config_module.get_config.cache_clear()
    sys.modules.pop("src.api.main", None)
    main = importlib.import_module("src.api.main")
    # No `with`: startup (model loading) must not run in unit tests.
    yield TestClient(main.app)
    sys.modules.pop("src.api.main", None)
    config_module.get_config.cache_clear()


def test_frontend_shell_and_assets_are_served(app_client) -> None:
    root = app_client.get("/")
    assert root.status_code == 200 and "shell" in root.text
    assert root.headers.get("cache-control") == "no-cache"
    assert app_client.get("/assets/app.js").text == "console.log(1)"
    assert app_client.get("/vite.svg").text == "<svg/>"
    deep_link = app_client.get("/some/client/route")
    assert deep_link.status_code == 200 and "shell" in deep_link.text


def test_api_and_evidence_routes_are_not_shadowed(app_client) -> None:
    assert app_client.get("/static/clough/lesson.html").text == "<p>evidence</p>"
    missing_api = app_client.get("/api/does-not-exist")
    assert missing_api.status_code == 404
    assert "shell" not in missing_api.text
    assert app_client.get("/static/missing.html").status_code == 404


def test_frontend_route_does_not_escape_dist(app_client) -> None:
    response = app_client.get("/..%2fsecret.txt")
    assert "nope" not in response.text


# --- Source manifest ------------------------------------------------------------


def test_manifest_classification_rules() -> None:
    sm = _load_script("source_manifest")
    classify = lambda p: sm.classify(PurePosixPath(p))  # noqa: E731

    assert classify("clough/transcripts/docs/bf-001_transcript.doc")[1] == "transcripts"
    assert classify("clough/transcripts/pdf/bf-001_transcript.pdf")[1] is None
    assert classify("clough/transcripts/docs/bf_Intro_to_transcripts.doc")[2].startswith("auxiliary")
    assert classify("clough_merged/doc/all.docx")[2].startswith("derived")
    assert classify("dean/transcripts_books_bible/New_Test/1_Cor/SCRIPTURE_INDEX_NEW.htm")[1] is None
    assert classify("dean/transcripts_books_bible/Old_Test/Kings/Kings-39b_files/filelist.xml")[1] is None
    assert classify("dean/transcripts_books_bible/New_Test/Acts/2010-Acts-001.htm")[1] == "dean_bible_books"
    assert classify("dean/topical_studies/Salvation/Salvation-001.rtf")[1] == "dean_topical_rtf"
    assert classify("dean/topical_studies/pdf/Walking/Walking.pdf")[1] == "dean_topical_pdfs"
    assert classify("dean/topical_studies/.DS_Store")[1] is None


def test_manifest_keeps_bible_book_copy_of_exact_duplicates(tmp_path) -> None:
    sm = _load_script("source_manifest")
    bible = tmp_path / "dean" / "transcripts_books_bible" / "New_Test" / "Rev"
    topical = tmp_path / "dean" / "topical_studies" / "html" / "Worship"
    bible.mkdir(parents=True)
    topical.mkdir(parents=True)
    (bible / "2004-Revelation-095b.htm").write_text("same lesson")
    (topical / "2006-WorshipAndMusic-001.htm").write_text("same lesson")

    manifest = sm.build_manifest(tmp_path)
    by_path = {e["path"]: e for e in manifest["entries"]}
    kept = by_path["dean/transcripts_books_bible/New_Test/Rev/2004-Revelation-095b.htm"]
    dropped = by_path["dean/topical_studies/html/Worship/2006-WorshipAndMusic-001.htm"]
    assert kept["include"] is True
    assert dropped["include"] is False
    assert dropped["duplicate_of"] == kept["path"]


# --- Processing failures / resumability ------------------------------------------


def test_stage_exception_rolls_back_and_is_reported(tmp_path, monkeypatch) -> None:
    bc = _load_script("build_corpus")

    class FakeSession:
        rolled_back = 0

        def rollback(self):
            FakeSession.rolled_back += 1

    def boom(_lesson_id, _session, _tag):
        raise RuntimeError("extractor crashed")

    def ok(_lesson_id, _session, _tag):
        return {"status": "success", "message": "fine"}

    monkeypatch.setattr(bc, "_stage_functions", lambda: [("extract", "pending", None, boom), ("structure", "extracted", None, ok)])
    monkeypatch.setattr(bc, "_lesson_ids", lambda _s, _tag, status, _c, _n=None: [7] if status == "pending" else [])
    reporter = bc.Reporter(tmp_path, "run")
    bc.run_stages(FakeSession(), "transcripts", set(), reporter)

    assert FakeSession.rolled_back == 1
    assert reporter.counts == {"extract": {"ok": 0, "failed": 1}}
    assert "extractor crashed" in reporter.failures[0]["message"]
    assert (tmp_path / "run.jsonl").read_text().count("\n") == 1


def test_validation_failure_lists_failed_checks(tmp_path, monkeypatch) -> None:
    bc = _load_script("build_corpus")

    def failed_validation(_lesson_id, _session, _tag):
        return {
            "status": "failed",
            "message": "Validation checks failed",
            "checks": {"minimum_content": {"passed": False}, "reading_flow": {"passed": True}},
        }

    monkeypatch.setattr(bc, "_stage_functions", lambda: [("validate", "structured", None, failed_validation)])
    monkeypatch.setattr(bc, "_lesson_ids", lambda *_args: [3])
    reporter = bc.Reporter(tmp_path, "run")
    bc.run_stages(SimpleNamespace(rollback=lambda: None), "course_notes", set(), reporter)

    assert reporter.counts["validate"]["failed"] == 1
    assert "minimum_content" in reporter.failures[0]["message"]
    assert "reading_flow" not in reporter.failures[0]["message"]


def test_migration_runner_detects_explicit_transactions() -> None:
    migrate = _load_script("migrate")
    assert migrate._has_txn_control("BEGIN;\nSELECT 1;\nCOMMIT;")
    assert not migrate._has_txn_control("CREATE TABLE IF NOT EXISTS x (id int);")


def test_resolve_chunk_refs_expands_unambiguous_prefixes() -> None:
    import src.generation.generate as gen

    source_map = {
        "37e5186f-c6fb-4bab-944b-abad813b93ea": {},
        "36fbaa82-9d28-404c-80a7-75a4d5c8c9ef": {},
        "__order__": [],
    }
    text = "Claim one [chunk:37e5186f]. Claim two [chunk:36fbaa82‑9d28‑404c‑80a7‑75a4d5c8c9ef]."
    resolved = gen.resolve_chunk_refs(text, source_map)
    assert "[chunk:37e5186f-c6fb-4bab-944b-abad813b93ea]" in resolved
    assert "[chunk:36fbaa82-9d28-404c-80a7-75a4d5c8c9ef]" in resolved


def test_resolve_chunk_refs_leaves_unknown_and_ambiguous_refs() -> None:
    import src.generation.generate as gen

    source_map = {
        "abcdef12-0000-0000-0000-000000000001": {},
        "abcdef12-0000-0000-0000-000000000002": {},
    }
    text = "Ambiguous [chunk:abcdef12]. Unknown [chunk:deadbeef]. Short [chunk:abcdef1]."
    assert gen.resolve_chunk_refs(text, source_map) == text


def test_course_note_selection_skips_engine_that_leaks_page_headers(monkeypatch) -> None:
    import src.processing.extract_course_notes as ecn

    def candidate(engine, score, texts):
        return SimpleNamespace(engine=engine, score=score, blocks=texts)

    # Rows stand in for derived paragraphs; "Page ii" (roman numerals) counts, as in validation.
    leaky = candidate("pymupdf", 0.79, ["Body Lesson 159 Page 17 Part VI", "Part VI _ Page ii"] + ["clean"] * 8)
    clean = candidate("docling", 0.74, ["clean paragraph"] * 10)
    monkeypatch.setattr(ecn, "_blocks_to_topical_nodes", lambda blocks, profile: (blocks, []))
    monkeypatch.setattr(ecn, "derive_paragraph_rows_from_nodes", lambda nodes: [{"text": t} for t in nodes])
    monkeypatch.setattr(ecn, "get_config", lambda: SimpleNamespace(
        validation_profile_thresholds=lambda: {"clough_course_notes_v2": {"leak_warn": 0.12}}
    ))

    selected, reason = ecn._select_course_note_candidate([leaky, clean], "default")
    assert selected.engine == "docling"
    assert reason == "page_marker_leakage_skipped:pymupdf"

    # When the best candidate is clean, the plain best score still wins.
    selected, reason = ecn._select_course_note_candidate([candidate("pymupdf", 0.8, ["clean"] * 10), clean], "default")
    assert selected.engine == "pymupdf" and reason == "highest_structure_quality_score"


def test_topical_book_is_topic_folder_not_format_folder() -> None:
    from src.ingestion.shared import resolve_dean_book

    def book(path):
        return resolve_dean_book(Path(path), "topical_studies", "topical_studies")[0]

    assert book("/c/dean/topical_studies/html/Tabernacle/2008-Tabernacle-001.htm") == "Tabernacle"
    assert book("/c/dean/topical_studies/pdf/Walking/Walking.pdf") == "Walking"
    assert book("/c/dean/topical_studies/doc/Faith/Faith.doc") == "Faith"
    # Topic-first layouts are unchanged.
    assert book("/c/dean/topical_studies/Salvation/Salvation-001.rtf") == "Salvation"
    assert resolve_dean_book(
        Path("/c/dean/transcripts_books_bible/New_Test/John/Jno015.htm"), "transcripts_books_bible", "bible_books"
    )[0] == "John"


def test_dean_semantic_html_blocks_exclude_inline_scripts() -> None:
    from src.processing.extract_dean_html import _extract_semantic_blocks

    html = (
        "<html><head><style>p.MsoNormal {margin:0}</style></head><body>"
        '<div id="overDiv"><script language="JavaScript">var overlib = "tooltip library code";</script></div>'
        '<p class=MsoNormal>Israel\'s religious calendar is divided in two.</p>'
        "</body></html>"
    )
    texts = [block.text for block in _extract_semantic_blocks(html)]
    assert any("religious calendar" in t for t in texts)
    assert not any("overlib" in t.lower() for t in texts)


def test_reading_flow_ignores_repeated_outline_headings() -> None:
    from src.processing.validate import check_reading_flow

    def para(text, idx):
        return SimpleNamespace(content=text, is_junk=False, paragraph_index=idx, word_count=len(text.split()))

    headings = ["I. INTRODUCTION.", "II. THE PHILOSOPHICAL SETTING OF THE POST-REFORMATION AGE.", "VI. CONCLUSION."]
    body = [f"Substantive paragraph number {i} discussing the doctrine of Scripture in some detail here." for i in range(20)]
    outline_doc = [para(t, i) for i, t in enumerate(headings + body + headings)]
    ok, _ = check_reading_flow(outline_doc, profile="chafer_lesson_pdf")
    assert ok

    repeated = body[:10] + body[:10]
    ok, message = check_reading_flow([para(t, i) for i, t in enumerate(repeated)], profile="chafer_lesson_pdf")
    assert not ok and "Duplicate" in message


def test_accepted_validation_exceptions_are_scoped_and_recorded(monkeypatch) -> None:
    import src.processing.validate as v

    class FakeSession:
        def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: "2008-Chafer-HistoryOfDoctrine-Paper-026.pdf")

    monkeypatch.setattr(v, "get_config", lambda: SimpleNamespace(
        validation_accepted_exceptions='{"2008-Chafer-HistoryOfDoctrine-Paper-026.pdf": ["heading_list_coherence"]}'
    ))
    checks = {
        "heading_list_coherence": (False, "6 abrupt heading depth jumps"),
        "reading_flow": (False, "Duplicate paragraph content detected"),
    }
    accepted = v._apply_accepted_exceptions(SimpleNamespace(source_file_id="x"), FakeSession(), checks)

    assert accepted == ["heading_list_coherence"]
    assert checks["heading_list_coherence"][0] is True
    assert "6 abrupt heading depth jumps" in checks["heading_list_coherence"][1]
    assert checks["reading_flow"][0] is False  # unlisted checks still fail


def test_dean_lesson_overrides_keep_basis_and_reason(monkeypatch) -> None:
    import src.utils.config as config_module
    from src.ingestion.shared import dean_lesson_override

    cfg = SimpleNamespace(dean_lesson_overrides=(
        '{"Jno015.htm": {"lesson_number": 14, "basis": "inferred_passage_order", "reason": "John 1:19-28"},'
        ' "Legacy.htm": 7}'
    ))
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)
    assert dean_lesson_override("New_Test/John/Jno015.htm", "Jno015.htm") == {
        "lesson_number": 14, "basis": "inferred_passage_order", "reason": "John 1:19-28",
    }
    assert dean_lesson_override("Legacy.htm") == {"lesson_number": 7, "basis": "manual", "reason": None}
    assert dean_lesson_override("Other.htm") is None
