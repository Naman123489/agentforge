"""Safe staging and secret checks for the deploy agent."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import zipfile
import base64
import codecs
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Iterator, Literal
from urllib.parse import unquote


_GH_PREFIX = "gh" + "p_"
_PAT_PREFIX = "github" + "_pat_"
_NV_PREFIX = "nv" + "api-"
_PRIVATE_HEADER = "BEGIN" + " PRIVATE KEY"
_BLOCK_PATTERN = re.compile(
    (
        rf"{_NV_PREFIX}[A-Za-z0-9_\-]+|{_GH_PREFIX}[A-Za-z0-9_\-]+|{_PAT_PREFIX}[A-Za-z0-9_\-]+"
        rf"|{_PRIVATE_HEADER}|NEBIUS_API_KEY\s*="
    ).encode("ascii")
    +
    rb"|\bAKIA[0-9A-Z]{16}\b"
    rb"|-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----"
    rb"|_authToken\s*=\s*[^\s]+"
    rb"|\bxox[a-z]-[A-Za-z0-9-]{10,}"
    rb"|\bsk_live_[A-Za-z0-9]{10,}",
    re.IGNORECASE,
)
_WARN_PATTERN = re.compile(rb"\b(?:api[_-]?key|secret[_-]?key)\s*=\s*(['\"])[^'\"\r\n]+\1", re.IGNORECASE)
_REDACT_TOKEN_PATTERN = re.compile(
    rf"{_GH_PREFIX}[A-Za-z0-9]+|{_PAT_PREFIX}[A-Za-z0-9_]+|{_NV_PREFIX}[A-Za-z0-9_-]+"
)
_AUTH_HEADER_PATTERN = re.compile(r"(?im)(authorization\s*:\s*(?:basic|bearer)\s+)[^\r\n]*")
_BASE64_CANDIDATE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{24,}(?![A-Za-z0-9+/=])")
_URL_CANDIDATE = re.compile(r"[A-Za-z0-9%._~-]{20,}")
_SPLIT_TOKEN = re.compile(rf"(?:{_GH_PREFIX}|{_PAT_PREFIX}|{_NV_PREFIX})[ \t]*\r?\n[ \t]*[A-Za-z0-9_-]{{20,}}")
_DECODED_MARKERS = (b"x-access-token:", _GH_PREFIX.encode(), _PAT_PREFIX.encode(), _NV_PREFIX.encode(), b"Bearer ")
_EXCLUDED_DIRS = frozenset(
    {"node_modules", "__pycache__", ".venv", ".agentforge", "dist", ".pytest_cache", ".git"}
)
_SECRET_FILENAMES = frozenset({"id_rsa", ".npmrc", ".pypirc", "credentials.json"})
_ALLOWED_BINARY_TYPES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".wav", ".ogg"})
_BLOCKED_FILE_TYPES = frozenset({".exe", ".dll", ".so", ".dylib", ".pyc", ".db", ".sqlite", ".zip", ".tar", ".gz", ".7z"})


@dataclass(frozen=True)
class ScanFinding:
    file: str
    severity: Literal["block", "warn"]
    line: int | None = None
    reason: str = ""

    def __str__(self) -> str:
        location = f"{self.file}:{self.line}" if self.line is not None else self.file
        if self.reason in {"credential pattern", "sensitive file name", "file type blocked", "symlink"}:
            return location
        return f"{location} ({self.reason})" if self.reason else location


@dataclass(eq=False)
class ScanReport:
    blocks: list[ScanFinding] = field(default_factory=list)
    warnings: list[ScanFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    excluded_files: list[str] = field(default_factory=list)

    # Keep earlier callers that used scan_for_secrets() as a list of blocking paths.
    def __iter__(self) -> Iterator[str]:
        return (str(item) for item in self.blocks)

    def __len__(self) -> int:
        return len(self.blocks)

    def __getitem__(self, index: int) -> str:
        return str(self.blocks[index])

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return list(self) == other
        if isinstance(other, ScanReport):
            return self.__dict__ == other.__dict__
        return NotImplemented


def redact(value: Any) -> Any:
    """Remove credentials from text, exceptions, and nested output structures."""
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="surrogateescape")
        return redact(decoded).encode("utf-8", errors="surrogateescape")
    if isinstance(value, dict):
        return {redact(key): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, BaseException):
        value = str(value)
    if not isinstance(value, str):
        return value
    def scrub_base64(match: re.Match[str]) -> str:
        candidate = match.group()
        try:
            padded = candidate.rstrip("=")
            raw = base64.b64decode(padded + "=" * (-len(padded) % 4), validate=False)
        except (ValueError, base64.binascii.Error):
            return candidate
        return "[REDACTED]" if any(marker in raw for marker in _DECODED_MARKERS) else candidate

    def scrub_url_encoded(match: re.Match[str]) -> str:
        candidate = match.group()
        if "%" not in candidate:
            return candidate
        decoded = unquote(candidate)
        return "[REDACTED]" if _REDACT_TOKEN_PATTERN.search(decoded) or "Bearer " in decoded else candidate

    value = _BASE64_CANDIDATE.sub(scrub_base64, value)
    value = _URL_CANDIDATE.sub(scrub_url_encoded, value)
    value = _SPLIT_TOKEN.sub("[REDACTED]", value)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        value = value.replace(token, "[REDACTED]")
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        value = value.replace(encoded, "[REDACTED]")
    value = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(NEBIUS_API_KEY\s*=\s*)[^\s]+", r"\1[REDACTED]", value)
    value = _REDACT_TOKEN_PATTERN.sub("[REDACTED]", value)
    value = value.replace(_PRIVATE_HEADER, "[REDACTED]")
    return value


def sanitize_repo_name(name: str) -> str:
    """Return a GitHub-safe repository name, capped at 80 characters."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80].rstrip("-")
    if not cleaned:
        raise ValueError("repository name is empty after sanitizing")
    return cleaned


