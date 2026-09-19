import argparse
import os

from agent.features import build_features

from .model import Detector


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train the Isolation Forest detector on NORMAL-only metrics"
    )
    parser.add_argument("--normal", required=True,
                        help="raw metrics JSONL captured during normal operation")
    parser.add_argument("--out", default="models/detector.joblib")
    parser.add_argument("--contamination", type=float, default=0.01)
    parser.add_argument("--window", type=int, default=5)
    parser.add_argument("--delta", type=int, default=3)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    feats = build_features(args.normal, window=args.window, delta_horizon=args.delta)
    detector = Detector(
        contamination=args.contamination,
        n_estimators=args.n_estimators,
        random_state=args.random_state,
    ).fit(feats)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    detector.save(args.out)
    print(f"trained on {len(feats)} feature rows ({len(detector.feature_cols)} features) -> {args.out}")


if __name__ == "__main__":
    main()
