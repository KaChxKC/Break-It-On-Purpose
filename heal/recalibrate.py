"""Recalibrate the detector on cloud-collected NORMAL data.

A model trained on laptop metrics doesn't transfer cleanly to EC2 — the baseline
CPU, latency and noise floor differ — so before the loop goes live we retrain and
re-threshold on normal data captured on the real instances.
"""

import argparse

from agent.features import build_features
from model.model import Detector


def recalibrate(normal_raw_path: str, out_model_path: str,
                contamination: float = 0.02, window: int = 5,
                delta_horizon: int = 3, random_state: int = 42) -> Detector:
    feats = build_features(normal_raw_path, window=window, delta_horizon=delta_horizon)
    detector = Detector(contamination=contamination, random_state=random_state).fit(feats)
    detector.save(out_model_path)
    return detector


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recalibrate the detector on cloud-collected normal metrics")
    parser.add_argument("--normal", required=True, help="raw normal-run JSONL from the cloud")
    parser.add_argument("--out", default="models/detector.joblib")
    parser.add_argument("--contamination", type=float, default=0.02)
    args = parser.parse_args()
    det = recalibrate(args.normal, args.out, contamination=args.contamination)
    print(f"recalibrated on {len(det.feature_cols)} features -> {args.out}")


if __name__ == "__main__":
    main()
