
from datetime import datetime, timezone

import pytest

from engine.reviewer.checkpoint import create_checkpoint
from engine.reviewer.schemas import ReviewReport


def make_report(verdict="approve"):
    return ReviewReport(
        stage_id="stage-1",
        verdict=verdict,
        checks=[],
        required_fixes=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def test_rejected_review_cannot_create_checkpoint(tmp_path):
    report = make_report("reject")

    with pytest.raises((ValueError, RuntimeError)):
        create_checkpoint(
            repo_path=tmp_path,
            report=report,
            contract_path=tmp_path / "openapi.yaml",
        )


def test_checkpoint_requires_git_repository(tmp_path):
    report = make_report("approve")

    with pytest.raises((ValueError, RuntimeError, OSError)):
        create_checkpoint(
            repo_path=tmp_path,
            report=report,
            contract_path=tmp_path / "openapi.yaml",
        )
