"""Configuration loading for the RAG system."""

from dataclasses import dataclass
from functools import lru_cache
import json
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from src.corpus.registry import CorpusId


@dataclass(frozen=True)
class DatabaseConfig:
    """Database runtime settings."""

    url: str
    pool_size: int
    max_overflow: int
    pool_timeout: int
    pool_recycle: int
    pool_pre_ping: bool
    statement_timeout_ms: int


@dataclass(frozen=True)
class VectorStoreConfig:
    """Vector store and retrieval defaults."""

    host: str
    port: int
    expected_dim: int
    ef_search_fast: int
    ef_search_deep: int
    default_retrieval_mode: str
    default_retrieval_mode_by_corpus: dict[str, str]


@dataclass(frozen=True)
class SufficiencyThresholds:
    """Sufficiency thresholds for one corpus profile."""

    t_high: float
    t_mid: float
    t_low: float
    o_mid: float
    o_low: float


@dataclass(frozen=True)
class SufficiencyConfig:
    """Corpus-aware sufficiency threshold registry."""

    default: SufficiencyThresholds
    by_corpus: dict[str, SufficiencyThresholds]

    def for_corpus(self, corpus: str) -> SufficiencyThresholds:
        return self.by_corpus.get(corpus, self.default)


class AppConfig(BaseSettings):
    """Application configuration loaded from environment variables."""

    database_url: str = "postgresql://raguser:dev_password@localhost:5432/theology_rag"
    db_pool_size: int = Field(default=30, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=30, validation_alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, validation_alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(default=1800, validation_alias="DB_POOL_RECYCLE")
    db_pool_pre_ping: bool = Field(default=True, validation_alias="DB_POOL_PRE_PING")
    db_statement_timeout_ms: int = Field(default=30000, validation_alias="DB_STATEMENT_TIMEOUT_MS")
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:120b"
    secondary_ollama_model: str = ""
    ollama_timeout: int = 900
    ollama_keepalive: str = "5m"
    ollama_warm_on_startup: bool = True
    ollama_num_ctx: int = Field(default=32768, validation_alias="OLLAMA_NUM_CTX")
    embedding_model: str = "BAAI/bge-m3"
    embedding_use_prefix: bool = Field(default=False, validation_alias="EMBEDDING_USE_PREFIX")
    embedding_doc_prefix: str = Field(default="", validation_alias="EMBEDDING_DOC_PREFIX")
    embedding_query_prefix: str = Field(default="", validation_alias="EMBEDDING_QUERY_PREFIX")
    qdrant_expected_dim: int = Field(default=1024, validation_alias="QDRANT_EXPECTED_DIM")
    reranker_model: str = "BAAI/bge-reranker-large"
    raw_data_path: str = "data/raw"
    canonical_path: str = "data/canonical"
    log_path: str = "data/logs"
    log_level: str = "INFO"
    log_max_bytes: int = Field(default=50 * 1024 * 1024, validation_alias="LOG_MAX_BYTES")
    log_backup_count: int = Field(default=5, validation_alias="LOG_BACKUP_COUNT")
    corpus_name: CorpusId = "clough"
    default_tag: str = "transcripts"
    transcript_lesson_overrides: str = "{}"
    transcript_source_format: str = Field(default="doc", validation_alias="TRANSCRIPT_SOURCE_FORMAT")
    transcript_metadata_strict_mode: bool = Field(default=True, validation_alias="TRANSCRIPT_METADATA_STRICT_MODE")
    transcript_heading_boundary_enabled: bool = Field(
        default=True, validation_alias="TRANSCRIPT_HEADING_BOUNDARY_ENABLED"
    )
    transcript_doc_soffice_command: str = Field(
        default="soffice",
        validation_alias="TRANSCRIPT_DOC_SOFFICE_COMMAND",
    )
    transcript_doc_soffice_timeout_sec: int = Field(
        default=240,
        validation_alias="TRANSCRIPT_DOC_SOFFICE_TIMEOUT_SEC",
    )
    transcript_validation_profile: str = Field(
        default="clough_transcripts_doc_v3",
        validation_alias="TRANSCRIPT_VALIDATION_PROFILE",
    )
    transcript_validation_min_blocks: int = Field(default=6, validation_alias="TRANSCRIPT_VALIDATION_MIN_BLOCKS")
    transcript_validation_min_words: int = Field(default=250, validation_alias="TRANSCRIPT_VALIDATION_MIN_WORDS")
    transcript_dup_warn: float = Field(default=0.14, validation_alias="TRANSCRIPT_DUP_WARN")
    transcript_leak_warn: float = Field(default=0.12, validation_alias="TRANSCRIPT_LEAK_WARN")
    clough_course_notes_validation_min_words: int = Field(
        default=350,
        validation_alias="CLOUGH_COURSE_NOTES_VALIDATION_MIN_WORDS",
    )
    clough_course_notes_validation_min_blocks: int = Field(
        default=8,
        validation_alias="CLOUGH_COURSE_NOTES_VALIDATION_MIN_BLOCKS",
    )
    clough_course_notes_validation_dup_warn: float = Field(
        default=0.16,
        validation_alias="CLOUGH_COURSE_NOTES_VALIDATION_DUP_WARN",
    )
    clough_course_notes_validation_leak_warn: float = Field(
        default=0.12,
        validation_alias="CLOUGH_COURSE_NOTES_VALIDATION_LEAK_WARN",
    )
    validation_profile_threshold_overrides: str = Field(
        default="{}",
        validation_alias="VALIDATION_PROFILE_THRESHOLD_OVERRIDES",
    )
    # Reviewed per-source exceptions: {"<source filename>": ["<check name>", ...]}.
    # Listed checks are recorded as accepted exceptions instead of failing the lesson.
    validation_accepted_exceptions: str = Field(
        default="{}",
        validation_alias="VALIDATION_ACCEPTED_EXCEPTIONS",
    )
    transcript_citation_include_chapter: bool = Field(
        default=True, validation_alias="TRANSCRIPT_CITATION_INCLUDE_CHAPTER"
    )
    dean_lesson_overrides: str = Field(default="{}", validation_alias="DEAN_LESSON_OVERRIDES")
    dean_default_speaker: str = Field(default="Pastor Dean", validation_alias="DEAN_DEFAULT_SPEAKER")
    dean_html_parser_version: str = Field(default="v1", validation_alias="DEAN_HTML_PARSER_VERSION")
    dean_html_structure_mode: str = Field(default="node", validation_alias="DEAN_HTML_STRUCTURE_MODE")
    dean_bible_html_min_blocks: int = Field(default=6, validation_alias="DEAN_BIBLE_HTML_MIN_BLOCKS")
    dean_bible_html_min_words: int = Field(default=250, validation_alias="DEAN_BIBLE_HTML_MIN_WORDS")
    dean_topical_html_min_blocks: int = Field(default=8, validation_alias="DEAN_TOPICAL_HTML_MIN_BLOCKS")
    dean_topical_html_min_words: int = Field(default=450, validation_alias="DEAN_TOPICAL_HTML_MIN_WORDS")
    dean_pdf_header_footer_repeat_threshold: int = Field(
        default=3, validation_alias="DEAN_PDF_HEADER_FOOTER_REPEAT_THRESHOLD"
    )
    dean_pdf_min_words_default: int = Field(default=500, validation_alias="DEAN_PDF_MIN_WORDS_DEFAULT")
    dean_pdf_min_words_slides: int = Field(default=700, validation_alias="DEAN_PDF_MIN_WORDS_SLIDES")
    dean_pdf_min_words_legacy: int = Field(default=1500, validation_alias="DEAN_PDF_MIN_WORDS_LEGACY")
    dean_pdf_min_words_chafer: int = Field(default=1400, validation_alias="DEAN_PDF_MIN_WORDS_CHAFER")
    dean_pdf_min_words_article: int = Field(default=1200, validation_alias="DEAN_PDF_MIN_WORDS_ARTICLE")
    dean_pdf_force_engine: str = Field(default="", validation_alias="DEAN_PDF_FORCE_ENGINE")
    dean_pdf_marker_enabled: bool = Field(default=True, validation_alias="DEAN_PDF_MARKER_ENABLED")
    dean_pdf_marker_command: str = Field(default="marker_single", validation_alias="DEAN_PDF_MARKER_COMMAND")
    dean_pdf_marker_timeout_sec: int = Field(default=240, validation_alias="DEAN_PDF_MARKER_TIMEOUT_SEC")
    dean_pdf_scan_chars_per_page_target: int = Field(default=1600, validation_alias="DEAN_PDF_SCAN_CHARS_PER_PAGE_TARGET")
    dean_pdf_validation_shadow_llm: bool = Field(default=False, validation_alias="DEAN_PDF_VALIDATION_SHADOW_LLM")
    dean_pdf_shadow_llm_model: str = Field(default="llama3.1:8b", validation_alias="DEAN_PDF_SHADOW_LLM_MODEL")
    dean_pdf_shadow_llm_timeout_sec: int = Field(default=20, validation_alias="DEAN_PDF_SHADOW_LLM_TIMEOUT_SEC")
    dean_doc_soffice_command: str = Field(default="soffice", validation_alias="DEAN_DOC_SOFFICE_COMMAND")
    dean_doc_soffice_timeout_sec: int = Field(default=240, validation_alias="DEAN_DOC_SOFFICE_TIMEOUT_SEC")
    dean_doc_force_engine: str = Field(default="", validation_alias="DEAN_DOC_FORCE_ENGINE")
    dean_doc_min_words_default: int = Field(default=450, validation_alias="DEAN_DOC_MIN_WORDS_DEFAULT")
    dean_doc_min_words_outline: int = Field(default=500, validation_alias="DEAN_DOC_MIN_WORDS_OUTLINE")
    dean_doc_min_words_lecture: int = Field(default=1200, validation_alias="DEAN_DOC_MIN_WORDS_LECTURE")
    dean_topical_node_mode: bool = Field(default=True, validation_alias="DEAN_TOPICAL_NODE_MODE")
    dean_topical_markdown_strip_strict: bool = Field(
        default=True, validation_alias="DEAN_TOPICAL_MARKDOWN_STRIP_STRICT"
    )
    dean_topical_canonical_from_nodes: bool = Field(
        default=True, validation_alias="DEAN_TOPICAL_CANONICAL_FROM_NODES"
    )
    dean_topical_retrieval_mode: str = Field(default="node_aware", validation_alias="DEAN_TOPICAL_RETRIEVAL_MODE")
    dean_chunk_parent_node_max_words: int = Field(default=800, validation_alias="DEAN_CHUNK_PARENT_NODE_MAX_WORDS")
    dean_chunk_parent_node_target_words: int = Field(default=450, validation_alias="DEAN_CHUNK_PARENT_NODE_TARGET_WORDS")
    dean_chunk_child_heading_prefix: bool = Field(default=True, validation_alias="DEAN_CHUNK_CHILD_HEADING_PREFIX")
    dean_topical_table_parser_enabled: bool = Field(
        default=True, validation_alias="DEAN_TOPICAL_TABLE_PARSER_ENABLED"
    )
    course_notes_node_mode: bool = Field(default=True, validation_alias="COURSE_NOTES_NODE_MODE")
    course_notes_canonical_from_nodes: bool = Field(
        default=True, validation_alias="COURSE_NOTES_CANONICAL_FROM_NODES"
    )
    course_notes_front_matter_penalty: bool = Field(
        default=True, validation_alias="COURSE_NOTES_FRONT_MATTER_PENALTY"
    )
    course_notes_front_matter_penalty_factor: float = Field(
        default=0.85, validation_alias="COURSE_NOTES_FRONT_MATTER_PENALTY_FACTOR"
    )
    clough_source_diversity_cap: bool = Field(default=True, validation_alias="CLOUGH_SOURCE_DIVERSITY_CAP")
    clough_source_diversity_max_per_tag: int = Field(
        default=3, validation_alias="CLOUGH_SOURCE_DIVERSITY_MAX_PER_TAG"
    )
    course_notes_engine_force: str = Field(default="", validation_alias="COURSE_NOTES_ENGINE_FORCE")
    rag_chunk_child_sentence_snap: bool = Field(default=False, validation_alias="RAG_CHUNK_CHILD_SENTENCE_SNAP")
    rag_chunk_parent_smart_boundary: bool = Field(default=False, validation_alias="RAG_CHUNK_PARENT_SMART_BOUNDARY")
    rag_rerank_weighting: bool = Field(default=True, validation_alias="RAG_RERANK_WEIGHTING")
    rag_retrieve_parent_diversity: bool = Field(default=True, validation_alias="RAG_RETRIEVE_PARENT_DIVERSITY")
    rag_experiment_tag: str = Field(default="baseline", validation_alias="RAG_EXPERIMENT_TAG")
    rag_hybrid_retrieval: bool = Field(default=True, validation_alias="RAG_HYBRID_RETRIEVAL")
    rag_citations_child_canonical: bool = Field(
        default=True, validation_alias="RAG_CITATIONS_CHILD_CANONICAL"
    )
    rag_citations_parent_legacy: bool = Field(
        default=False, validation_alias="RAG_CITATIONS_PARENT_LEGACY"
    )
    enable_telemetry: bool = Field(default=True, validation_alias="ENABLE_TELEMETRY")
    telemetry_store_query_text: bool = Field(
        default=True, validation_alias="TELEMETRY_STORE_QUERY_TEXT"
    )
    rag_version: str = Field(default="dev", validation_alias="RAG_VERSION")
    rag_followups_enabled: bool = Field(default=True, validation_alias="RAG_FOLLOWUPS_ENABLED")
    rag_rerank_cap_bm25: int = Field(default=140, validation_alias="RAG_RERANK_CAP_BM25")
    rag_rerank_cap_vec: int = Field(default=180, validation_alias="RAG_RERANK_CAP_VEC")
    rag_query_search_k_fast: int = Field(default=220, validation_alias="RAG_QUERY_SEARCH_K_FAST")
    rag_query_search_k_deep: int = Field(default=320, validation_alias="RAG_QUERY_SEARCH_K_DEEP")
    rag_query_bm25_limit: int = Field(default=260, validation_alias="RAG_QUERY_BM25_LIMIT")
    rag_parallel_retrieval: bool = Field(default=True, validation_alias="RAG_PARALLEL_RETRIEVAL")
    rag_parent_min_words: int = Field(default=300, validation_alias="RAG_PARENT_MIN_WORDS")
    rag_parent_max_words: int = Field(default=600, validation_alias="RAG_PARENT_MAX_WORDS")
    rag_parent_tail_min_words: int = Field(default=150, validation_alias="RAG_PARENT_TAIL_MIN_WORDS")
    rag_child_size_words: int = Field(default=150, validation_alias="RAG_CHILD_SIZE_WORDS")
    rag_child_overlap_words: int = Field(default=30, validation_alias="RAG_CHILD_OVERLAP_WORDS")
    rag_child_tail_min_words: int = Field(default=50, validation_alias="RAG_CHILD_TAIL_MIN_WORDS")
    rag_child_max_words: int = Field(default=240, validation_alias="RAG_CHILD_MAX_WORDS")
    rag_child_snap_window: int = Field(default=30, validation_alias="RAG_CHILD_SNAP_WINDOW")
    rag_sufficiency_t_high: float = Field(default=0.6, validation_alias="RAG_SUFFICIENCY_T_HIGH")
    rag_sufficiency_t_mid: float = Field(default=0.35, validation_alias="RAG_SUFFICIENCY_T_MID")
    rag_sufficiency_t_low: float = Field(default=0.15, validation_alias="RAG_SUFFICIENCY_T_LOW")
    rag_sufficiency_o_mid: float = Field(default=0.08, validation_alias="RAG_SUFFICIENCY_O_MID")
    rag_sufficiency_o_low: float = Field(default=0.03, validation_alias="RAG_SUFFICIENCY_O_LOW")
    rag_sufficiency_t_high_clough: float = Field(default=0.6, validation_alias="RAG_SUFFICIENCY_T_HIGH_CLOUGH")
    rag_sufficiency_t_mid_clough: float = Field(default=0.35, validation_alias="RAG_SUFFICIENCY_T_MID_CLOUGH")
    rag_sufficiency_t_low_clough: float = Field(default=0.15, validation_alias="RAG_SUFFICIENCY_T_LOW_CLOUGH")
    rag_sufficiency_o_mid_clough: float = Field(default=0.08, validation_alias="RAG_SUFFICIENCY_O_MID_CLOUGH")
    rag_sufficiency_o_low_clough: float = Field(default=0.03, validation_alias="RAG_SUFFICIENCY_O_LOW_CLOUGH")
    rag_sufficiency_t_high_dean: float = Field(default=0.62, validation_alias="RAG_SUFFICIENCY_T_HIGH_DEAN")
    rag_sufficiency_t_mid_dean: float = Field(default=0.38, validation_alias="RAG_SUFFICIENCY_T_MID_DEAN")
    rag_sufficiency_t_low_dean: float = Field(default=0.18, validation_alias="RAG_SUFFICIENCY_T_LOW_DEAN")
    rag_sufficiency_o_mid_dean: float = Field(default=0.1, validation_alias="RAG_SUFFICIENCY_O_MID_DEAN")
    rag_sufficiency_o_low_dean: float = Field(default=0.04, validation_alias="RAG_SUFFICIENCY_O_LOW_DEAN")
    rag_parent_diversity_min_unique: int = Field(default=6, validation_alias="RAG_PARENT_DIVERSITY_MIN_UNIQUE")
    rag_parent_diversity_min_topk: int = Field(default=4, validation_alias="RAG_PARENT_DIVERSITY_MIN_TOPK")
    rag_parent_diversity_max_per_parent: int = Field(default=3, validation_alias="RAG_PARENT_DIVERSITY_MAX_PER_PARENT")
    rag_threadpool_workers: int = Field(default=4, validation_alias="RAG_THREADPOOL_WORKERS")
    rag_rerank_batch_size: int = Field(default=256, validation_alias="RAG_RERANK_BATCH_SIZE")
    rag_query_embed_cache_size: int = Field(default=20000, validation_alias="RAG_QUERY_EMBED_CACHE_SIZE")
    rag_dynamic_rerank_enabled: bool = Field(default=False, validation_alias="RAG_DYNAMIC_RERANK_ENABLED")
    rag_dynamic_rerank_overlap_high: float = Field(default=0.5, validation_alias="RAG_DYNAMIC_RERANK_OVERLAP_HIGH")
    rag_dynamic_rerank_overlap_low: float = Field(default=0.1, validation_alias="RAG_DYNAMIC_RERANK_OVERLAP_LOW")
    rag_dynamic_rerank_reduce_factor: float = Field(default=0.5, validation_alias="RAG_DYNAMIC_RERANK_REDUCE_FACTOR")
    rag_dynamic_rerank_boost_factor: float = Field(default=1.5, validation_alias="RAG_DYNAMIC_RERANK_BOOST_FACTOR")
    rag_rerank_cache_size: int = Field(default=200000, validation_alias="RAG_RERANK_CACHE_SIZE")
    rag_generation_temperature: float = Field(default=0.1, validation_alias="RAG_GENERATION_TEMPERATURE")
    # Output-token cap per answer (reasoning + content). Bounds a runaway reasoning
    # loop, observed once with gpt-oss filling the whole 32k context (2m50s, no answer).
    rag_generation_max_tokens: int = Field(default=8192, validation_alias="RAG_GENERATION_MAX_TOKENS")
    rag_hierarchy_boost_enabled: bool = Field(default=True, validation_alias="RAG_HIERARCHY_BOOST_ENABLED")
    rag_hierarchy_boost_weight: float = Field(default=0.12, validation_alias="RAG_HIERARCHY_BOOST_WEIGHT")
    rag_hierarchy_min_coverage: float = Field(default=0.25, validation_alias="RAG_HIERARCHY_MIN_COVERAGE")
    rag_hierarchy_anchor_top_n: int = Field(default=3, validation_alias="RAG_HIERARCHY_ANCHOR_TOP_N")
    rag_hierarchy_use_section_path: bool = Field(default=True, validation_alias="RAG_HIERARCHY_USE_SECTION_PATH")
    rag_citation_guardrail_enabled: bool = Field(default=True, validation_alias="RAG_CITATION_GUARDRAIL_ENABLED")
    rag_citation_guardrail_min_sentence_words: int = Field(
        default=6, validation_alias="RAG_CITATION_GUARDRAIL_MIN_SENTENCE_WORDS"
    )
    rag_citation_guardrail_min_coverage: float = Field(
        default=0.70, validation_alias="RAG_CITATION_GUARDRAIL_MIN_COVERAGE"
    )
    rag_citation_guardrail_log_only: bool = Field(default=True, validation_alias="RAG_CITATION_GUARDRAIL_LOG_ONLY")
    # --- Retrieval-quality improvements (renovation; all flag-gated, default = prior behavior) ---
    rag_rrf_fusion_enabled: bool = Field(default=False, validation_alias="RAG_RRF_FUSION_ENABLED")
    rag_stable_tiebreak: bool = Field(default=False, validation_alias="RAG_STABLE_TIEBREAK")
    rag_parent_diversity_stable: bool = Field(default=False, validation_alias="RAG_PARENT_DIVERSITY_STABLE")
    rag_bm25_or_fallback_enabled: bool = Field(default=False, validation_alias="RAG_BM25_OR_FALLBACK_ENABLED")
    rag_bm25_or_fallback_min_hits: int = Field(default=5, validation_alias="RAG_BM25_OR_FALLBACK_MIN_HITS")
    rag_child_dedup_enabled: bool = Field(default=False, validation_alias="RAG_CHILD_DEDUP_ENABLED")
    rag_child_dedup_jaccard_threshold: float = Field(
        default=0.92, validation_alias="RAG_CHILD_DEDUP_JACCARD_THRESHOLD"
    )
    rag_hierarchy_boost_corpus_scope: str = Field(default="", validation_alias="RAG_HIERARCHY_BOOST_CORPUS_SCOPE")
    rag_default_retrieval_mode: str = Field(default="fast", validation_alias="RAG_DEFAULT_RETRIEVAL_MODE")
    rag_default_retrieval_mode_clough: str = Field(
        default="fast", validation_alias="RAG_DEFAULT_RETRIEVAL_MODE_CLOUGH"
    )
    rag_default_retrieval_mode_dean: str = Field(
        default="deep", validation_alias="RAG_DEFAULT_RETRIEVAL_MODE_DEAN"
    )
    qdrant_ef_search_fast: int = Field(default=128, validation_alias="QDRANT_EF_SEARCH_FAST")
    qdrant_ef_search_deep: int = Field(default=256, validation_alias="QDRANT_EF_SEARCH_DEEP")
    rag_generation_max_concurrency: int = Field(default=2, validation_alias="RAG_GENERATION_MAX_CONCURRENCY")
    rag_query_rewrite_enabled: bool = Field(default=True, validation_alias="RAG_QUERY_REWRITE_ENABLED")
    rag_small_talk_bypass_enabled: bool = Field(
        default=True,
        validation_alias="RAG_SMALL_TALK_BYPASS_ENABLED",
    )
    rag_no_cite_retry_enabled: bool = Field(
        default=False,
        validation_alias="RAG_NO_CITE_RETRY_ENABLED",
    )
    rag_no_cite_retry_max: int = Field(
        default=1,
        validation_alias="RAG_NO_CITE_RETRY_MAX",
    )
    rag_no_cite_substantive_words: int = Field(
        default=35,
        validation_alias="RAG_NO_CITE_SUBSTANTIVE_WORDS",
    )
    rag_citation_automap_enabled: bool = Field(
        default=False,
        validation_alias="RAG_CITATION_AUTOMAP_ENABLED",
    )
    rag_citation_automap_min_sent_words: int = Field(
        default=6,
        validation_alias="RAG_CITATION_AUTOMAP_MIN_SENT_WORDS",
    )
    rag_citation_automap_min_score: float = Field(
        default=0.22,
        validation_alias="RAG_CITATION_AUTOMAP_MIN_SCORE",
    )
    rag_citation_automap_min_coverage: float = Field(
        default=0.70,
        validation_alias="RAG_CITATION_AUTOMAP_MIN_COVERAGE",
    )
    rag_citation_automap_max_cites_per_sentence: int = Field(
        default=2,
        validation_alias="RAG_CITATION_AUTOMAP_MAX_CITES_PER_SENTENCE",
    )
    api_allowed_origins: str = Field(default="http://localhost:5173", validation_alias="API_ALLOWED_ORIGINS")
    # Models clients may request (comma-separated). Empty = OLLAMA_MODEL plus
    # SECONDARY_OLLAMA_MODEL. Other installed Ollama models are never offered or used,
    # so a public visitor cannot load an unrelated model onto the shared GPU.
    api_allowed_models: str = Field(default="", validation_alias="API_ALLOWED_MODELS")
    # Built frontend (npm run build) served by the API at "/". Relative paths resolve
    # against the repository root; empty disables frontend serving.
    frontend_dist_path: str = Field(default="frontend/dist", validation_alias="FRONTEND_DIST_PATH")

    class Config:
        env_file = ".env"
        extra = "ignore"

    def validation_profile_thresholds(self) -> dict[str, dict[str, float | int]]:
        """Return per-profile validation thresholds with optional env overrides."""
        table: dict[str, dict[str, float | int]] = {
            "dbm_slides_pdf": {
                "min_words": self.dean_pdf_min_words_slides,
                "min_blocks": 5,
                "dup_warn": 0.12,
                "leak_warn": 0.2,
            },
            "legacy_html_export_pdf": {
                "min_words": self.dean_pdf_min_words_legacy,
                "min_blocks": 8,
                "dup_warn": 0.08,
                "leak_warn": 0.15,
            },
            "chafer_lesson_pdf": {
                "min_words": self.dean_pdf_min_words_chafer,
                "min_blocks": 8,
                "dup_warn": 0.08,
                "leak_warn": 0.1,
            },
            "article_pdf": {
                "min_words": self.dean_pdf_min_words_article,
                "min_blocks": 6,
                "dup_warn": 0.08,
                "leak_warn": 0.1,
            },
            "doctrinal_outline_doc": {
                "min_words": self.dean_doc_min_words_outline,
                "min_blocks": 6,
                "dup_warn": 0.1,
                "leak_warn": 0.12,
            },
            "dean_bible_html_v2": {
                "min_words": self.dean_bible_html_min_words,
                "min_blocks": self.dean_bible_html_min_blocks,
                "dup_warn": 0.2,
                "leak_warn": 0.15,
            },
            "dean_topical_html_v2": {
                "min_words": self.dean_topical_html_min_words,
                "min_blocks": self.dean_topical_html_min_blocks,
                "dup_warn": 0.14,
                "leak_warn": 0.12,
            },
            "lecture_transcript_doc": {
                "min_words": self.dean_doc_min_words_lecture,
                "min_blocks": 8,
                "dup_warn": 0.08,
                "leak_warn": 0.12,
            },
            "clough_course_notes_v2": {
                "min_words": self.clough_course_notes_validation_min_words,
                "min_blocks": self.clough_course_notes_validation_min_blocks,
                "dup_warn": self.clough_course_notes_validation_dup_warn,
                "leak_warn": self.clough_course_notes_validation_leak_warn,
            },
            "clough_transcripts_pdf_v2": {
                "min_words": self.transcript_validation_min_words,
                "min_blocks": self.transcript_validation_min_blocks,
                "dup_warn": self.transcript_dup_warn,
                "leak_warn": self.transcript_leak_warn,
            },
            "clough_transcripts_doc_v3": {
                "min_words": 220,
                "min_blocks": 5,
                "dup_warn": 0.16,
                "leak_warn": 0.14,
            },
            "default": {
                "min_words": max(self.dean_pdf_min_words_default, self.dean_doc_min_words_default),
                "min_blocks": 10,
                "dup_warn": 0.06,
                "leak_warn": 0.1,
            },
        }

        try:
            parsed: Any = json.loads(self.validation_profile_threshold_overrides or "{}")
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            return table

        for profile_name, profile_values in parsed.items():
            if not isinstance(profile_name, str) or not isinstance(profile_values, dict):
                continue
            merged = dict(table.get(profile_name, {}))
            for key in ("min_words", "min_blocks", "dup_warn", "leak_warn"):
                if key not in profile_values:
                    continue
                raw = profile_values[key]
                try:
                    merged[key] = int(raw) if key in {"min_words", "min_blocks"} else float(raw)
                except Exception:
                    continue
            if set(merged.keys()) == {"min_words", "min_blocks", "dup_warn", "leak_warn"}:
                table[profile_name] = merged
        return table

    @property
    def database(self) -> DatabaseConfig:
        """Grouped database settings."""
        return DatabaseConfig(
            url=self.database_url,
            pool_size=self.db_pool_size,
            max_overflow=self.db_max_overflow,
            pool_timeout=self.db_pool_timeout,
            pool_recycle=self.db_pool_recycle,
            pool_pre_ping=self.db_pool_pre_ping,
            statement_timeout_ms=self.db_statement_timeout_ms,
        )

    @property
    def vector_store(self) -> VectorStoreConfig:
        """Grouped vector store settings."""
        return VectorStoreConfig(
            host=self.qdrant_host,
            port=self.qdrant_port,
            expected_dim=self.qdrant_expected_dim,
            ef_search_fast=self.qdrant_ef_search_fast,
            ef_search_deep=self.qdrant_ef_search_deep,
            default_retrieval_mode=self.rag_default_retrieval_mode,
            default_retrieval_mode_by_corpus={
                "clough": self.rag_default_retrieval_mode_clough,
                "dean": self.rag_default_retrieval_mode_dean,
            },
        )

    @property
    def sufficiency(self) -> SufficiencyConfig:
        """Grouped sufficiency thresholds with corpus overrides."""
        return SufficiencyConfig(
            default=SufficiencyThresholds(
                t_high=self.rag_sufficiency_t_high,
                t_mid=self.rag_sufficiency_t_mid,
                t_low=self.rag_sufficiency_t_low,
                o_mid=self.rag_sufficiency_o_mid,
                o_low=self.rag_sufficiency_o_low,
            ),
            by_corpus={
                "clough": SufficiencyThresholds(
                    t_high=self.rag_sufficiency_t_high_clough,
                    t_mid=self.rag_sufficiency_t_mid_clough,
                    t_low=self.rag_sufficiency_t_low_clough,
                    o_mid=self.rag_sufficiency_o_mid_clough,
                    o_low=self.rag_sufficiency_o_low_clough,
                ),
                "dean": SufficiencyThresholds(
                    t_high=self.rag_sufficiency_t_high_dean,
                    t_mid=self.rag_sufficiency_t_mid_dean,
                    t_low=self.rag_sufficiency_t_low_dean,
                    o_mid=self.rag_sufficiency_o_mid_dean,
                    o_low=self.rag_sufficiency_o_low_dean,
                ),
            },
        )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Load configuration with defaults overridden by .env."""
    return AppConfig()


def reload_config() -> AppConfig:
    """Clear cached config and reload from environment."""
    get_config.cache_clear()
    return get_config()