def _safe_relative_path(value: str | Path) -> Path:
    raw = str(value)
    path = Path(raw)
    if (
        not raw
        or ".." in raw
        or raw.startswith(("/", "\\"))
        or path.is_absolute()
        or bool(PureWindowsPath(raw).drive)
    ):
        raise ValueError("path must be relative and must not contain '..'")
    return path


def is_link(path: Path) -> bool:
    """Symlink or Windows junction/reparse point. Path.is_symlink() is False for junctions, which can be created
    without admin rights, and rglob() walks through them (hardening attack 4)."""
    path = Path(path)
    try:
        if path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False


def safe_deploy_dir(root: Path, target: Path) -> Path:
    """Refuse a deploy-owned directory (staging, cache, history, lock) if it or any parent below the workspace is
    a link, or if it resolves outside the workspace. Called before anything is deleted or written there."""
    root = Path(root).resolve(strict=True)
    target = Path(target)
    relative = target.relative_to(root) if target.is_absolute() else target
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if is_link(current):
                raise ValueError(f"refusing to use a link in the deploy path: {current.relative_to(root).as_posix()}")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("deploy path escapes the workspace (link)")
    return root / relative


def _walk_files(source: Path) -> Iterator[Path]:
    """Every file under source without following links; a link anywhere is an error."""
    if is_link(source):
        raise ValueError("symlink or junction paths are not allowed (link)")
    if source.is_file():
        yield source
        return
    for entry in sorted(source.iterdir()):
        if is_link(entry):
            yield entry  # the caller decides: excluded paths are skipped, others refused
            continue
        if entry.is_dir():
            yield entry
            yield from _walk_files(entry)
        else:
            yield entry


def _excluded(relative: Path) -> bool:
    parts = tuple(part.lower() for part in relative.parts)
    if any(part in _EXCLUDED_DIRS or part.startswith(".env") for part in parts):
        return True
    return relative.suffix.lower() in {".db", ".sqlite"}


