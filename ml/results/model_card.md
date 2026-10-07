# Model card: ner-landslide-cell-day

**Decision: no candidate passed the gate. RASTA keeps baseline-v1.**

Generated 2026-10-07T19:51:43+00:00 from manifest `05f1f654c811` and dataset `32b719f748cf`. Regenerate with `python -m rasta_ml evaluate`.

## Question

Does a model trained on past landslides and rainfall rank the places and days of rainfall-triggered landslides in North East India better than baseline-v1, a rainfall threshold rule and climatology, on years it never saw?

## Data

- Unit: one 0.25 degree cell (about 25 by 28 km) on one UTC day.
- Region: North East India, Arunachal Pradesh, Assam, Manipur, Meghalaya, Mizoram, Nagaland, Sikkim, Tripura.
- Period: 2007-01-01 to 2017-12-31.
- Labels: 309 inventory events kept (triggers: continuous_rain, downpour, monsoon, rain, tropical_cyclone; located within 25 km). Set aside: 103 another country, 174 another state, 46 located less precisely than 25 km, 11 location accuracy not stated, 39 not triggered by rain, 45 outside the period, 10306 outside the region.
- Events by year: 2007: 13, 2008: 3, 2009: 9, 2010: 37, 2011: 12, 2012: 17, 2013: 17, 2014: 21, 2015: 54, 2016: 33, 2017: 93.
- Observed area: 46 cells with a training-year event. Events outside it, which no row can score: 91 in test, 50 in validate.
- Rows (positive cell-days): train 114770 (104), validate 33580 (16), test 33626 (27).
- Features: `rain_1d` rainfall on the day, mm; `rain_2d` rainfall on the day and the day before, mm; `rain_3d` rainfall over the three days ending on the day, mm; `rain_7d` rainfall over the seven days ending on the day, mm; `rain_30d` rainfall over the thirty days ending on the day, mm; `slope_mean_deg` mean terrain slope in the cell, degrees; `slope_p90_deg` 90th-percentile terrain slope in the cell, degrees; `steep_fraction` share of the cell steeper than 30 degrees; `relief_m` elevation range between the cell's 5th and 95th percentiles, m.
- Rainfall: NOAA Climate Data Record PERSIANN-CDR daily precipitation, v01r01. Terrain: SRTM 1 arc-second elevation, as Tilezen skadi tiles. Events: NASA Global Landslide Catalog export (2007 to 2017).

## Split

Train 2007 to 2013, validate 2014 to 2015, test 2016 to 2017. Written 2026-10-07T19:51:33+00:00, before any model was fitted. Settings and calibration come from the validation years; the test years are used once, here.

## Results on the test years

Intervals are 95 percent, from 1000 resamples of whole days.

| Model | Kind | PR-AUC | Interval | ROC-AUC | Brier | Precision | Recall | Alerts per cell-week |
|---|---|---|---|---|---|---|---|---|
| baseline-v1 | benchmark | 0.0061 | 0.0018 to 0.0283 | 0.7111 | 0.000803 | 0.0007 | 0.9259 | 6.9713 |
| rainfall rule | benchmark | 0.0062 | 0.0015 to 0.0492 | 0.7447 | 0.000801 | 0.0357 | 0.0370 | 0.0058 |
| climatology | benchmark | 0.0019 | 0.0010 to 0.0035 | 0.7315 | 0.000802 | 0.0006 | 0.0370 | 0.3397 |
| logistic regression | candidate | 0.0061 | 0.0022 to 0.0216 | 0.8131 | 0.000801 | 0.0117 | 0.1111 | 0.0533 |
| gradient-boosted trees | candidate | 0.0054 | 0.0019 to 0.0191 | 0.7994 | 0.000802 | 0.0055 | 0.2593 | 0.2642 |

A model that guessed would score a PR-AUC near the test base rate, 0.00080. Operating points use the threshold with the best F1 on the validation years.

Spatial check (train on other areas' training years, test on held-out areas' test years):

