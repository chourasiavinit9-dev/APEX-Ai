"""
core/constants.py — ALL magic values live here, nowhere else.
Import from here; never hardcode values in other modules.
"""

# ── Model identifiers ─────────────────────────────────────────────────────────
EXTRACTION_MODEL = "claude-sonnet-4-8"
CLASSIFICATION_MODEL = "claude-haiku-4-5"
VALIDATION_MODEL = "claude-haiku-4-5"

# ── Pipeline thresholds ───────────────────────────────────────────────────────
CONFIDENCE_REVIEW_THRESHOLD = 0.70  # below this → human review queue
CONFIDENCE_WEB_SEARCH_THRESHOLD = 0.50  # below this → agent tries web search
CONFIDENCE_INFER_PENALTY = 0.10  # per inferred field
DEDUP_HARD_THRESHOLD = 0.95  # >= this → definite duplicate, skip
DEDUP_SOFT_THRESHOLD = 0.85  # >= this and < hard → possible_duplicate flag
MAX_AGENT_ITERATIONS = 5  # max tool-use loop turns
K_RAG_NEIGHBORS = 5  # nearest neighbors to fetch
MAX_EVIDENCE_CHARS = 80  # max length of evidence quote
MAX_DOCUMENT_CHARS = 8000  # context window budget per document
MAX_PDF_PAGES = 4  # max pages rendered for VLM
IMAGE_DPI = 150  # PDF → image render DPI

# ── Product types ─────────────────────────────────────────────────────────────
PRODUCT_TYPES = ["bearing", "valve", "sensor", "coupling", "fastener", "pump"]

# ── Field source tags ─────────────────────────────────────────────────────────
SOURCE_EXTRACTED = "extracted"
SOURCE_INFERRED = "inferred"
SOURCE_WEB_ENRICHED = "web_enriched"
SOURCE_HUMAN_CORRECTED = "human_corrected"
SOURCE_RULE_DEFAULT = "rule_default"
SOURCE_MERGED_DUPLICATE = "merged_duplicate"  # field filled from a deduplicated secondary record

# ── Knowledge graph edge types ────────────────────────────────────────────────
EDGE_COMPATIBLE = "compatible_with"
EDGE_REPLACES = "replaces"
EDGE_SAME_AS = "same_as"  # manufacturer aliases
EDGE_STANDARD = "meets_standard"

# ── Storage paths ─────────────────────────────────────────────────────────────
CHROMA_DB_PATH = "data/product_catalog_db"
CHROMA_COLLECTION = "products"
KNOWLEDGE_GRAPH_PATH = "data/knowledge_graph.json"

# ── Export formats ────────────────────────────────────────────────────────────
SCHEMA_ORG_CONTEXT = "https://schema.org"
JSONLD_PRODUCT_TYPE = "Product"

# ── Supported file extensions ─────────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".txt",
    ".html",
    ".htm",
    ".csv",
    ".xlsx",
    ".md",
}

# ── Security (imported by security/middleware.py) ─────────────────────────────
JWT_SECRET_ENV = "APEX_JWT_SECRET"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = 60
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_MINUTES = 15
RATE_LIMIT_LOCKOUT_MINUTES = 15
HONEYPOT_FIELD_NAME = "website"
MAX_INPUT_STRING_LENGTH = 1000
MAX_EMAIL_LENGTH = 254
PASSWORD_MIN_LENGTH = 12
