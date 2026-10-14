from pathlib import Path

import yaml

from eval.common import expected_titles, load_yaml
from eval.run_search import compute_metrics, rank_of

SEED_PROGRAMS = Path(__file__).parent.parent / "seed" / "programs"


def row(id: str, expected, *hits):
    return {"id": id, "expected": expected, "hits": [{"title": t, "distance": d} for t, d in hits]}


def test_rank_of():
    assert rank_of(["a", "b", "c"], ["c", "b"]) == 2
    assert rank_of(["a"], ["x"]) is None


def test_metrics_and_threshold():
    rows = [
        row("p1", ["A"], ("A", 0.1), ("B", 0.4)),
        row("p2", ["A"], ("B", 0.2), ("A", 0.3)),
        row("n1", None, ("B", 0.6)),
    ]
    metrics = compute_metrics(rows, threshold=0.5)
    assert metrics["positives"] == 2 and metrics["negatives"] == 1
    assert metrics["hit@1"] == 0.5 and metrics["hit@3"] == 1.0
    assert metrics["mrr"] == (1 + 0.5) / 2
    # p1 верно найден, p2 первым кандидатом получил чужую операцию, n1 отсечён порогом
    assert metrics["decision_accuracy"] == 2 / 3
    assert metrics["max_good_distance"] == 0.1 and metrics["min_negative_distance"] == 0.6
    assert metrics["recommended_threshold"] == 0.35 and metrics["thresholds_separable"] is True


def test_negative_above_threshold_is_correct_and_below_is_not():
    rows = [row("n", None, ("A", 0.3))]
    assert compute_metrics(rows, 0.5)["decision_accuracy"] == 0.0
    assert compute_metrics(rows, 0.2)["decision_accuracy"] == 1.0


def test_queries_reference_existing_programs_and_types():
    titles = {
        yaml.safe_load(p.read_text("utf-8"))["title"] for p in SEED_PROGRAMS.glob("*.yaml")
    }
    queries = load_yaml("queries.yaml")
    assert len(queries) >= 20
    assert len({q["id"] for q in queries}) == len(queries)
    for query in queries:
        assert query["op_type"] in {"facing", "turning", "drilling", "threading"}
        expected = expected_titles(query)
        assert expected is None or set(expected) <= titles, query["id"]
