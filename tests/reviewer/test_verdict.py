import pytest

from engine.reviewer.schemas import InternalCheckResult
from engine.reviewer.verdict import verdict

POLICY = {
    "contract": "required",
    "pytest": "required",
    "ruff": "advisory",
}

def make_result(
    name: str,
    passed: bool,
    exit_code: int | None,
    required: bool | None = None,
) -> InternalCheckResult:
    if required is None:
        required = POLICY[name] == "required"

    return InternalCheckResult(
        name=name,
        passed=passed,
        required=required,
        duration_s=0.1,
        exit_code=exit_code,
        log_tail="Test evidence",
    )

def test_approves_when_all_required_checks_pass():
    results = [
        make_result("contract", True, 0),
        make_result("pytest", True, 0),
        make_result("ruff", False, 1),
    ]

    assert verdict(results, POLICY) == "approve"

def test_rejects_when_required_check_fails():
    results = [
        make_result("contract", True, 0),
        make_result("pytest", False, 1),
    ]

    assert verdict(results, POLICY) == "reject"

def test_rejects_when_required_check_is_missing():
    results = [
        make_result("contract", True, 0),
    ]

    assert verdict(results, POLICY) == "reject"

def test_rejects_when_required_check_has_no_exit_code():
    results = [
        make_result("contract", True, 0),
        make_result("pytest", True, None),
    ]

    assert verdict(results, POLICY) == "reject"

def test_rejects_duplicate_required_check_results():
    results = [
        make_result("contract", True, 0),
        make_result("pytest", True, 0),
        make_result("pytest", True, 0),
    ]

    assert verdict(results, POLICY) == "reject"

def test_rejects_invalid_policy():
    with pytest.raises(ValueError):
        verdict([], {"pytest": "sometimes"})