def stage_workspace(workspace_root: Path, paths: Iterable[str | Path] | None = None) -> Path:
    """Copy selected workspace paths into .agentforge/deploy/staging.

    Paths are relative to the workspace. Symlinks are rejected so a copy cannot
    escape the workspace or change between validation and scanning.
    """
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace_root must be a directory")
    selected = [_safe_relative_path(path) for path in paths] if paths is not None else [Path(".")]
    staging = safe_deploy_dir(root, root / ".agentforge" / "deploy" / "staging")
    candidates: set[Path] = set()
    for relative in selected:
        source = root / relative
        if is_link(source):
            raise ValueError("symlink paths are not allowed (link)")
        if not source.exists():
            raise ValueError(f"workspace path does not exist: {relative.as_posix()}")
        entries = _walk_files(source)
        for entry in entries:
            rel = entry.relative_to(root)
            if _excluded(rel):
                continue
            if is_link(entry):
                raise ValueError("symlink or junction paths are not allowed (link)")
            if entry.is_file():
                candidates.add(rel)
    if staging.exists():
        if staging.is_symlink():
            raise ValueError("staging directory must not be a symlink")
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for rel in sorted(candidates):
        destination = staging / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, destination)
    return staging


def resolve_tag_commit(workspace_root: Path, git_tag: str, runner: Any, timeout: float = 15.0) -> str | None:
    """Resolve a local tag to a commit only when workspace_root is the repo root."""
    try:
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            return None
        repository = runner.run(["git", "rev-parse", "--show-toplevel"], workdir=root, timeout=timeout)
        if repository.returncode or Path(repository.stdout.strip()).resolve() != root:
            return None
        reference = f"refs/tags/{git_tag}^{{commit}}"
        resolved = runner.run(
            ["git", "rev-parse", "--verify", "--end-of-options", reference], workdir=root, timeout=timeout
        )
        commit = resolved.stdout.strip().lower()
        if resolved.returncode or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            return None
        return commit
    except (OSError, ValueError, AttributeError):
        return None


def stage_tag_archive(
    workspace_root: Path,
    source_commit: str,
    runner: Any,
    staging_dir: Path | None = None,
    timeout: float = 60.0,
    max_file_bytes: int = 50 * 1024 * 1024,
    max_total_bytes: int = 500 * 1024 * 1024,
) -> tuple[Path, list[str], list[str]]:
    """Export one resolved commit to staging, skipping excluded or unsafe entries."""
    root = Path(workspace_root).resolve(strict=True)
    staging = Path(staging_dir) if staging_dir is not None else safe_deploy_dir(root, root / ".agentforge" / "deploy" / "staging")
    with tempfile.NamedTemporaryFile(prefix="agentforge-tag-", suffix=".zip", delete=False) as handle:
        archive_path = Path(handle.name)
    if archive_path.resolve().is_relative_to(root):
        archive_path.unlink(missing_ok=True)
        raise ValueError("temporary archive must be outside the workspace")
    excluded: list[str] = []
    notes: list[str] = []
    try:
        archived = runner.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.longpaths=true", "archive", "--format=zip", "-o", str(archive_path), source_commit, "--"],
            workdir=root, timeout=timeout,
        )
        if archived.returncode:
            raise ValueError(f"git archive failed: {redact(archived.stderr or archived.stdout)}")
        if staging.exists():
            if staging.is_symlink():
                raise ValueError("staging directory must not be a symlink")
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        total = 0
        try:
            with zipfile.ZipFile(archive_path) as source:
                for member in source.infolist():
                    raw = member.filename
                    relative = PurePosixPath(raw)
                    if (
                        not raw or raw.startswith(("/", "\\")) or relative.is_absolute()
                        or ".." in relative.parts or PureWindowsPath(raw).drive
                        or "\\" in raw
                    ):
                        notes.append("skipped unsafe archive entry")
                        continue
                    name = relative.as_posix().rstrip("/")
                    if not name or name == ".":
                        continue
                    path = Path(*relative.parts)
                    if _excluded(path):
                        excluded.append(name)
                        continue
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        notes.append(f"skipped archive symlink: {name}")
                        continue
                    if member.is_dir():
                        continue
                    if member.file_size > max_file_bytes:
                        raise ValueError(f"archive file exceeds {max_file_bytes} byte limit: {name}")
                    total += member.file_size
                    if total > max_total_bytes:
                        raise ValueError(f"archive total exceeds {max_total_bytes} byte limit")
                    destination = staging / path
                    if not destination.resolve().is_relative_to(staging.resolve()):
                        notes.append("skipped unsafe archive entry")
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as reader, destination.open("xb") as writer:
                        copied = 0
                        while chunk := reader.read(65536):
                            copied += len(chunk)
                            if copied > max_file_bytes or copied > member.file_size:
                                raise ValueError(f"archive file exceeds size limit: {name}")
                            writer.write(chunk)
            return staging, sorted(excluded), notes
        except Exception:
            shutil.rmtree(staging)
            raise
    finally:
        archive_path.unlink(missing_ok=True)


