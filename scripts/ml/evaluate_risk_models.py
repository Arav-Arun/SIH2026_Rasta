"""Decide whether a supervised risk model can be justified, and record why."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "api"))

CARD_PATH = REPOSITORY_ROOT / "artifacts" / "models" / "baseline_vs_supervised.md"
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "models" / "label_audit.json"


def database_url() -> str | None:
    try:
        out = subprocess.check_output(
            ["npx", "supabase", "status", "-o", "json"],
            cwd=REPOSITORY_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return json.loads(out).get("DB_URL")
    except Exception:
        return None


def count_labels(url: str | None) -> dict[str, Any]:
    """How many examples of an actual outcome the database holds."""

    if not url:
        return {"database_reachable": False}
    import psycopg

    queries = {
        "confirmed_incidents": "select count(*) from public.incidents where status = 'confirmed'",
        "rejected_incidents": "select count(*) from public.incidents where status = 'rejected'",
        "reviewed_inspections": "select count(*) from public.inspections where status = 'reviewed'",
        "segments_ever_closed": (
            "select count(*) from public.network_observations where passability = 'closed'"
        ),
        "segments_total": "select count(*) from public.road_segments",
        "observation_days": (
            "select coalesce(extract(day from (max(observed_at) - min(observed_at))), 0)::int "
            "from public.network_observations"
        ),
    }
    counts: dict[str, Any] = {"database_reachable": True}
    with psycopg.connect(url) as connection:
        for key, sql in queries.items():
            counts[key] = connection.execute(sql).fetchone()[0]
    return counts


def audit_supervised_model() -> dict[str, Any]:
    """Show what the existing model's reported accuracy is measuring."""

    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import train_test_split

    sys.path.insert(0, str(REPOSITORY_ROOT))
    from scripts.ml.train_random_forest import generate_government_synthetic_dataset

    frame = generate_government_synthetic_dataset(5000)
    features = [
        "forecast_rainfall",
        "official_warning",
        "slope_susceptibility",
        "recent_incidents",
        "speed_anomaly",
    ]
    x = frame[features].to_numpy()
    y = frame["risk_level_class"].to_numpy()
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.2, random_state=42, stratify=y
    )
    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(x_train, y_train)
    in_rule = {
        "accuracy": round(float(accuracy_score(y_test, model.predict(x_test))), 4),
        "macro_f1": round(
            float(f1_score(y_test, model.predict(x_test), average="macro")), 4
        ),
    }

    # The same features, labelled by a *different* plausible rule: rainfall on a steep
    # slope matters far more, and the official warning barely at all.
    rng = np.random.default_rng(7)
    alt_raw = (
        0.15 * x[:, 0]
        + 0.05 * x[:, 1]
        + 0.30 * x[:, 2]
        + 0.20 * x[:, 3]
        + 0.10 * x[:, 4]
        + 0.40 * (x[:, 0] * x[:, 2])
        + rng.normal(0, 0.03, len(x))
    )
    alt_y = np.where(alt_raw >= 0.65, 2, np.where(alt_raw >= 0.35, 1, 0))
    out_of_rule = {
        "accuracy": round(float(accuracy_score(alt_y, model.predict(x))), 4),
        "macro_f1": round(float(f1_score(alt_y, model.predict(x), average="macro")), 4),
    }

    # And the explainable baseline, scored on the same two label sets.
    from app.risk_engine import WEIGHTS

    # The engine calls the same input `telemetry_anomaly`; the training script
    # calls it `speed_anomaly`. Mapped explicitly rather than assumed equal.
    engine_names = {
        "forecast_rainfall": "forecast_rainfall",
        "official_warning": "official_warning",
        "slope_susceptibility": "slope_susceptibility",
        "recent_incidents": "recent_incidents",
        "speed_anomaly": "telemetry_anomaly",
    }
    weights = np.array([WEIGHTS[engine_names[name]] for name in features])
    baseline_raw = x @ weights
    baseline_pred = np.where(
        baseline_raw >= 0.70, 2, np.where(baseline_raw >= 0.30, 1, 0)
    )
    baseline = {
        "against_generator_rule": {
            "accuracy": round(float(accuracy_score(y, baseline_pred)), 4),
            "macro_f1": round(float(f1_score(y, baseline_pred, average="macro")), 4),
        },
        "against_alternative_rule": {
            "accuracy": round(float(accuracy_score(alt_y, baseline_pred)), 4),
            "macro_f1": round(
                float(f1_score(alt_y, baseline_pred, average="macro")), 4
            ),
        },
    }

    return {
        "supervised_against_its_own_generator": in_rule,
        "supervised_against_an_alternative_rule": out_of_rule,
        "baseline_v1": baseline,
        "labels_are_synthetic": True,
        "leakage": (
            "The training labels are a deterministic function of the training "
            "features, so the model is fitted to an arithmetic rule and the "
            "held-out split contains no information the rule does not."
        ),
    }


