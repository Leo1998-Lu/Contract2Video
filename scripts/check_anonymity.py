#!/usr/bin/env python3
"""Screen a review tree for common identifying or secret-bearing strings.

This check is intentionally conservative and is only a screening aid. It skips
version-control metadata and generated media, then reports file and line
locations for manual review.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache", "build", "dist"}
SKIP_SUFFIXES = {".mp4", ".webm", ".mov", ".wav", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".pt", ".ckpt", ".safetensors"}

# Keep matching patterns generic so this file does not encode any contributor
# or institution names. Reviewers can pass additional terms on the command line.
PATTERNS = {
    "private_path": re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\Users\\|/workspace/|/mnt/data/|/root/)[^\s'\"`]+", re.IGNORECASE),
    "credential_assignment": re.compile(r"\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)\s*[:=]\s*['\"]?[^\s,'\"}]+", re.IGNORECASE),
    "private_url": re.compile(r"https?://(?:drive\.google\.com|docs\.google\.com|[^/\s]+\.internal(?:[:/]|$)|localhost(?:[:/]|$))", re.IGNORECASE),
    "identity_metadata": re.compile(r"\b(?:author|authors|institution|university|affiliation|acknowledg(?:e?ment|ements))\s*[:=]", re.IGNORECASE),
}


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        yield path, text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screen files for common anonymity hazards")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--term", action="append", default=[], help="Additional case-insensitive term to flag")
    args = parser.parse_args(argv)

    patterns = dict(PATTERNS)
    for i, term in enumerate(args.term):
        patterns[f"custom:{i}"] = re.compile(re.escape(term), re.IGNORECASE)

    findings: list[tuple[Path, int, str, str]] = []
    for path, text in iter_text_files(args.root):
        for line_no, line in enumerate(text.splitlines(), start=1):
            for label, pattern in patterns.items():
                match = pattern.search(line)
                if match:
                    preview = match.group(0)
                    if len(preview) > 80:
                        preview = preview[:77] + "..."
                    findings.append((path.relative_to(args.root), line_no, label, preview))

    if findings:
        print("Potential anonymity findings (manual review required):", file=sys.stderr)
        for path, line_no, label, preview in findings:
            print(f"- {path}:{line_no}: {label}: {preview}", file=sys.stderr)
        return 1

    print("No configured anonymity patterns found in text files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
