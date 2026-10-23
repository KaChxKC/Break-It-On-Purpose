import matplotlib

matplotlib.use("Agg")  # headless: render to files, never a window
import matplotlib.pyplot as plt  # noqa: E402

_COMPARISONS = [
    ("model_over_highres", "model vs high-res"),
    ("highres_over_cloudwatch", "high-res vs CloudWatch"),
    ("model_over_cloudwatch", "model vs CloudWatch"),
]


def lead_time_bar(summary: dict, path: str) -> str:
    labels, means, lo_err, hi_err = [], [], [], []
    for key, label in _COMPARISONS:
        s = summary.get(key, {})
        mean = s.get("mean")
        if mean is None:
            continue
        labels.append(label)
        means.append(mean)
        lo_err.append(mean - s["ci_low"])
        hi_err.append(s["ci_high"] - mean)

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, means, yerr=[lo_err, hi_err], capsize=5, color="#1f618d")
    ax.axhline(0, color="#999", linewidth=0.8)
    ax.set_ylabel("lead time (s)")
    ax.set_title("Early-warning lead time (mean ± 95% CI)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def detection_timeline(raw, epochs: dict, onset_epoch: float, metric: str,
                       path: str) -> str:
    t0 = raw["ts_epoch"].min()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(raw["ts_epoch"] - t0, raw[metric], color="#333", linewidth=1, label=metric)
    ax.axvline(onset_epoch - t0, color="#c0392b", linestyle="--", label="fault onset")
    colors = {"cloudwatch": "#e67e22", "highres": "#8e44ad", "model": "#27ae60"}
    for arm, epoch in epochs.items():
        if epoch is not None:
            ax.axvline(epoch - t0, color=colors.get(arm, "#555"), linestyle=":", label=arm)
    ax.set_xlabel("seconds since run start")
    ax.set_ylabel(metric)
    ax.set_title("Detection timeline")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
