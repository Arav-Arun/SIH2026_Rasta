"""Fit the candidates, judge them against the benchmarks, write the report and model card.

The decision rule is the manifest's, applied mechanically: a candidate passes only if
its test PR-AUC interval lies wholly above every benchmark's and its Brier score is
below climatology's, with enough test events to judge at all. Anything else keeps
baseline-v1, and the card says why.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np

from rasta_ml.dataset import PARTS, Dataset
from rasta_ml.grid import Grid
from rasta_ml.manifest import Manifest
from rasta_ml.metrics import (
    at_threshold,
    average_precision,
    best_f1_threshold,
    brier,
    day_bootstrap,
    reliability,
    roc_auc,
)
from rasta_ml.models import (
    Baseline,
    Boosted,
    Climatology,
    Logistic,
    RainfallRule,
    Scorer,
)

MODEL_SCHEMA = "rasta-risk-model-v1"

FEATURE_MEANINGS = {
    "rain_1d": "rainfall on the day, mm",
    "rain_2d": "rainfall on the day and the day before, mm",
    "rain_3d": "rainfall over the three days ending on the day, mm",
    "rain_7d": "rainfall over the seven days ending on the day, mm",
    "rain_30d": "rainfall over the thirty days ending on the day, mm",
    "slope_mean_deg": "mean terrain slope in the cell, degrees",
    "slope_p90_deg": "90th-percentile terrain slope in the cell, degrees",
    "steep_fraction": "share of the cell steeper than 30 degrees",
    "relief_m": "elevation range between the cell's 5th and 95th percentiles, m",
}


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def decide(
    candidate: str,
    benchmarks: list[str],
    intervals: dict[str, list[float] | None],
    briers: dict[str, float],
    test_positives: int,
    min_positives: int,
) -> dict:
    """Apply the gate to one candidate. Every unmet condition is listed."""

    reasons = []
    if test_positives < min_positives:
        reasons.append(
            f"only {test_positives} positive cell-days in the test years; the manifest "
            f"asks for at least {min_positives} before judging"
        )
    own = intervals.get(candidate)
    for benchmark in benchmarks:
        other = intervals.get(benchmark)
        if own is None or other is None:
            reasons.append(f"no PR-AUC interval to compare with {benchmark}")
        elif not own[0] > other[1]:
            reasons.append(
                f"its PR-AUC interval {own} does not lie above {benchmark}'s {other}"
            )
    if not briers[candidate] < briers["climatology"]:
        reasons.append(
            f"its Brier score {briers[candidate]:.6f} is not below climatology's "
            f"{briers['climatology']:.6f}"
        )
    return {"passed": not reasons, "reasons": reasons}


def spatial_check(
    data: Dataset,
    grid: Grid,
    manifest: Manifest,
    logistic: Logistic,
    boosted: Boosted,
    rule: RainfallRule,
) -> dict:
    """A second view: train on some areas' early years, test on other areas' late years.

    Blocks of ``spatial_block_deg`` are dealt into folds in a seeded order. Settings
    chosen in the main fit are reused, not re-tuned.
    """

    block_deg = manifest.split.spatial_block_deg
    folds = manifest.split.spatial_folds
    blocks = sorted(
        {grid.block_of(int(cell), block_deg) for cell in np.unique(data.cell)}
    )
    order = np.random.default_rng(manifest.seed).permutation(len(blocks))
    fold_of_block = {blocks[i]: rank % folds for rank, i in enumerate(order)}
    fold_of_cell = {
        int(cell): fold_of_block[grid.block_of(int(cell), block_deg)]
        for cell in np.unique(data.cell)
    }
    row_fold = np.array([fold_of_cell[int(cell)] for cell in data.cell])

    results = []
    for fold in range(folds):
        train = data.mask("train") & (row_fold != fold)
        test = data.mask("test") & (row_fold == fold)
        y = data.y[test]
        entry: dict = {
            "fold": fold,
            "test_rows": int(test.sum()),
            "test_positives": int(y.sum()),
        }
        if y.sum() == 0 or data.y[train].sum() == 0:
            entry["note"] = "no positives to train or test on in this fold"
            results.append(entry)
            continue
        scorers: list[Scorer] = [
            Baseline(),
            rule,
            Climatology.fit(data, train),
            Logistic(C=logistic.C, seed=logistic.seed).fit(data, train),
            Boosted(params=dict(boosted.params), seed=boosted.seed).fit(data, train),
        ]
        entry["pr_auc"] = {
            scorer.name: _round(average_precision(y, scorer.score(data, test)))
            for scorer in scorers
        }
        results.append(entry)
    return {"block_deg": block_deg, "folds": results}


def export_logistic(model: Logistic, data: Dataset) -> dict:
    """The logistic model as plain numbers, with its calibration folded in.

    sigmoid(a * logit(sigmoid(z)) + b) is sigmoid(a * z + b), so the calibrated model
    is still one weighted sum: coefficients times a, intercept times a, plus b.
    """

    slope, shift = model.calibration.slope, model.calibration.intercept
    log_names = {data.features[i] for i in model.log_columns}
    return {
        "features": [
            {
                "name": name,
                "meaning": FEATURE_MEANINGS.get(name, name),
                "transform": "log1p" if name in log_names else "none",
                "mean": float(model.means[index]),
                "scale": float(model.scales[index]),
                "coefficient": float(slope * model.coef[index]),
            }
            for index, name in enumerate(data.features)
        ],
        "intercept": float(slope * model.intercept + shift),
        "regularisation_C": model.C,
    }


def evaluate(
    data: Dataset,
    split: dict,
    manifest: Manifest,
    grid: Grid,
    *,
    audit: dict,
    inputs: dict,
    out: Path,
) -> dict:
    """Fit, judge, and write evaluation.json, model_card.md and the exported model."""

    if split.get("dataset_sha256") != data.sha256():
        raise ValueError("the split was written for a different dataset; rebuild")
    if split.get("manifest_sha256") != manifest.sha256:
        raise ValueError("the manifest changed after the split was written; rebuild")
    seed = manifest.seed

    rule = RainfallRule.select(data)
    benchmarks: list[Scorer] = [Baseline(), rule, Climatology.fit(data)]
    logistic = Logistic.select(data, seed)
    boosted = Boosted.select(data, seed)
    candidates: list[Scorer] = [logistic, boosted]
    everyone = [*benchmarks, *candidates]
    for scorer in everyone:
        scorer.calibrate(data)

    validate, test = data.mask("validate"), data.mask("test")
    y, days, cells = data.y[test], data.day[test], data.cell[test]
    scores = {s.name: s.score(data, test) for s in everyone}
    probabilities = {s.name: s.probability(data, test) for s in everyone}

    results: dict[str, dict] = {}
    for scorer in everyone:
        threshold = best_f1_threshold(
            data.y[validate], scorer.probability(data, validate)
        )
        results[scorer.name] = {
            "kind": scorer.kind,
            "pr_auc": _round(average_precision(y, scores[scorer.name])),
            "roc_auc": _round(roc_auc(y, scores[scorer.name])),
            "brier": _round(brier(y, probabilities[scorer.name]), 8),
            "calibration": {
                "method": "Platt, fitted on the validation years",
                "slope": _round(scorer.calibration.slope),
                "intercept": _round(scorer.calibration.intercept),
            },
            "operating_point": at_threshold(
                y, probabilities[scorer.name], threshold, cells, days
            ),
        }
    results[rule.name]["feature"] = rule.feature
    results[logistic.name]["setting"] = {"C": logistic.C}
    results[boosted.name]["setting"] = dict(boosted.params)

    bootstrap = day_bootstrap(
        y,
        days,
        scores,
        probabilities,
        pairs=[(c.name, b.name) for c in candidates for b in benchmarks],
        resamples=manifest.gate.bootstrap_resamples,
        interval=manifest.gate.interval,
        seed=seed,
    )
    briers = {name: entry["brier"] for name, entry in results.items()}
    decisions = {
        candidate.name: decide(
            candidate.name,
            [b.name for b in benchmarks],
            bootstrap["pr_auc"],
            briers,
            int(y.sum()),
            manifest.labels.min_test_positives,
        )
        for candidate in candidates
    }

    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    report = {
        "generated_at": now,
        "manifest": {"name": manifest.name, "sha256": manifest.sha256},
        "dataset": {
            "sha256": data.sha256(),
            "rows": {part: int(data.mask(part).sum()) for part in PARTS},
            "positives": {part: int(data.y[data.mask(part)].sum()) for part in PARTS},
            "features": list(data.features),
        },
        "split": split,
        "inputs": inputs,
        "results": results,
        "bootstrap": bootstrap,
        "spatial_check": spatial_check(data, grid, manifest, logistic, boosted, rule),
        "reliability": {
            name: reliability(y, probabilities[name])
            for name in (logistic.name, boosted.name, "climatology")
        },
        "decision": decisions,
        "gate_rule": manifest.gate.rule,
        "software": {
            "numpy": version("numpy"),
            "scikit-learn": version("scikit-learn"),
        },
    }

    out.mkdir(parents=True, exist_ok=True)
    model = {
        "schema_version": MODEL_SCHEMA,
        "name": "ner-landslide-logistic",
        "version": f"lr-{data.sha256()[:12]}",
        "trained": True,
        "unit": "one 0.25 degree cell on one UTC day",
        "target": (
            "probability that the inventory records a rainfall-triggered landslide "
            "in the cell on the day"
        ),
        **export_logistic(logistic, data),
        "operating_threshold": results[logistic.name]["operating_point"]["threshold"],
        "manifest_sha256": manifest.sha256,
        "dataset_sha256": data.sha256(),
        "test_metrics": {
            "pr_auc": results[logistic.name]["pr_auc"],
            "pr_auc_interval": bootstrap["pr_auc"][logistic.name],
            "brier": results[logistic.name]["brier"],
        },
        "decision": decisions[logistic.name],
        "created_at": now,
    }
    model_bytes = (json.dumps(model, indent=2, sort_keys=True) + "\n").encode()
    (out / "model_logistic.json").write_bytes(model_bytes)
    report["exported_model"] = {
        "file": "model_logistic.json",
        "sha256": hashlib.sha256(model_bytes).hexdigest(),
        "version": model["version"],
    }
    (out / "evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "model_card.md").write_text(
        model_card(report, audit, manifest), encoding="utf-8"
    )
    return report


def _interval(values: list[float] | None) -> str:
    return "n/a" if values is None else f"{values[0]:.4f} to {values[1]:.4f}"


def _number(value: float | int | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def model_card(report: dict, audit: dict, manifest: Manifest) -> str:
    decisions = report["decision"]
    passed = [name for name, item in decisions.items() if item["passed"]]
    rows, positives = report["dataset"]["rows"], report["dataset"]["positives"]
    kept = audit["kept"]
    split = report["split"]

    lines = [
        f"# Model card: {manifest.name}",
        "",
        (
            f"**Decision: {', '.join(passed)} passed the gate.**"
            if passed
            else "**Decision: no candidate passed the gate. RASTA keeps baseline-v1.**"
        ),
        "",
        f"Generated {report['generated_at']} from manifest `{manifest.sha256[:12]}` and "
        f"dataset `{report['dataset']['sha256'][:12]}`. Regenerate with "
        "`python -m rasta_ml evaluate`.",
        "",
        "## Question",
        "",
        manifest.question,
        "",
        "## Data",
        "",
        f"- Unit: one {manifest.cell_deg} degree cell (about 25 by 28 km) on one UTC day.",
        f"- Region: {manifest.region.name}, {', '.join(manifest.region.states)}.",
        f"- Period: {manifest.start} to {manifest.end}.",
        f"- Labels: {kept['events']} inventory events kept "
        f"(triggers: {', '.join(sorted(manifest.labels.triggers))}; located within "
        f"{manifest.labels.max_location_error_km:g} km). Set aside: "
        + (
            ", ".join(
                f"{count} {reason}" for reason, count in audit["excluded"].items()
            )
            or "none"
        )
        + ".",
        "- Events by year: "
        + ", ".join(f"{year}: {count}" for year, count in kept["by_year"].items())
        + ".",
        f"- Observed area: {audit['dataset']['cells']} cells with a training-year event. "
        "Events outside it, which no row can score: "
        + (
            ", ".join(
                f"{count} in {part}"
                for part, count in audit["reporting_bias"][
                    "events_outside_the_observed_area"
                ].items()
            )
            or "none"
        )
        + ".",
        "- Rows (positive cell-days): "
        + ", ".join(f"{part} {rows[part]} ({positives[part]})" for part in PARTS)
        + ".",
        "- Features: "
        + "; ".join(
            f"`{name}` {FEATURE_MEANINGS.get(name, name)}"
            for name in report["dataset"]["features"]
        )
        + ".",
        f"- Rainfall: {manifest.sources['rainfall'].title}. Terrain: "
        f"{manifest.sources['terrain'].title}. Events: {manifest.sources['events'].title}.",
        "",
        "## Split",
        "",
        f"Train {split['years']['train'][0]} to {split['years']['train'][1]}, validate "
        f"{split['years']['validate'][0]} to {split['years']['validate'][1]}, test "
        f"{split['years']['test'][0]} to {split['years']['test'][1]}. Written "
        f"{split['written_at']}, before any model was fitted. Settings and calibration "
        "come from the validation years; the test years are used once, here.",
        "",
        "## Results on the test years",
        "",
        f"Intervals are {int(manifest.gate.interval * 100)} percent, from "
        f"{report['bootstrap']['resamples']} resamples of whole days.",
        "",
        "| Model | Kind | PR-AUC | Interval | ROC-AUC | Brier | Precision | Recall | Alerts per cell-week |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, item in report["results"].items():
        point = item["operating_point"]
        lines.append(
            f"| {name} | {item['kind']} | {_number(item['pr_auc'])} | "
            f"{_interval(report['bootstrap']['pr_auc'].get(name))} | "
            f"{_number(item['roc_auc'])} | {_number(item['brier'], 6)} | "
            f"{_number(point['precision'])} | {_number(point['recall'])} | "
            f"{_number(point['alerts_per_cell_per_week'])} |"
        )
    base_rate = positives["test"] / rows["test"] if rows["test"] else 0.0
    lines += [
        "",
        f"A model that guessed would score a PR-AUC near the test base rate, "
        f"{base_rate:.5f}. Operating points use the threshold with the best F1 on the "
        "validation years.",
        "",
        "Spatial check (train on other areas' training years, test on held-out areas' "
        "test years):",
        "",
    ]
    for fold in report["spatial_check"]["folds"]:
        if "pr_auc" not in fold:
            lines.append(f"- Fold {fold['fold']}: {fold.get('note', 'not scored')}.")
            continue
        lines.append(
            f"- Fold {fold['fold']} ({fold['test_positives']} positives): "
            + ", ".join(
                f"{name} {_number(value)}" for name, value in fold["pr_auc"].items()
            )
            + "."
        )
    lines += ["", "## Calibration", ""]
    lines.append(
        "Platt scaling, fitted on the validation years only. Mean predicted probability "
        "against the observed rate, in ten bins of equal size, on the test years:"
    )
    lines.append("")
    for name, table in report["reliability"].items():
        lines.append(
            f"- {name}: "
            + "; ".join(
                f"{row['mean_predicted']:.5f} vs {row['observed_rate']:.5f}"
                for row in table
            )
            + "."
        )
    lines += ["", "## Decision", "", report["gate_rule"], ""]
    for name, item in decisions.items():
        if item["passed"]:
            lines.append(f"- {name}: passed.")
        else:
            lines.append(
                f"- {name}: not passed. "
                + " ".join(f"{r[0].upper()}{r[1:]}." for r in item["reasons"])
            )
    lines += [
        "",
        "## Limits",
        "",
        "- The labels are an inventory of reported landslides. Reports cluster near roads "
        "and towns and follow news coverage, so a quiet cell-day is not proof that "
        "nothing happened. Negatives come only from cells with a training-year report.",
        "- Event locations are uncertain by up to the stated accuracy, and the dates are "
        "as reported; a rainfall day is a UTC day, about five and a half hours off "
        "India's.",
        "- The model is trained on observed satellite rainfall. RASTA's live input today is "
        "a district forecast. Until it reads rainfall of the same kind, serving this "
        "model would feed it inputs it was not trained on.",
        "- Rainfall features include the day itself, so the lead time is that of the "
        "rainfall input, not more.",
        "- Terrain statistics describe the whole cell, not the road; a 0.25 degree cell "
        "also smooths out local cloudbursts.",
        "- The rainfall rule chooses its window on the validation years, so it is a "
        "slightly tuned benchmark, not a naive one.",
        "",
        "## Intended use",
        "",
        "To decide, with evidence, whether a trained model should replace or inform "
        "baseline-v1 in RASTA's road risk score. Not for closing or opening roads, which "
        "only a reviewed field report does, and not a statement about any single slope.",
        "",
    ]
    if report.get("exported_model"):
        exported = report["exported_model"]
        lines += [
            "## Exported model",
            "",
            f"`{exported['file']}`, version `{exported['version']}`, SHA-256 "
            f"`{exported['sha256']}`. The API loads a model only when this hash, the "
            "schema version and a passed decision all check out.",
            "",
        ]
    return "\n".join(lines)


__all__ = [
    "MODEL_SCHEMA",
    "decide",
    "evaluate",
    "export_logistic",
    "model_card",
    "spatial_check",
]
