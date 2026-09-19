"""Isolation Forest anomaly detector over engineered trend features.

The model is trained on NORMAL operation only: it learns the shape of healthy
behaviour, then scores how far a live sample deviates from it. Only engineered
trend features are used (rolling stats, slope, deltas, rates) — raw instantaneous
values are deliberately excluded, since those mostly reproduce a static threshold.
"""

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

FEATURE_SUFFIXES = ("_mean", "_std", "_slope", "_delta", "_rate")


def select_feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.endswith(FEATURE_SUFFIXES)]


def prepare_matrix(df: pd.DataFrame) -> pd.DataFrame:
    X = df[select_feature_columns(df)].copy()
    # Explicit outage signal: app fields are NaN when the app is unreachable,
    # which is itself a strong anomaly the median-imputer would otherwise mask.
    if "app_latency_p95_ms" in df.columns:
        X["app_unreachable"] = df["app_latency_p95_ms"].isna().astype(int)
    else:
        X["app_unreachable"] = 0
    return X


class Detector:
    def __init__(self, contamination: float = 0.01, n_estimators: int = 200,
                 random_state: int = 42) -> None:
        self.contamination = contamination
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.feature_cols: list[str] | None = None
        self.pipeline: Pipeline | None = None

    def fit(self, feats: pd.DataFrame) -> "Detector":
        X = prepare_matrix(feats)
        self.feature_cols = list(X.columns)
        self.pipeline = Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("iforest", IsolationForest(
                n_estimators=self.n_estimators,
                contamination=self.contamination,
                random_state=self.random_state,
            )),
        ])
        self.pipeline.fit(X)
        return self

    def _align(self, feats: pd.DataFrame) -> pd.DataFrame:
        return prepare_matrix(feats).reindex(columns=self.feature_cols, fill_value=0.0)

    def anomaly_score(self, feats: pd.DataFrame):
        """Higher = more anomalous (0 is the decision boundary)."""
        return -self.pipeline.decision_function(self._align(feats))

    def is_anomaly(self, feats: pd.DataFrame):
        return self.pipeline.predict(self._align(feats)) == -1

    def save(self, path: str) -> None:
        joblib.dump(
            {
                "pipeline": self.pipeline,
                "feature_cols": self.feature_cols,
                "params": {
                    "contamination": self.contamination,
                    "n_estimators": self.n_estimators,
                    "random_state": self.random_state,
                },
            },
            path,
        )

    @classmethod
    def load(cls, path: str) -> "Detector":
        blob = joblib.load(path)
        det = cls(**blob["params"])
        det.pipeline = blob["pipeline"]
        det.feature_cols = blob["feature_cols"]
        return det
