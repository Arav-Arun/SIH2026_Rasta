# Landslide model: dataset, training and evaluation

RASTA's road risk score today is `baseline-v1`, a documented weighting of forecast
rainfall, official warnings, terrain slope and confirmed incidents
(`api/app/risk_engine.py`). Its weights were not fitted to outcomes.

This folder is the pipeline that decides, with held-out evidence, whether a trained model
should replace or inform it. It never ships a model by itself: a model is used only if it
passes the gate below on years it never saw, and the API refuses any model file whose
hash, schema or decision does not check out.

## What it does

1. **Fetch.** Reads a landslide inventory and downloads, for the cells it needs:
   - daily rainfall from NOAA's PERSIANN-CDR climate record (0.25 degree, 1983 onwards,
     public on AWS, no account needed);
   - terrain from SRTM 1 arc-second elevation (the same tiles as the pilot's terrain
     slope).

   Every file is hashed into `work/inputs.lock.json`.
2. **Build.** One row per 0.25 degree cell per day in North East India, 2007 to 2017.
   - Features: rainfall over the last 1, 2, 3, 7 and 30 days; slope mean and 90th
     percentile; steep share; relief.
   - Label: whether the inventory records a rainfall-triggered landslide in that cell
     that day.
   - Rows come only from cells with a training-year report, so quiet days in places
     nobody reports from are not used as negatives.

   Writes the label audit (counts by year, state, source, accuracy, trigger and what was
   set aside) and the split, before anything is fitted.
3. **Evaluate.**
   - Candidates: logistic regression and gradient-boosted trees, fitted on 2007 to 2013,
     tuned and calibrated on 2014 to 2015, and judged once on 2016 to 2017.
   - Benchmarks they must beat: `baseline-v1` (on the inputs that exist for past days), a
     rainfall threshold rule, and climatology.
   - Measured: PR-AUC with day-block bootstrap intervals, Brier score, reliability,
     precision, recall and alert burden at an operating point, and a spatial-block check.
   - Writes `work/report/evaluation.json`, `work/report/model_card.md` and the exported
     model.

Region, period, label rules, split years, seed and the gate are all in
[`manifest.json`](manifest.json); every output records its SHA-256.

## The gate

A candidate passes only if:
- its test PR-AUC interval lies wholly above every benchmark's;
- its Brier score is below climatology's;
- the test years hold enough events to judge (20 positive cell-days by default).

A candidate that fails keeps `baseline-v1` in place, and the card says why. A negative
result is a result.

## Run it

```bash
python3.12 -m venv ml/.venv
ml/.venv/bin/pip install -e './ml[dev]'

# 1. The inventory: the NASA Global Landslide Catalog export (its link and terms are in
#    manifest.json), or an authority's CSV with date, latitude and longitude columns once
#    its terms are recorded there. ml/work/ is not committed.
mkdir -p ml/work && curl -L -o ml/work/events.csv \
  https://data.nasa.gov/docs/legacy/Global_Landslide_Catalog_Export/Global_Landslide_Catalog_Export_rows.csv
# 2. Everything else:
ml/.venv/bin/python -m rasta_ml all
```

`fetch` downloads about 1.2 MB of rainfall per day of the period (about 5 GB for
2007 to 2017, keeping only the region's cells) and one elevation tile per 1 degree square
with events. Runs resume from `ml/work/cache`.

## Tests

```bash
cd ml && .venv/bin/python -m pytest
```

The tests build small worlds from **synthetic** data to check the mechanics: parsing, the
split, the metrics, calibration, the gate and the export. Their numbers are not results
and must never be quoted as such.

## First run

[`results/`](results/) holds the first run on real data: the model card, the evaluation,
the label audit, the split and the hashes of every input. It used the NASA Global
Landslide Catalog (682 events in the region, 309 kept after the label rules), PERSIANN-CDR
rainfall and SRTM terrain.

**Decision: neither candidate passed the gate, so RASTA keeps `baseline-v1`.** On
2016 to 2017, logistic regression ranked better overall (ROC-AUC 0.81 against
`baseline-v1`'s 0.71), but the test years hold only 27 landslide cell-days and its PR-AUC
interval overlaps every benchmark's. A new inventory with more events, or official
closure records, is what could change this.

## Limits

- RASTA's live rainfall input is a district forecast, not this satellite record. A model
  that passes would still need the same kind of rainfall in the API before it could be
  served (see the card's limits).