def scan_for_secrets(
    staged_dir: Path,
    max_file_bytes: int = 50 * 1024 * 1024,
    max_total_bytes: int = 500 * 1024 * 1024,
    apply_staging_exclusions: bool = False,
) -> ScanReport:
    """Scan raw bytes and return blocking findings, warnings, and file names."""
    root = Path(staged_dir).resolve(strict=True)
    report = ScanReport()
    total_bytes = 0
    for entry in sorted(root.rglob("*")):
        if is_link(entry):
            report.blocks.append(ScanFinding(entry.relative_to(root).as_posix(), "block", reason="symlink"))
            continue
        if not entry.is_file():
            continue
        rel = entry.relative_to(root)
        relative_name = rel.as_posix()
        if apply_staging_exclusions and _excluded(rel):
            report.excluded_files.append(relative_name)
            continue
        size = entry.stat().st_size
        total_bytes += size
        report.files_scanned.append(relative_name)
        if size > max_file_bytes:
            report.blocks.append(ScanFinding(relative_name, "block", reason=f"exceeds {max_file_bytes} byte file limit"))
            continue
        name = rel.name.lower()
        if (
            any(part.lower().startswith(".env") for part in rel.parts)
            or name in _SECRET_FILENAMES
            or name.endswith((".pem", ".p12"))
            or name.startswith("secrets.")
        ):
            report.blocks.append(ScanFinding(relative_name, "block", reason="sensitive file name"))
        if rel.suffix.lower() in _BLOCKED_FILE_TYPES:
            report.blocks.append(ScanFinding(relative_name, "block", reason="file type blocked"))
        binary_seen = False
        block_seen = any(item.file == relative_name for item in report.blocks)
        warning_lines: set[int] = set()
        bytes_read = 0
        newlines_read = 0
        utf8_decoder = codecs.getincrementaldecoder("utf-8")("strict")
        with entry.open("rb") as handle:
            overlap = b""
            while chunk := handle.read(65536):
                data = overlap + chunk
                if b"\x00" in chunk:
                    binary_seen = True
                if utf8_decoder is not None:
                    try:
                        utf8_decoder.decode(chunk, final=False)
                    except UnicodeDecodeError:
                        binary_seen = True
                        utf8_decoder = None
                if not block_seen and _BLOCK_PATTERN.search(data):
                    report.blocks.append(ScanFinding(relative_name, "block", reason="credential pattern"))
                    block_seen = True
                base_line = newlines_read - overlap.count(b"\n") + 1
                for match in _WARN_PATTERN.finditer(data):
                    if bytes_read - len(overlap) + match.end() <= bytes_read:
                        continue
                    line = base_line + data[: match.start()].count(b"\n")
                    if line not in warning_lines:
                        report.warnings.append(ScanFinding(relative_name, "warn", line=line, reason="generic secret assignment"))
                        warning_lines.add(line)
                bytes_read += len(chunk)
                newlines_read += chunk.count(b"\n")
                overlap = data[-256:]
        if utf8_decoder is not None:
            try:
                utf8_decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                binary_seen = True
        if binary_seen and rel.suffix.lower() not in _ALLOWED_BINARY_TYPES and rel.suffix.lower() not in _BLOCKED_FILE_TYPES:
            report.notes.append(f"unknown binary type: {relative_name}")
    if total_bytes > max_total_bytes:
        report.blocks.append(ScanFinding("", "block", reason=f"total staged size exceeds {max_total_bytes} byte limit"))
    return report
