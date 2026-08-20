# Contributing to LEAP

Thank you for your interest in contributing! LEAP is a UniHack 2026 submission but welcomes improvements.

## Development Setup

```bash
git clone https://github.com/chourasiavinit9-dev/APEX-Ai.git
cd APEX-Ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install flake8 black isort bandit pytest-cov
```

## Branch Strategy

| Branch | Purpose |
|---|---|
| `main` | Stable, production-ready code |
| `develop` | Integration branch |
| `feat/your-feature` | Feature development |
| `fix/issue-description` | Bug fixes |

## Code Standards

- **Python version**: 3.9+ (always use `from __future__ import annotations`)
- **Line length**: 120 characters max
- **Formatter**: `black --line-length=120`
- **Linter**: `flake8 --max-line-length=120`
- **Imports**: `isort --profile black`

Before committing:
```bash
black --line-length=120 .
isort --profile black .
flake8 . --max-line-length=120
python -m pytest tests/ -v
```

## Testing

All contributions must keep **194/194 tests passing**. New features must include tests.

```bash
python -m pytest tests/ -v --tb=short
python3 evaluate.py --demo
```

## Source / Evidence Rules

If you add any data enrichment logic:

- ✅ Only use manufacturer URLs as sources
- ❌ Never use Amazon, eBay, Grainger, or distributor URLs
- ✅ If evidence is absent, leave the field blank — do not invent values
- ✅ Every populated field must carry a `FieldProvenance` record

## Pull Request Checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Code formatted (`black`, `isort`)
- [ ] No hardcoded API keys
- [ ] `from __future__ import annotations` at top of every new file
- [ ] New functions have docstrings
- [ ] Evidence/sourcing rules respected

## Reporting Issues

Open a GitHub Issue with:
- Python version
- Steps to reproduce
- Expected vs actual behaviour
- Any relevant error output
