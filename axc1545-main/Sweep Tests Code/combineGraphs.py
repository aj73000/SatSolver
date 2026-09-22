from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ============================================================
# USER CONFIGURATION
# ============================================================

# List every summary CSV you want to combine.
# These are the files named parameter_sweep_<policy>_summary.csv
# produced by parameter_sweep.py.

#geometric
#glucose
#fixed
#luby
policy = "fixed"


CSV_FILES = [
    fr"./Test Data/uf250-1065/ai/hoos/Shortcuts/UF250.1065.100/analysis_output/parameter_sweep_{policy}_summary.csv"
]


# Y-axis metric.
# "seconds"   -> use median_seconds, q25_seconds, q75_seconds columns
# "conflicts" -> use median_conflicts column (no IQR band for conflicts)
Y_METRIC = "conflicts"

# Label shown on the x-axis (describes what parameter was varied).
# Copied from the parameter sweep — change to match your policy.
X_LABEL = "Base interval / unit u (conflicts)"

# Title for the graph.
TITLE = f"Parameter sweep — {policy} policy"

# Where to save the output.
OUTPUT_PATH = fr"./analysis_output/combined_{policy}_{Y_METRIC}_sweep.png"

# If two CSVs share the same param_value, keep: "first" or "mean".
ON_DUPLICATE = "first"


# ============================================================
# LOAD AND MERGE
# ============================================================

def load_and_merge(csv_files, on_duplicate):

    frames = []

    for path in csv_files:
        p = Path(path)
        if not p.exists():
            print(f"  Warning: file not found — {p}")
            continue
        df = pd.read_csv(p)
        print(f"  Loaded {p.name}  ({len(df)} rows)")
        frames.append(df)

    if not frames:
        raise RuntimeError("No CSV files could be loaded.")

    combined = pd.concat(frames, ignore_index=True)

    # Convert param_value to numeric and sort.
    combined["param_value"] = pd.to_numeric(combined["param_value"], errors="coerce")
    combined = combined.dropna(subset=["param_value"])
    combined = combined.sort_values("param_value").reset_index(drop=True)

    # Handle duplicate param values.
    if combined["param_value"].duplicated().any():
        dupes = combined["param_value"][combined["param_value"].duplicated()].unique()
        print(f"  Duplicate param values found: {list(dupes)}")

        if on_duplicate == "mean":
            combined = combined.groupby("param_value", as_index=False).mean(numeric_only=True)
            print("  -> averaged duplicates")
        else:
            combined = combined.drop_duplicates(subset=["param_value"], keep="first")
            print("  -> kept first occurrence of each duplicate")

    print(f"  Combined: {len(combined)} unique parameter values")
    return combined


# ============================================================
# PLOT
# ============================================================

def plot(df, output_path):

    if Y_METRIC == "seconds":
        y    = df["median_seconds"].values
        q25  = df["q25_seconds"].values  if "q25_seconds"  in df.columns else None
        q75  = df["q75_seconds"].values  if "q75_seconds"  in df.columns else None
        y_lbl = "Median solve time (seconds)"
    else:
        y    = df["median_conflicts"].values
        q25  = None
        q75  = None
        y_lbl = "Median conflicts"

    x = df["param_value"].values

    best_idx = int(np.argmin(y))
    best_x   = x[best_idx]
    best_y   = y[best_idx]

    fig, ax = plt.subplots(figsize=(12, 6))

    # IQR band.
    if q25 is not None and q75 is not None:
        ax.fill_between(x, q25, q75, alpha=0.2, label="IQR (Q1–Q3)")

    # Main line.
    ax.plot(x, y, marker="o", linewidth=2, markersize=6, label=f"Median {Y_METRIC}")

    # Best point.
    ax.plot(
        best_x, best_y,
        marker="*", markersize=18, color="gold", zorder=5,
        label=f"Best: {best_x}  →  {best_y:.2f}",
    )
    ax.axvline(best_x, color="gold", linestyle="--", linewidth=1, alpha=0.6)

    # Annotate each data point.
    for xi, yi in zip(x, y):
        ax.annotate(
            f"{yi:.2f}",
            (xi, yi),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            fontsize=8,
        )

    n = int(df["n_solved"].iloc[0]) if "n_solved" in df.columns else "?"

    ax.set_xlabel(X_LABEL, fontsize=12)
    ax.set_ylabel(y_lbl, fontsize=12)
    ax.set_title(f"{TITLE}\n{X_LABEL}  (n={n} instances per point)", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()

    print(f"\n  Saved: {out}")
    print(f"  Best : {X_LABEL} = {best_x}  ({best_y:.2f} {Y_METRIC})")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("COMBINING SWEEP CSVS")
    print("=" * 60)

    df = load_and_merge(CSV_FILES, ON_DUPLICATE)

    print()
    print("=" * 60)
    print("PLOTTING")
    print("=" * 60)

    plot(df, OUTPUT_PATH)

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()