def write_card(labels: dict[str, Any], audit: dict[str, Any], decision: str) -> None:
    generated = datetime.now(tz=UTC).isoformat()
    CARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    CARD_PATH.write_text(
        f"""# Risk model card: baseline-v1, and why no supervised model ships

Generated: {generated}
Decision: **{decision}**

## What ships

`baseline-v1` in `services/api/app/risk_engine.py`: a documented weighting over
five normalised features, which reports every feature's contribution, lists the
inputs it did not have, drops inputs that are out of date, and refuses to
produce a number at all when there is no weather evidence. It caps its own
verdict at *moderate* when fewer than 60% of its inputs are available.

It is **not** a trained model and does not claim to be. `model_versions`
records it with `trained: false` and empty metrics, because publishing accuracy
figures for a weighting nobody fitted would be an invented result.

## Why no supervised model ships

### 1. There are no observed-outcome labels

Counted from the pilot database:

```json
{json.dumps(labels, indent=2)}
```

A supervised model of road disruption needs examples of roads that were
disrupted and roads that were not, over a period long enough to contain both.
The pilot has an imported road graph and whatever incidents this environment has
recorded, not a labelled history. No time split is possible over a dataset with
no time span.

### 2. The existing model's reported accuracy measures a formula, not a road

`scripts/ml/train_random_forest.py` generates its own labels with an arithmetic
expression over the same five features it then trains on. The classifier
therefore recovers that expression, and the held-out split contains no
information the expression does not.

```json
{json.dumps(audit, indent=2)}
```

Read the two supervised rows together. Against the rule that produced its
labels the model looks excellent. Against a different but equally plausible rule
over the *same* features it collapses. That is the signature of a model that has
learned a formula rather than a phenomenon, and it is the reason the figures
once reported for it (precision 0.92, recall 0.93) must not be presented as
evidence about landslides. They are evidence about arithmetic.

### 3. The comparison cannot be won, because both sides are scored on invented labels

The learned model scores higher than the weighting against both rules above.
That is not a reason to ship it, and stating it as one would be the mistake this
card exists to avoid: **both figures are accuracy against labels somebody made
up.** A model that fits a formula better than a hand-set weighting does has
demonstrated arithmetic, not skill at anticipating a landslide. There is no
measurement here that either approach can win.

With no way to measure which is better, the decision falls to what each can
account for. The weighting can tell a dispatcher that forecast rainfall
contributed 0.26 of a 0.41 score, that slope susceptibility was missing, and
that a stale warning was ignored. The classifier can offer a class probability
and nothing else. On an unmeasurable tie, the one that can be argued with wins.

## Where the experimental models went

Nothing serves them. The API's experimental scoring endpoints were removed:
no screen called them, and no deployment carried a model file for them to
load. `scripts/ml/train_random_forest.py` stays, because this audit re-trains
its model to show what the model's accuracy measures.

## What would change this decision

1. A labelled record of closures and their preceding conditions covering at
   least one full monsoon, with the date of each observation.
2. A slope-susceptibility layer for the pilot district, which the baseline
   currently reports as a missing input on every segment.
3. Telemetry from enough trips that a speed anomaly is a measurement rather
   than a guess.

With those, the comparison is worth re-running: `python scripts/ml/evaluate_risk_models.py`.
""",
        encoding="utf-8",
    )


def main() -> int:
    labels = count_labels(database_url())
    try:
        audit = audit_supervised_model()
    except ImportError as error:
        audit = {"error": f"scikit-learn or numpy unavailable: {type(error).__name__}"}

    has_real_labels = bool(
        labels.get("database_reachable")
        and labels.get("confirmed_incidents", 0) >= 200
        and labels.get("observation_days", 0) >= 90
    )
    decision = (
        "supervised model accepted (re-run evaluation)"
        if has_real_labels
        else "supervised model rejected in favour of baseline-v1"
    )

    write_card(labels, audit, decision)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(tz=UTC).isoformat(),
                "decision": decision,
                "labels_available": has_real_labels,
                "label_counts": labels,
                "audit": audit,
                "shipped_model": "baseline-v1",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Decision: {decision}")
    print(f"Model card → {CARD_PATH}")
    print(f"Label audit → {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
