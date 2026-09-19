import argparse
import os

from .collector import Collector, run


def main() -> None:
    parser = argparse.ArgumentParser(description="Break It On Purpose metric agent")
    # 127.0.0.1, not localhost: on some hosts localhost resolves to IPv6 (::1)
    # first and stalls ~2s per scrape before falling back to IPv4, which would
    # dominate the sampling interval.
    parser.add_argument("--app-url", default="http://127.0.0.1:8000/metrics",
                        help="app /metrics endpoint to scrape")
    parser.add_argument("--out", default="data/raw/metrics.jsonl",
                        help="JSONL output path (appended)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="sampling interval in seconds")
    parser.add_argument("--duration", type=float, default=None,
                        help="stop after this many seconds")
    parser.add_argument("--max-samples", type=int, default=None,
                        help="stop after this many samples")
    args = parser.parse_args()

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    collector = Collector(args.app_url)
    print(f"sampling every {args.interval}s from {args.app_url} -> {args.out}")
    written = run(
        collector,
        args.out,
        args.interval,
        max_samples=args.max_samples,
        duration_sec=args.duration,
    )
    print(f"wrote {written} samples")


if __name__ == "__main__":
    main()
