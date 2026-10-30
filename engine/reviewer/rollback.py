
"""Safe rollback helpers for the AI2 Reviewer."""

from pathlib import Path
import subprocess


def _git(repo: Path, *args: str) -> str:
    """Run Git and return standard output."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: git {' '.join(args)}\n"
            f"{result.stderr.strip()}"
        )

    return result.stdout.strip()


def restore(
    repo_path: str | Path,
    tag: str,
    *,
    allow_dirty: bool = False,
) -> bool:
    """
    Restore a dedicated workspace to an annotated stage checkpoint.

    Untracked files are preserved. This changes the current branch and
    discards tracked-file changes when allow_dirty=True, so use only
    with an isolated run workspace.
    """
    repo = Path(repo_path).resolve()

    if not (repo / ".git").exists():
        raise ValueError(f"Not a Git repository: {repo}")

    if (
        not tag.startswith("stage-")
        or "-green" not in tag
        or tag.startswith("-")
        or "/" in tag
        or "\\" in tag
        or ".." in tag
    ):
        raise ValueError("Rollback target must be a valid stage checkpoint tag.")

    # Require an existing annotated tag rather than accepting an arbitrary
    # revision or a lightweight tag.
    tag_type = _git(repo, "cat-file", "-t", f"refs/tags/{tag}")
    if tag_type != "tag":
        raise ValueError(
            "Rollback target must be an existing annotated checkpoint tag."
        )

    commit = _git(
        repo,
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
    )

    status = _git(repo, "status", "--porcelain")
    if status and not allow_dirty:
        raise RuntimeError(
            "Workspace has uncommitted changes. Preserve failure logs "
            "and clean or archive the workspace before rollback."
        )

    # Reset tracked files only. Do not run git clean: untracked files,
    # including failure logs and generated artifacts, must be preserved.
    _git(repo, "reset", "--hard", commit)

    restored_commit = _git(repo, "rev-parse", "HEAD")
    if restored_commit != commit:
        raise RuntimeError("Rollback verification failed.")

    return True
