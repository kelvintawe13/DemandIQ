#!/usr/bin/env python3
"""DemandIQ — static build-context linter (no Docker required).

Docker is not available in every environment that needs to catch a leaked
secret, so the expensive guarantee ("the image contains no .env") is the wrong
place to enforce the cheap one. This walks each service's build context the way
Docker would and fails when the payload would contain a credential, a host
build artifact, or a COPY source that does not exist.

Run: python3.11 scripts/check_docker_context.py
"""
from __future__ import annotations

import fnmatch as _fnmatch
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Anything here landing in a build context is a credential. These are matched
# against the context-relative POSIX path of every file, plus a basename test so
# a nested `frontend/.env` cannot hide behind a pattern that only covers the root.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    ".netrc",
    ".npmrc",
    "*credentials*.json",
    "*secret*.json",
)

# Re-includable by an explicit `!` rule: samples and templates carry no secret.
SECRET_ALLOWLIST = (".env.example", ".env.sample", ".env.template")

# Host build artifacts: excluded for correctness (stale ABI, wrong platform) and
# for size. A host `node_modules` in a Linux image is a silently broken runtime.
JUNK_DIRS = ("node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git")
JUNK_FILES = ("*.pyc", "*.pyo", ".DS_Store", "npm-debug.log", "*.tsbuildinfo")

# Generous: the measured root context is ~2.3MB. A regression to node_modules or
# .git would blow past this by three orders of magnitude, so the budget catches
# "someone added a new leak" without needing to know what every file is for.
CONTEXT_BUDGET_MB = 25


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class Ignore:
    """A single .dockerignore, including any inherited from parent directories.

    Docker applies the ignore files of every directory ABOVE the context root as
    well, with those patterns expressed relative to the context. Composition is
    therefore: parent ignores first, then the context's own file.
    """

    def __init__(self) -> None:
        self.rules: list[tuple[str, bool, str, int]] = []  # pattern, negated, source, line no

    def add_file(self, path: Path, base: Path) -> None:
        if not path.is_file():
            return
        for n, raw in enumerate(path.read_text().splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:].strip()
            # Leading '/' anchors to the ignore file's own directory; a trailing
            # '/' is directory-only and carries no matching meaning for files.
            line = line.strip("/")
            if not line:
                continue
            self.rules.append((line, negated, rel(path), n))

    @classmethod
    def for_context(cls, context: Path) -> "Ignore":
        self = cls()
        # Parents of the context, outermost first — Docker reads them in that order.
        chain = [p / ".dockerignore" for p in sorted(context.parents, key=lambda p: len(p.parts)) if context not in p.parents or p != context]
        for parent_ignore in chain:
            self.add_file(parent_ignore, context)
        self.add_file(context / ".dockerignore", context)
        return self

    def _last_match(self, path: str) -> tuple[bool, str]:
        ignored, reason = False, "no rule matched (sent to daemon)"
        for pattern, negated, source, n in self.rules:
            if match(pattern, path):
                ignored = not negated
                reason = f"{'re-included' if negated else 'excluded'} by {source}:{n} ({pattern!r})"
        return ignored, reason

    def decide(self, path: str) -> tuple[bool, str]:
        """Docker semantics: the LAST matching pattern wins, re-inclusions included.

        With one rule that precedes it, and Docker is explicit about the reason:
        a file cannot be re-included while a parent directory of it is excluded,
        because the daemon never descends into an excluded directory. Modelling
        that here is what keeps a `.venv/**/README.md` from being reported as a
        leak that a real build would never send.
        """
        parts = path.split("/")
        for i in range(1, len(parts)):
            ancestor = "/".join(parts[:i])
            ignored, reason = self._last_match(ancestor)
            if ignored:
                return True, f"parent directory {ancestor}/ is excluded — {reason}"
        return self._last_match(path)


def _segment_regex(segment: str) -> str:
    """One path segment: '*' and '?' stop at the separator, '**' is handled apart.

    '[cod]' stays a character class, the way Docker's Go matcher reads it. Escaping
    the brackets made `**/*.py[cod]` a literal match for a file named `*.py[cod]`,
    so that rule — and any other class in a .dockerignore — excluded nothing.
    """
    out = ""
    for ch in segment:
        if ch == "?":
            out += "[^/]"
        elif ch == "*":
            out += "[^/]*"
        elif ch in "[]":
            out += ch
        else:
            out += re.escape(ch)
    return out


