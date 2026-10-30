"""Per-run workspace: file ownership rules, git checkpoints and rollback (FR-6, FR-7, FR-8)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

OWNERSHIP = {"backend": "backend/", "frontend": "frontend/", "planner": "contract/"}
MAX_FILE_BYTES = 200_000


class OwnershipError(ValueError):
    pass


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        if not (root / ".git").exists():
            self._git("init", "-q")
            self._git("config", "user.email", "agentforge@local")
            self._git("config", "user.name", "AgentForge")
            (root / ".gitignore").write_text("__pycache__/\n*.db\n.pytest_cache/\n")
            self.commit("init")

    # ---- files -------------------------------------------------------------------
    def safe_path(self, rel: str, owner: str | None = None) -> Path:
        p = PurePosixPath(rel.replace("\\", "/").lstrip("/"))
        if ".." in p.parts or not p.parts or p.parts[0] == ".git":
            raise OwnershipError(f"illegal path {rel!r}")
        if owner and not str(p).startswith(OWNERSHIP[owner]):
            raise OwnershipError(f"{owner} agent may only write {OWNERSHIP[owner]}**, not {p}")
        return self.root / p

    def write(self, rel: str, content: str, owner: str | None = None) -> str:
        if len(content.encode()) > MAX_FILE_BYTES:
            raise OwnershipError(f"{rel} is larger than {MAX_FILE_BYTES} bytes")
        path = self.safe_path(rel, owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return str(path.relative_to(self.root).as_posix())

    def write_json(self, rel: str, data) -> None:
        self.write(rel, json.dumps(data, indent=2))

    def read(self, rel: str) -> str:
        return self.safe_path(rel).read_text(encoding="utf-8")

    def files(self, prefix: str = "") -> list[str]:
        out = []
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root).as_posix()
            if p.is_file() and not rel.startswith(".git/") and "__pycache__" not in rel \
                    and not rel.endswith((".db", ".pyc")) and rel.startswith(prefix):
                out.append(rel)
        return out

    def snapshot(self, prefix: str, max_chars: int = 60_000) -> str:
        """Current files under prefix, formatted for a prompt."""
        parts, total = [], 0
        for rel in self.files(prefix):
            body = self.read(rel)
            if total + len(body) > max_chars:
                parts.append(f"=== FILE: {rel} === (omitted, too long)")
                continue
            parts.append(f"=== FILE: {rel} ===\n{body}\n=== END FILE ===")
            total += len(body)
        return "\n\n".join(parts) or "(no files yet)"

    # ---- git ---------------------------------------------------------------------
    def _git(self, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    def commit(self, message: str) -> str:
        self._git("add", "-A")
        if self._git("status", "--porcelain"):
            self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "--short", "HEAD")

    def tag(self, name: str, message: str) -> None:
        self._git("tag", "-f", "-a", name, "-m", message)

    def tags(self) -> list[str]:
        return [t for t in self._git("tag", "--sort=creatordate").splitlines() if t]

    def rollback(self, tag: str) -> None:
        self._git("reset", "-q", "--hard", tag)
        self._git("clean", "-q", "-fd")
