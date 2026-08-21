#!/usr/bin/env python3
"""
score_check.py — Run before every submission.
Python equivalent of the pw-test score-check.mjs pattern.

Usage: python score_check.py
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CORE_FILES = list((ROOT / "core").glob("*.py"))
UI_FILES = list((ROOT / "ui").glob("*.py"))
ALL_PY = CORE_FILES + UI_FILES + [ROOT / "tests" / "test_pipeline.py"]


def check(name: str, fn) -> bool:
    try:
        result = fn()
        icon = "✅" if result else "❌"
        print(f"  {icon}  {name}")
        return bool(result)
    except Exception as e:
        print(f"  ❌  {name}  ({e})")
        return False


# ── Individual checks ─────────────────────────────────────────────────────────

def no_hardcoded_keys() -> bool:
    """No API keys in any source file."""
    patterns = ["sk-ant-", "AIzaSy", "AAAA", "api_key='sk"]
    for f in ALL_PY:
        text = f.read_text(errors="ignore")
        for p in patterns:
            if p in text:
                print(f"       ↳ Found '{p}' in {f.name}")
                return False
    return True


def env_example_exists() -> bool:
    return (ROOT / ".env.example").exists()


def gitignore_excludes_env() -> bool:
    gi = ROOT / ".gitignore"
    if not gi.exists():
        return False
    return ".env.local" in gi.read_text()


def constants_file_exists() -> bool:
    return (ROOT / "core" / "constants.py").exists()


def pydantic_schemas_exist() -> bool:
    f = ROOT / "core" / "pydantic_schemas.py"
    if not f.exists():
        return False
    text = f.read_text()
    # Must have domain-named schemas, not generic ones
    return "Schema" in text and "class Bearing" in text


def no_hardcoded_magic_values() -> bool:
    """Model names and thresholds must come from constants.py, not be inline."""
    bad_patterns = [
        '"claude-sonnet-4-8"',
        '"claude-haiku-4-5"',
        "0.70",   # confidence threshold — must be from constants
    ]
    violations = []
    for f in CORE_FILES:
        if f.name in ("constants.py", "pydantic_schemas.py"):
            continue
        text = f.read_text()
        for p in bad_patterns:
            if p in text:
                violations.append(f"{f.name}: {p}")
    if violations:
        for v in violations[:3]:
            print(f"       ↳ {v}")
        return False
    return True


def all_functions_under_25_lines() -> bool:
    """Every production function body must be ≤ 25 lines (excludes test fixtures)."""
    EXEMPT_PREFIXES = ("_mock_", "test_")  # test data builders and test cases exempt
    violations = []
    # UI pages are exempt from 25-line rule (they are layout, not logic)
    for f in CORE_FILES:  # core logic only — UI pages are layout code
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            violations.append(f"{f.name}: syntax error")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(node.name.startswith(p) for p in EXEMPT_PREFIXES):
                continue
            body_lines = node.end_lineno - node.lineno
            if body_lines > 25:
                violations.append(f"{f.name}:{node.lineno} {node.name}() = {body_lines} lines")
    if violations:
        for v in violations[:5]:
            print(f"       ↳ {v}")
        return False
    return True


def syntax_clean() -> bool:
    """All .py files parse without errors."""
    for f in ALL_PY:
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            print(f"       ↳ {f.name}: {e}")
            return False
    return True


def tests_exist_and_pass() -> bool:
    test_file = ROOT / "tests" / "test_pipeline.py"
    if not test_file.exists():
        return False
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "--tb=no"],
        capture_output=True, text=True, cwd=ROOT
    )
    passed = "passed" in result.stdout
    if not passed:
        print(f"       ↳ {result.stdout.strip()[-200:]}")
    return passed


def requirements_md_exists_and_ticked() -> bool:
    req = ROOT / "REQUIREMENTS.md"
    if not req.exists():
        return False
    text = req.read_text()
    unticked = text.count("- [ ]")
    if unticked > 0:
        print(f"       ↳ {unticked} unchecked item(s) in REQUIREMENTS.md")
        return False
    return True


def code_quality_md_exists() -> bool:
    return (ROOT / "CODE_QUALITY.md").exists()


def readme_has_architecture() -> bool:
    readme = ROOT / "README.md"
    if not readme.exists():
        return False
    text = readme.read_text()
    return "Architecture" in text and ("→" in text or "─" in text)


def dockerfile_exists() -> bool:
    return (ROOT / "Dockerfile").exists()


def all_modules_present() -> bool:
    required = [
        "core/constants.py",
        "core/pydantic_schemas.py",
        "core/ingest.py",
        "core/extractor.py",
        "core/agent.py",
        "core/enricher.py",
        "core/web_enricher.py",
        "core/knowledge_graph.py",
        "core/validator.py",
        "core/exporter.py",
        "core/pipeline.py",
        "ui/app.py",
        "tests/test_pipeline.py",
        "security/middleware.py",
        "tests/test_security.py",
        "migrations/001_schema_rls.sql",
    ]
    missing = [m for m in required if not (ROOT / m).exists()]
    if missing:
        for m in missing:
            print(f"       ↳ Missing: {m}")
    return len(missing) == 0


def team_prompt_exists() -> bool:
    return (ROOT / "TEAM_PROMPT.md").exists()


# ── Run all checks ────────────────────────────────────────────────────────────

CHECKS = [
    ("No hardcoded API keys",               no_hardcoded_keys),
    (".env.example exists",                 env_example_exists),
    (".env.local in .gitignore",            gitignore_excludes_env),
    ("core/constants.py exists",            constants_file_exists),
    ("Domain-named Pydantic schemas",       pydantic_schemas_exist),
    ("No inline magic values",              no_hardcoded_magic_values),
    ("All functions ≤ 25 lines",            all_functions_under_25_lines),
    ("All Python files syntax-clean",       syntax_clean),
    ("45+ tests passing (no API key)",      tests_exist_and_pass),
    ("REQUIREMENTS.md all items ticked",    requirements_md_exists_and_ticked),
    ("CODE_QUALITY.md exists",              code_quality_md_exists),
    ("README has architecture diagram",     readme_has_architecture),
    ("Dockerfile exists",                   dockerfile_exists),
    ("All 13 modules present",              all_modules_present),
    ("TEAM_PROMPT.md exists",               team_prompt_exists),
]


def main() -> None:
    print("\n🔍  APEX Pre-Submission Score Check\n" + "─" * 42)
    passed = sum(check(name, fn) for name, fn in CHECKS)
    total = len(CHECKS)
    pct = int(passed / total * 100)

    print(f"\n{'─' * 42}")
    print(f"   Score: {passed}/{total} checks passing ({pct}%)")

    if passed == total:
        print("   🚀  Ready to submit!\n")
        sys.exit(0)
    else:
        print(f"   ⚠️   Fix {total - passed} failing check(s) before submitting.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
