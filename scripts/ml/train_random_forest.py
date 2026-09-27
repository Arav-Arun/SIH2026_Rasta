import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

MODEL_PATH = "models/landslide_risk_rf_v1.pkl"


def generate_government_synthetic_dataset(num_samples: int = 5000) -> pd.DataFrame:
    """
    Simulates a historical dataset aligned with:
    1. IMD Rainfall API (forecast_rainfall: 0.0 - 1.0)
    2. GSI / ISRO Bhuvan Landslide Atlas (slope_susceptibility: 0.0 - 1.0)
    3. NDMA/Local Alerts (official_warning: 0.0 - 1.0)
    4. Historical Incident Log (recent_incidents: 0.0 - 1.0)
    5. Isolation Forest Output (speed_anomaly: 0.0 - 1.0)
    """
    print(
        "Generating synthetic historical dataset based on IMD, GSI, and ISRO parameters..."
    )
    np.random.seed(42)

    rainfall = np.random.uniform(0.0, 1.0, num_samples)
    warning = np.random.uniform(0.0, 1.0, num_samples)
    slope = np.random.uniform(0.0, 1.0, num_samples)
    incidents = np.random.uniform(0.0, 1.0, num_samples)
    anomaly = np.random.uniform(0.0, 1.0, num_samples)

    # Nonlinear interaction logic representing real-world landslide triggers:
    # High rainfall on steep slope + speed anomaly heavily indicates active hazard.
    risk_indicator = (
        0.35 * rainfall
        + 0.25 * warning
        + 0.20 * slope
        + 0.10 * incidents
        + 0.10 * anomaly
        + 0.20
        * (
            rainfall * slope
        )  # Interaction term: rain on steep terrain is exponential risk
    )

    # Target class: 0 = Low, 1 = Moderate, 2 = High Risk
    target = []
    for r in risk_indicator:
        if r >= 0.65:
            target.append(2)  # High
        elif r >= 0.35:
            target.append(1)  # Moderate
        else:
            target.append(0)  # Low

    df = pd.DataFrame(
        {
            "forecast_rainfall": rainfall,
            "official_warning": warning,
            "slope_susceptibility": slope,
            "recent_incidents": incidents,
            "speed_anomaly": anomaly,
            "risk_level_class": target,
        }
    )

    return df


def train_and_eval():
    df = generate_government_synthetic_dataset()

    X = df[
        [
            "forecast_rainfall",
            "official_warning",
            "slope_susceptibility",
            "recent_incidents",
            "speed_anomaly",
        ]
    ]
    y = df["risk_level_class"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    print("Training Random Forest Classifier (100 estimators)...")
    clf = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
    clf.fit(X_train, y_train)

    # Validation
    y_pred = clf.predict(X_test)
    print("\nModel Performance Evaluation:")
    print(
        classification_report(y_test, y_pred, target_names=["Low", "Moderate", "High"])
    )

    # Save artifact. Nothing in the product loads it (the label audit rejected it);
    # models/ is gitignored so a re-run cannot put it back into the repository.
    print(f"Saving model to {MODEL_PATH}...")
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(clf, MODEL_PATH)
    print("Random Forest model successfully trained and saved!")


if __name__ == "__main__":
    train_and_eval()
