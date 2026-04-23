"""Fixture tests for QueryValidationModule._validate_single.

These pin down the validation control flow before we split the 280-line
method into handlers. Every scenario is fully stubbed — no network I/O.

Scenarios covered:
1. happy path: OK count + passing relevance -> valid
2. too narrow, refines once, passes
3. too broad, exhausts refinements, falls through to regeneration
4. zero results, refines, stays zero -> rejected
5. S2 rate-limited -> unvalidated (result_count=None, explicit details)
6. relevance fail, refines, passes on second try
7. relevance early-exit (math-impossible) -> rejected
"""

from unittest.mock import MagicMock

from roboscout.models import GeneratedQuery, QueryCategory, QueryRequest
from roboscout.modules import QueryValidationModule
from roboscout.semantic_scholar import S2Result, S2Status


def _ok(total, papers=None):
    return S2Result(status=S2Status.OK, total=total, papers=papers or [])


def _papers(n=5):
    return [{"title": f"Paper {i}", "abstract": f"abs {i}"} for i in range(n)]


def _make_validator(monkeypatch, s2_results, relevance_results=None, refine_fn=None):
    """Build a QueryValidationModule with stub s2 + stub gen module."""
    s2 = MagicMock()
    s2.search_relevance = MagicMock(side_effect=list(s2_results))

    gen = MagicMock()
    # Default: refine produces a new query with round+1
    def _default_refine(q, req, problem, target_range):
        return GeneratedQuery(
            query=f"{q.query} refined",
            target_soi=q.target_soi,
            rationale="refined",
            refinement_round=q.refinement_round + 1,
            original_query=q.original_query or q.query,
            refinement_reason=problem,
        )
    gen.forward_refine = MagicMock(side_effect=refine_fn or _default_refine)

    if relevance_results is not None:
        gen.forward_relevance = MagicMock(side_effect=list(relevance_results))
    else:
        gen.forward_relevance = MagicMock(
            return_value={
                "relevance_ratio": 1.0,
                "summary": "all relevant",
                "relevant_count": 5,
                "total_checked": 5,
            }
        )

    gen.forward_regenerate = MagicMock(return_value=None)

    vm = QueryValidationModule(s2, gen)
    # Tight budgets so tests fail fast if they diverge.
    vm.MAX_REFINEMENT_ROUNDS = 2
    vm.RELEVANCE_THRESHOLD = 0.6
    vm.PAPERS_TO_CHECK = 5
    vm.EARLY_CHECK_SIZE = 5
    return vm, s2, gen


def _q(text="neural networks", soi="ML"):
    return GeneratedQuery(query=text, target_soi=soi, rationale="test")


def _req():
    return QueryRequest(title="t", looking_for="lf", solutions_of_interest="ML")


def test_happy_path_returns_validated(monkeypatch):
    vm, s2, gen = _make_validator(monkeypatch, [_ok(100, _papers(5))])
    out = vm._validate_single(_q(), _req())
    assert out.result_count == 100
    assert out.category is QueryCategory.SPECIFIC
    assert out.relevance_passed is True
    assert out.is_valid
    # One S2 call, one relevance call, zero refines.
    assert s2.search_relevance.call_count == 1
    assert gen.forward_refine.call_count == 0


def test_too_narrow_refines_then_passes(monkeypatch):
    vm, s2, gen = _make_validator(
        monkeypatch,
        s2_results=[_ok(5, _papers(3)), _ok(100, _papers(5))],
    )
    out = vm._validate_single(_q(), _req())
    assert out.is_valid
    assert out.refinement_round == 1
    assert gen.forward_refine.call_count == 1
    # Refine problem should mention "20-result minimum" for narrow.
    problem = gen.forward_refine.call_args_list[0].kwargs["problem"]
    assert "20-result" in problem or "too narrow" in problem.lower() or "below" in problem


def test_too_broad_exhausts_refinements_and_rejects(monkeypatch):
    # 3 broad results (original + 2 refinements), all too broad.
    vm, s2, gen = _make_validator(
        monkeypatch,
        s2_results=[_ok(5000, []), _ok(5000, []), _ok(5000, [])],
    )
    out = vm._validate_single(_q(), _req())
    assert out.category is QueryCategory.TOO_BROAD
    assert not out.is_valid
    assert gen.forward_refine.call_count == 2  # MAX_REFINEMENT_ROUNDS


def test_zero_results_then_zero_rejects(monkeypatch):
    vm, s2, gen = _make_validator(
        monkeypatch,
        s2_results=[_ok(0, []), _ok(0, []), _ok(0, [])],
    )
    out = vm._validate_single(_q(), _req())
    assert out.result_count == 0
    assert out.relevance_passed is False
    assert "Zero results" in (out.relevance_details or "")
    assert not out.is_valid


def test_s2_rate_limited_returns_unvalidated(monkeypatch):
    vm, s2, gen = _make_validator(
        monkeypatch,
        s2_results=[S2Result(status=S2Status.RATE_LIMITED, error="429")],
    )
    out = vm._validate_single(_q(), _req())
    assert out.result_count is None
    assert out.is_unvalidated
    assert "rate_limited" in out.relevance_details


def test_relevance_fail_then_pass_on_refinement(monkeypatch):
    relevance_calls = iter([
        {"relevance_ratio": 0.2, "summary": "mostly off-topic",
         "relevant_count": 1, "total_checked": 5},
        {"relevance_ratio": 1.0, "summary": "all relevant",
         "relevant_count": 5, "total_checked": 5},
    ])
    vm, s2, gen = _make_validator(
        monkeypatch,
        s2_results=[_ok(200, _papers(5)), _ok(200, _papers(5))],
        relevance_results=relevance_calls,
    )
    out = vm._validate_single(_q(), _req())
    assert out.is_valid
    assert out.refinement_round == 1


def test_relevance_early_exit_math_impossible(monkeypatch):
    """If first batch shows 0 relevant and math says we can't hit threshold,
    the code should early-exit rather than checking all papers. Repeated
    across all refinement rounds since we never find a good query."""
    vm, s2, gen = _make_validator(
        monkeypatch,
        s2_results=[_ok(200, _papers(5))] * 3,
    )
    # Every relevance call returns 0/5 — math can't reach 0.6. Refinements
    # keep making it worse; eventually budget exhausted.
    gen.forward_relevance = MagicMock(
        return_value={
            "relevance_ratio": 0.0, "summary": "none relevant",
            "relevant_count": 0, "total_checked": 5,
        }
    )
    out = vm._validate_single(_q(), _req())
    assert out.relevance_passed is False
    details = (out.relevance_details or "").lower()
    assert "early exit" in details or "impossible" in details


def test_no_papers_but_positive_count_passes(monkeypatch):
    """Edge case: S2 returns total > 0 but no paper details.
    Should skip relevance check and mark as passing."""
    vm, s2, gen = _make_validator(
        monkeypatch,
        s2_results=[_ok(50, [])],
    )
    out = vm._validate_single(_q(), _req())
    assert out.is_valid
    assert out.relevance_passed is True
    assert "No papers" in (out.relevance_details or "")
