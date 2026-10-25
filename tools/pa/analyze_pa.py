#!/usr/bin/env python3
"""Plot a PA_CAL_RUN log using the same analysis the printer runs.

Usage: analyze_pa.py <dir> <name> [--plot out.png]
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pa_analysis


def main():
    directory, name = sys.argv[1], sys.argv[2]
    p, moves, marks = pa_analysis.load(directory, name)
    blocks, dry = pa_analysis.analyse(p, moves, marks)
    if not blocks:
        sys.exit("no PA blocks found")
    res = pa_analysis.recommend(blocks, dry)
    print("   K    balance   err   overshoot  undershoot   snr  fill")
    for row in res["table"]:
        print("%6.3f  %7.3f  %6.3f  %8.3f  %9.3f  %6.0f  %4.2f" % (
            row["k"], row["balance"], row["err"], row["over"], row["under"],
            row["snr"], row["fill"]))
    print("\nBest PA %s (line R2 %s; nearest tested %s; PATuner rule %s)"
          % (res["fitted"], res["r2"], res["best"], res["no_undershoot"]))
    print("dry-wiggle disturbance %s of a step; reliable: %s %s" % (
        res["dry_fraction"], res["reliable"], "; ".join(res["problems"])))
    if "--plot" in sys.argv:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        out = sys.argv[sys.argv.index("--plot") + 1]
        blocks = sorted(blocks, key=lambda b: b["k"])
        cols = 3
        rows = (len(blocks) + cols - 1) // cols
        fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 2.6 * rows),
                                 sharex=True, squeeze=False)
        for ax, b in zip(axes.flat, blocks):
            ax.plot(b["t"], b["model"], color="#888", lw=1, label="commanded")
            ax.plot(b["t"], b["p"], ".", color="#1ab2ff", ms=1.5,
                    label="pressure")
            ax.set_title("PA %.3f  balance %.2f  under %.2f"
                         % (b["k"], b["balance"], b["under"]), fontsize=9)
        for ax in list(axes.flat)[len(blocks):]:
            ax.axis("off")
        axes.flat[0].legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(out, dpi=110)
        print("plot:", out)


if __name__ == "__main__":
    main()
