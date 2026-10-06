"""Fitting, the gate, the report, the card and the export, in SYNTHETIC worlds.

These check that the machinery does what the manifest says. The numbers they produce
describe formulas, not landslides.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import synthetic
from rasta_ml.dataset import build_dataset, label_audit, split_record
from rasta_ml.evaluate import decide, evaluate
from rasta_ml.events import Inventory, select_events
from rasta_ml.metrics import average_precision, brier
from rasta_ml.models import (
    BASELINE_WEIGHTS,
    RAINFALL_FULL_SCALE_MM_48H,
    SLOPE_FULL_SCALE_DEG,
)

BENCHMARKS = ("baseline-v1", "rainfall rule", "climatology")
CANDIDATES = ("logistic regression", "gradient-boosted trees")
API = Path(__file__).resolve().parents[2] / "api" / "app"


def prepared(signal: bool = True):
    m = synthetic.manifest()
    rain, first, terrain, events, grid = synthetic.world(m, signal=signal)
    selection = select_events(events, m, grid)
    dataset, built = build_dataset(rain, first, terrain, selection.kept, m, grid)
    inventory = Inventory(events=events, rows=len(events))
    audit = label_audit(inventory, selection, dataset, built, m, grid, "0" * 64)
    return m, grid, dataset, audit, split_record(dataset, m)


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):
    m, grid, dataset, audit, split = prepared()
    out = tmp_path_factory.mktemp("report")
    report = evaluate(
        dataset, split, m, grid, audit=audit, inputs={"synthetic": True}, out=out
    )
    return m, dataset, report, out


def test_every_benchmark_and_candidate_is_scored_on_the_test_years(evaluated) -> None:
    _, dataset, report, out = evaluated
    assert set(report["results"]) == {*BENCHMARKS, *CANDIDATES}
    for name, item in report["results"].items():
        assert item["pr_auc"] is not None, name
        assert report["bootstrap"]["pr_auc"][name] is not None, name
    assert report["dataset"]["sha256"] == dataset.sha256()
    assert (
        json.loads((out / "evaluation.json").read_text())["decision"]
        == report["decision"]
    )


def test_the_decision_is_the_rule_applied_to_the_reported_numbers(evaluated) -> None:
    m, _, report, _ = evaluated
    intervals = report["bootstrap"]["pr_auc"]
    briers = {name: item["brier"] for name, item in report["results"].items()}
    enough = report["dataset"]["positives"]["test"] >= m.labels.min_test_positives
    for name in CANDIDATES:
        expected = (
            enough
            and all(intervals[name][0] > intervals[b][1] for b in BENCHMARKS)
            and briers[name] < briers["climatology"]
        )
        assert report["decision"][name]["passed"] == expected
        assert report["decision"][name]["passed"] == (
            not report["decision"][name]["reasons"]
        )


def test_the_card_has_every_section_a_reader_needs(evaluated) -> None:
    _, _, _, out = evaluated
    card = (out / "model_card.md").read_text(encoding="utf-8")
    for heading in (
        "## Question",
        "## Data",
        "## Split",
        "## Results on the test years",
        "## Calibration",
        "## Decision",
        "## Limits",
        "## Intended use",
        "## Exported model",
    ):
        assert heading in card
    assert "SYNTHETIC" in card  # the synthetic world's own words reach the card


def test_the_exported_model_is_hashed_and_reproduces_the_candidate(evaluated) -> None:
    _, dataset, report, out = evaluated
    raw = (out / "model_logistic.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == report["exported_model"]["sha256"]
    model = json.loads(raw)
    assert model["schema_version"] == "rasta-risk-model-v1"
    assert model["decision"] == report["decision"]["logistic regression"]

    test = dataset.mask("test")
    z = np.full(int(test.sum()), model["intercept"])
    for index, feature in enumerate(model["features"]):
        value = dataset.X[test, index].astype(np.float64)
        if feature["transform"] == "log1p":
            value = np.log1p(np.maximum(value, 0.0))
        z += feature["coefficient"] * (value - feature["mean"]) / feature["scale"]
    probability = 1 / (1 + np.exp(-z))
    result = report["results"]["logistic regression"]
    assert average_precision(dataset.y[test], probability) == pytest.approx(
        result["pr_auc"], abs=1e-6
    )
    assert brier(dataset.y[test], probability) == pytest.approx(
        result["brier"], rel=1e-3
    )


def test_with_labels_unrelated_to_rain_or_slope_nothing_passes(tmp_path) -> None:
    m, grid, dataset, audit, split = prepared(signal=False)
    report = evaluate(dataset, split, m, grid, audit=audit, inputs={}, out=tmp_path)
    assert not any(item["passed"] for item in report["decision"].values())
    assert "no candidate passed" in (tmp_path / "model_card.md").read_text()


def test_a_dataset_that_is_not_the_one_split_is_refused(tmp_path) -> None:
    m, grid, dataset, audit, split = prepared()
    split["dataset_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="different dataset"):
        evaluate(dataset, split, m, grid, audit=audit, inputs={}, out=tmp_path)


def test_the_gate_lists_every_unmet_condition() -> None:
    intervals = {
        "model": [0.20, 0.30],
        "baseline-v1": [0.05, 0.10],
        "climatology": [0.15, 0.22],
    }
    briers = {"model": 0.010, "baseline-v1": 0.02, "climatology": 0.009}
    decision = decide("model", ["baseline-v1", "climatology"], intervals, briers, 3, 20)
    assert not decision["passed"]
    assert len(decision["reasons"]) == 3  # too few events, overlap, Brier
    clear = decide(
        "model",
        ["baseline-v1"],
        intervals,
        {**briers, "climatology": 0.011},
        25,
        20,
    )
    assert clear == {"passed": True, "reasons": []}


def _assigned(path: Path, name: str):
    """The literal value assigned to ``name`` anywhere in a source file."""

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise LookupError(name)


@pytest.mark.skipif(
    not API.is_dir(), reason="the API source is not next to this package"
)
def test_the_baseline_benchmark_uses_the_apis_own_numbers() -> None:
    weights = _assigned(API / "risk_engine.py", "WEIGHTS")
    for name, weight in BASELINE_WEIGHTS.items():
        assert weights[name] == weight
    assert (
        _assigned(API / "sources.py", "FULL_SCALE_MM_48H") == RAINFALL_FULL_SCALE_MM_48H
    )
    assert (
        _assigned(API / "terrain.py", "DEFAULT_FULL_SCALE_DEG") == SLOPE_FULL_SCALE_DEG
    )
