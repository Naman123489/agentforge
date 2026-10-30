
"""Create Git checkpoints for approved Reviewer stages."""

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import subprocess
import uuid

from .schemas import Checkpoint, ReviewReport


def _git(repo: Path, *args: str) -> str:
    """Run Git and return standard output."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: git {' '.join(args)}\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_hashes(repo: Path) -> dict[str, str]:
    """Hash tracked files in the working tree."""
    hashes = {}
    for relative in _git(repo, "ls-files", "-z").split("\0"):
        if not relative:
            continue
        path = repo / relative
        if path.is_file():
            hashes[relative] = _sha256_file(path)
    return hashes


def create_checkpoint(
    repo_path: str | Path,
    report: ReviewReport,
    contract_path: str | Path,
    summary: str | None = None,
    run_id: str | None = None,
) -> Checkpoint:
    """Tag a clean, committed Git tree after an approved review."""
    if report.verdict != "approve":
        raise ValueError("Cannot checkpoint a rejected review.")

    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"Not a Git repository: {repo}")

    if _git(repo, "status", "--porcelain"):
        raise RuntimeError(
            "Working tree is not clean. Commit or discard changes first."
        )

    commit = _git(repo, "rev-parse", "HEAD")

    contract = Path(contract_path)
    if not contract.is_absolute():
        contract = repo / contract
    if not contract.is_file():
        raise ValueError(f"Contract file not found: {contract}")

    contract_hash = _sha256_file(contract)
    file_hashes = _file_hashes(repo)

    # Keep the checkpoint tag unique.
    base_tag = f"stage-{report.stage_id}-green"
    tag = base_tag
    suffix = 2

    while subprocess.run(
        [
            "git", "-C", str(repo), "rev-parse",
            "-q", "--verify", f"refs/tags/{tag}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    ).returncode == 0:
        tag = f"{base_tag}-{suffix}"
        suffix += 1

    checkpoint = Checkpoint(
        id=str(uuid.uuid4()),
        run_id=run_id or str(uuid.uuid4()),
        stage_id=report.stage_id,
        git_tag=tag,
        summary=summary or f"Approved stage {report.stage_id}",
        tests_passed=sum(1 for check in report.checks if check.passed),
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    checkpoint_dir = repo / ".agentforge" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = checkpoint_dir / "checkpoint.json"
    checkpoint_file.write_text(
        checkpoint.model_dump_json(indent=2),
        encoding="utf-8",
    )

    try:
        _git(
            repo,
            "tag",
            "-a",
            tag,
            commit,
            "-m",
            f"Approved stage {report.stage_id}: {checkpoint.summary}",
        )
    except Exception:
        checkpoint_file.unlink(missing_ok=True)
        raise

    return checkpoint