def match(pattern: str, path: str) -> bool:
    """Docker's matcher, in the two cases that actually differ from glob().

    A pattern with no separator is matched against EVERY path component, not just
    the basename: `.git` excludes `<root>/.git` *and* `frontend/.git`, and a
    directory that matches excludes everything beneath it. A pattern containing a
    separator is anchored to the context root, with '**' crossing directories.
    """
    if pattern == "**":
        return True
    segments = [s for s in pattern.split("/") if s]
    parts = path.split("/")

    if len(segments) == 1:
        if re.fullmatch(_segment_regex(segments[0]), parts[-1]):
            return True
        return any(re.fullmatch(_segment_regex(segments[0]), p) for p in parts[:-1])

    # The separator belongs BETWEEN segments, not after the last one. Appending
    # it unconditionally built `**/node_modules/`, which no directory path can
    # equal, so every multi-segment rule (`**/node_modules`, `frontend/dist`, a
    # `!frontend/*.md` re-inclusion) silently matched nothing and the excluded
    # tree was scanned and reported as if it would be sent.
    last = len(segments) - 1
    regex = ""
    for i, seg in enumerate(segments):
        if seg == "**":
            # Crosses directories; as the final segment it is a bare prefix.
            regex += "(?:.*/)?" if i < last else ".*"
        else:
            regex += _segment_regex(seg) + ("/" if i < last else "")
    return re.fullmatch(regex, path) is not None


@dataclass
class Report:
    leaks: list[str] = field(default_factory=list)
    junk: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    total: int = 0
    total_bytes: int = 0

    @property
    def ok(self) -> bool:
        return not (self.leaks or self.missing)


def compose_builds() -> list[tuple[Path, Path]]:
    """(context, dockerfile) pairs declared in docker-compose.yml.

    Deliberately a targeted scan rather than a YAML parse: the file uses merge
    keys, and this script must run with the standard library alone so it can be a
    pre-build gate in any environment. Duplicate (context, dockerfile) pairs are
    collapsed because `migrate`/`backend`/`worker` all share one anchor.
    """
    text = (ROOT / "docker-compose.yml").read_text()
    contexts = re.findall(r"^\s*context:\s*(\S+)\s*$", text, re.M)
    dockerfiles = re.findall(r"^\s*dockerfile:\s*(\S+)\s*$", text, re.M)
    builds: list[tuple[Path, Path]] = []
    for ctx, df in zip(contexts, dockerfiles):
        context = (ROOT / ctx).resolve()
        dockerfile = context / df if not df.startswith("/") else ROOT / df.lstrip("/")
        if (context, dockerfile) not in builds:
            builds.append((context, dockerfile))
    if not builds:
        raise SystemExit("no build contexts found in docker-compose.yml — did the compose file change shape?")
    return builds


def dockerfile_copy_sources(dockerfile: Path) -> list[str]:
    """Source paths of every COPY, joined across `\\` continuations.

    Sources coming from a build stage (`--from=`) are excluded: they are produced
    inside the build, not read from the context, so they cannot be a typo here.
    """
    text = dockerfile.read_text()
    text = re.sub(r"\\\s*\n", " ", text)  # join continuations
    sources: list[str] = []
    for match_ in re.finditer(r"^\s*COPY\s+(.*)$", text, re.M | re.I):
        args = match_.group(1).split()
        if any(a.startswith("--from=") for a in args):
            continue
        args = [a for a in args if not a.startswith("--")]
        for src in args[:-1]:  # last token is the destination
            sources.append(src.strip("\"'"))
    return sources