- Fold 0 (3 positives): baseline-v1 0.0053, rainfall rule 0.0029, climatology 0.0017, logistic regression 0.0320, gradient-boosted trees 0.0492.
- Fold 1: no positives to train or test on in this fold.
- Fold 2 (11 positives): baseline-v1 0.0093, rainfall rule 0.0342, climatology 0.0018, logistic regression 0.0175, gradient-boosted trees 0.0057.
- Fold 3 (6 positives): baseline-v1 0.0240, rainfall rule 0.0074, climatology 0.0019, logistic regression 0.0165, gradient-boosted trees 0.0077.
- Fold 4 (7 positives): baseline-v1 0.0036, rainfall rule 0.0040, climatology 0.0041, logistic regression 0.0048, gradient-boosted trees 0.0040.

## Calibration

Platt scaling, fitted on the validation years only. Mean predicted probability against the observed rate, in ten bins of equal size, on the test years:

- logistic regression: 0.00000 vs 0.00000; 0.00000 vs 0.00000; 0.00001 vs 0.00000; 0.00003 vs 0.00059; 0.00010 vs 0.00030; 0.00024 vs 0.00030; 0.00044 vs 0.00030; 0.00072 vs 0.00119; 0.00120 vs 0.00119; 0.00288 vs 0.00416.
- gradient-boosted trees: 0.00001 vs 0.00030; 0.00001 vs 0.00000; 0.00001 vs 0.00000; 0.00003 vs 0.00030; 0.00010 vs 0.00030; 0.00020 vs 0.00030; 0.00035 vs 0.00030; 0.00059 vs 0.00149; 0.00107 vs 0.00119; 0.00407 vs 0.00387.
- climatology: 0.00007 vs 0.00000; 0.00007 vs 0.00000; 0.00012 vs 0.00000; 0.00018 vs 0.00030; 0.00029 vs 0.00059; 0.00039 vs 0.00119; 0.00045 vs 0.00030; 0.00055 vs 0.00238; 0.00105 vs 0.00297; 0.00166 vs 0.00030.

## Decision

A candidate passes only if its test PR-AUC interval lies wholly above every benchmark's interval and its test Brier score is below climatology's.

- logistic regression: not passed. Its PR-AUC interval [0.002176, 0.021628] does not lie above baseline-v1's [0.001813, 0.028281]. Its PR-AUC interval [0.002176, 0.021628] does not lie above rainfall rule's [0.001478, 0.049221]. Its PR-AUC interval [0.002176, 0.021628] does not lie above climatology's [0.000987, 0.003461].
- gradient-boosted trees: not passed. Its PR-AUC interval [0.00191, 0.019073] does not lie above baseline-v1's [0.001813, 0.028281]. Its PR-AUC interval [0.00191, 0.019073] does not lie above rainfall rule's [0.001478, 0.049221]. Its PR-AUC interval [0.00191, 0.019073] does not lie above climatology's [0.000987, 0.003461].

## Limits

- The labels are an inventory of reported landslides. Reports cluster near roads and towns and follow news coverage, so a quiet cell-day is not proof that nothing happened. Negatives come only from cells with a training-year report.
- Event locations are uncertain by up to the stated accuracy, and the dates are as reported; a rainfall day is a UTC day, about five and a half hours off India's.
- The model is trained on observed satellite rainfall. RASTA's live input today is a district forecast. Until it reads rainfall of the same kind, serving this model would feed it inputs it was not trained on.
- Rainfall features include the day itself, so the lead time is that of the rainfall input, not more.
- Terrain statistics describe the whole cell, not the road; a 0.25 degree cell also smooths out local cloudbursts.
- The rainfall rule chooses its window on the validation years, so it is a slightly tuned benchmark, not a naive one.

## Intended use

To decide, with evidence, whether a trained model should replace or inform baseline-v1 in RASTA's road risk score. Not for closing or opening roads, which only a reviewed field report does, and not a statement about any single slope.

## Exported model

`model_logistic.json`, version `lr-32b719f748cf`, SHA-256 `85d0836f9d1026044c7b1f0c39e7d8dc1262a741f65294c0099c676232d22fd0`. The API loads a model only when this hash, the schema version and a passed decision all check out.
