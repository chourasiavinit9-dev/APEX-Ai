# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| Latest (main) | ✅ |
| develop branch | ⚠️ Best-effort |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Email: security@your-org.com (or open a private GitHub Security Advisory)

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will respond within 48 hours and aim to patch within 7 days.

## Security Architecture

**Controls implemented:**
- JWT HS256 auth
- bcrypt password hashing
- Rate limiting (5 attempts / 15 min per IP and email)
- CSRF HMAC tokens
- XSS input sanitization
- Honeypot bot detection
- Security headers (CSP, HSTS, X-Frame-Options) — set in `run_ui.py` response headers
- Session revocation on explicit logout
- Audit log (immutable — every review action written to `reviews` SQLite table)

> **Note**: PostgreSQL Row-Level Security is documented as a production upgrade path; the current SQLite implementation enforces access via application-layer auth, not DB-level RLS.

### Authentication
- **JWT HS256** tokens with 60-minute expiry
- **PBKDF2-SHA256** password hashing (260,000 iterations) — falls back from bcrypt gracefully
- **Auto-generated secrets** when `APEX_JWT_SECRET` env var is absent (ephemeral, secure for dev)
- Session invalidation on explicit logout

### Input Validation
- **XSS sanitization** on every user-supplied string (HTML-escape + length limit)
- **CSRF protection** via HMAC-signed tokens per session
- **Bot detection** via honeypot field + User-Agent pattern matching
- **SQL injection prevention** via parameterized queries only (no string interpolation in SQL)
- **Input length limits** enforced at both UI (max_chars) and API level

### Rate Limiting
- **5 login attempts** per email + IP per 15-minute window
- **Lockout** with countdown displayed to user
- In-memory rate store (reset on server restart — upgrade to Redis for production)

### Data Security
- **Manufacturer-only sourcing**: Amazon, eBay, Grainger, and all distributors are blocked
- **Hallucination risk control**: Hallucination risk is controlled — LOV validation gate rejects out-of-vocabulary values; evidence quotes required for every LLM extraction; human review triggered for confidence below 0.80
- **LOV-constrained prompts**: Attribute extraction is bounded by approved vocabulary
- **Audit trail**: Every review action is written immutably to `reviews` SQLite table
- **ChromaDB indexing**: Only approved records are indexed; rejected records are excluded

### Known Limitations (by design, for hackathon scope)
- Rate limit store is in-memory (not Redis) — resets on restart
- JWT secret auto-generation is transient — set `APEX_JWT_SECRET` in production
- No email verification on login (demo users only)
- SQLite suitable for ≤ 100K rows; migrate to PostgreSQL for production scale

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `APEX_JWT_SECRET` | Recommended | Min 32-char secret for JWT signing. Auto-generated if absent. |
| `ANTHROPIC_API_KEY` | Optional | Claude API key. Heuristic fallback works without it. |

**Never commit API keys to the repository.** The CI pipeline checks for hardcoded secrets.