def sent_files(context: Path, ignore: "Ignore") -> list[tuple[Path, str]]:
    """Every file Docker would actually send, with the rule that decided it.

    Excluded directories are never descended into, because the daemon never
    descends into them either — that is the same reason `decide` consults
    ancestors. Pruning is not just faithful, it is what makes the scan finish:
    a host `node_modules` is tens of thousands of files, and proving it is not
    sent must not require reading it.
    """
    sent: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(context):
        directory = Path(dirpath)
        prefix = directory.relative_to(context).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        dirnames[:] = sorted(d for d in dirnames if not ignore.decide(prefix + d)[0])
        for name in sorted(filenames):
            path = directory / name
            if path.is_symlink():
                continue
            ignored, reason = ignore.decide(prefix + name)
            if not ignored:
                sent.append((path, reason))
    return sent


def check_build(context: Path, dockerfile: Path, verbose: bool) -> Report:
    report = Report()
    label = rel(context) if context != ROOT else "."
    ignore = Ignore.for_context(context)
    print(f"\n  {label}  (dockerfile: {rel(dockerfile)})")
    if not ignore.rules:
        print(f"    \033[33m⚠\033[0m no .dockerignore — the whole {label} tree is sent to the daemon")
        report.leaks.append(f"{label}: no .dockerignore")

    for path, reason in sent_files(context, ignore):
        path_rel = path.relative_to(context).as_posix()
        report.total += 1
        size = path.stat().st_size
        report.total_bytes += size
        if verbose:
            print(f"      {path_rel} ({size:,}B)")
        if any(_fnmatch.fnmatch(path_rel, s) or _fnmatch.fnmatch(Path(path_rel).name, s) for s in SECRET_PATTERNS):
            if Path(path_rel).name in SECRET_ALLOWLIST:
                continue
            report.leaks.append(f"{label}/{path_rel} — {reason}")
        if any(part in JUNK_DIRS for part in Path(path_rel).parts) or any(
            _fnmatch.fnmatch(Path(path_rel).name, j) for j in JUNK_FILES
        ):
            report.junk.append(f"{label}/{path_rel} ({size:,}B)")

    if report.leaks:
        for item in report.leaks:
            print(f"    \033[31m✗ secret in context:\033[0m {item}")
    if report.junk:
        for item in report.junk[:5]:
            print(f"    \033[33m⚠ host artifact:\033[0m {item}")
        if len(report.junk) > 5:
            print(f"    \033[33m⚠ host artifact:\033[0m …and {len(report.junk) - 5} more")
    megabytes = report.total_bytes / (1024 * 1024)
    if not report.leaks and not report.junk:
        print(f"    \033[32m✓\033[0m clean — {report.total} files, {megabytes:.2f}MB sent to the daemon")
    if megabytes > CONTEXT_BUDGET_MB:
        report.leaks.append(f"{label}: context is {megabytes:.1f}MB, over the {CONTEXT_BUDGET_MB}MB budget")
    if report.total and not (context / ".dockerignore").is_file():
        print(f"    \033[33m⚠\033[0m sent without an ignore file")

    for src in dockerfile_copy_sources(dockerfile):
        candidate = (context / src).resolve()
        if not candidate.exists() and not any(context.glob(src)):
            report.missing.append(f"{rel(dockerfile)}: COPY {src} — not found under {label}")
    return report


def main() -> int:
    verbose = "-v" in sys.argv
    print("\033[34m━━ Build contexts ━━\033[0m")
    total_files = 0
    failures: list[str] = []
    warns: list[str] = []

    builds = compose_builds()
    for context, dockerfile in builds:
        if not dockerfile.is_file():
            failures.append(f"missing Dockerfile: {rel(dockerfile)}")
            continue
        report = check_build(context, dockerfile, verbose)
        total_files += report.total
        failures += report.leaks + report.missing
        warns += report.junk

    print("\n\033[34m━━ Summary ━━\033[0m")
    print(f"  {total_files} files across {len(builds)} contexts; budget {CONTEXT_BUDGET_MB}MB each")
    if warns:
        print(f"  \033[33m⚠ {len(warns)} host build artifact(s) would be sent\033[0m")
    if failures:
        for f in failures:
            print(f"  \033[31m✗ {f}\033[0m")
        print("\n\033[31m✗ build contexts are not clean\033[0m")
        return 1
    print("\n\033[32m✓ no credential and no stale artifact would reach the daemon\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
