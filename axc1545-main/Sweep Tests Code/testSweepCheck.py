import statistics
import subprocess
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ============================================================
# USER CONFIGURATION
# ============================================================

# Path to the compiled solver executable.
SOLVER_EXE = r"/home/arthur/Documents/Cpp/SATSolver/cmake-build-debug/SATSolver"

# Folder containing .cnf instance files.
# All .cnf files found recursively will be used.
CNF_FOLDER = r"./Test Data/uf250-1065/ai/hoos/Shortcuts/UF250.1065.100"

# Restart policy whose parameter you want to sweep.
# Options: "fixed", "geometric", "luby", "glucose"
#   fixed     -> varies base interval (conflicts before restart)
#   geometric -> varies base interval by default; set GEOMETRIC_TARGET
#                to "factor" to vary the growth factor instead
#   luby      -> varies base interval (unit u in the Luby sequence)
#   glucose   -> varies the K threshold (restart when fast LBD > K * slow LBD)
POLICY = "luby"

# For the geometric policy only: which parameter to vary.
# "interval" varies --restart-unit; "factor" varies --geo-factor.
GEOMETRIC_TARGET = "interval"

# Range and number of evenly-spaced test points.
# Example: RANGE_MIN=10, RANGE_MAX=500, N_STEPS=10
#   -> tests at: 10, 64, 118, ..., 500  (10 points)
RANGE_MIN = 10
RANGE_MAX = 2000
N_STEPS   = 15

# All other solver settings are held constant during the sweep.
PHASE_SAVING    = True
MINIMIZE_LEARNT = True
REDUCE_DB       = True
VAR_DECAY       = 0.95
BLOCK_RESTARTS  = False

# Fixed values used when they are NOT the parameter being swept.
FIXED_RESTART_UNIT = 100
FIXED_GEO_FACTOR   = 1.5
FIXED_GLUCOSE_K    = 0.8

# Per-file wall-clock timeout (seconds).
# Runs that exceed this are recorded as unsolved and excluded from medians.
TIMEOUT_SECONDS = 300

# Y-axis metric for the line graph.
# "seconds"   -> solve time  (primary recommendation)
# "conflicts" -> conflict count (hardware-independent)
Y_METRIC = "seconds"


# ============================================================
# PARAMETER MAP
# ============================================================

# Maps each policy to: (cli_flag, human_label, value_type)
_POLICY_PARAM = {
    "none":      None,
    "fixed":     ("--restart-unit", "Base interval (conflicts)",   int),
    "luby":      ("--restart-unit", "Base interval / unit u (conflicts)", int),
    "geometric": {
        "interval": ("--restart-unit", "Base interval (conflicts)",    int),
        "factor":   ("--geo-factor",   "Geometric growth factor",      float),
    },
    "glucose":   ("--glucose-k",   "Glucose K threshold",         float),
}


def resolve_param(policy, geo_target):
    """Return (cli_flag, label, type) for the chosen policy."""

    entry = _POLICY_PARAM.get(policy)

    if entry is None:
        raise ValueError(
            f"Policy '{policy}' has no sweepable parameter. "
            "Choose fixed, geometric, luby, or glucose."
        )

    if policy == "geometric":
        return entry[geo_target]

    return entry


# ============================================================
# SOLVER INVOCATION
# ============================================================

def build_args(policy, swept_flag, swept_value):
    """Build the solver argument list for one run."""

    args = [
        f"--restart={policy}",
        f"{swept_flag}={swept_value}",
        "--csv",
    ]

    # Fixed parameters.
    if swept_flag != "--restart-unit":
        args.append(f"--restart-unit={FIXED_RESTART_UNIT}")
    if swept_flag != "--geo-factor":
        args.append(f"--geo-factor={FIXED_GEO_FACTOR}")
    if swept_flag != "--glucose-k":
        args.append(f"--glucose-k={FIXED_GLUCOSE_K}")

    args.append(f"--var-decay={VAR_DECAY}")
    if not PHASE_SAVING:
        args.append("--no-phase-saving")
    if not MINIMIZE_LEARNT:
        args.append("--no-minimize")
    if not REDUCE_DB:
        args.append("--no-reduce")
    if BLOCK_RESTARTS:
        args.append("--block-restarts")

    return args


def run_one(cnf_path, policy, swept_flag, swept_value):
    """
    Run the solver on a single .cnf file with the given parameter value.

    Returns a dict of CSV column -> value on success, or None on timeout
    or solver error.
    """

    cmd = [SOLVER_EXE, str(cnf_path)] + build_args(policy, swept_flag, swept_value)

    try:

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )

        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]

        if len(lines) < 2:
            return None

        header = lines[0].split(",")
        values = lines[1].split(",")

        if len(header) != len(values):
            return None

        row = dict(zip(header, values))
        row["_cnf"] = cnf_path.name
        return row

    except subprocess.TimeoutExpired:
        return None

    except Exception:
        return None


# ============================================================
# SWEEP
# ============================================================

def run_sweep(cnf_files, policy, swept_flag, swept_values):
    """
    Run the solver for every (parameter_value, cnf_file) combination.

    Returns a list of summary dicts, one per parameter value.
    """

    print("=" * 60)
    print("PARAMETER SWEEP")
    print("=" * 60)
    print(f"  Policy      : {policy}")
    print(f"  Parameter   : {swept_flag}")
    print(f"  Values      : {[round(v, 4) for v in swept_values]}")
    print(f"  Instances   : {len(cnf_files)}")
    print(f"  Total runs  : {len(swept_values) * len(cnf_files)}")
    print()

    all_rows = []
    summaries = []

    for idx, value in enumerate(swept_values):

        # Format the display value neatly.
        display = int(value) if swept_flag == "--restart-unit" else round(value, 4)

        print(f"  [{idx + 1:>2}/{len(swept_values)}]  {swept_flag}={display}")

        seconds_list   = []
        conflicts_list = []
        n_timeout      = 0

        for cnf in cnf_files:

            row = run_one(cnf, policy, swept_flag, value)

            if row is None:
                n_timeout += 1
                continue

            try:
                seconds_list.append(float(row["seconds"]))
                conflicts_list.append(int(row["conflicts"]))
                row["_param_value"] = display
                all_rows.append(row)
            except (KeyError, ValueError):
                n_timeout += 1

        if not seconds_list:
            print(f"         No results (all timed out).")
            continue

        med_s = statistics.median(seconds_list)
        med_c = statistics.median(conflicts_list)

        print(
            f"         solved={len(seconds_list)}/{len(cnf_files)}  "
            f"median_s={med_s:.4f}  median_conflicts={int(med_c)}"
        )

        summaries.append({
            "param_value":       display,
            "n_solved":          len(seconds_list),
            "n_total":           len(cnf_files),
            "median_seconds":    round(med_s, 6),
            "mean_seconds":      round(statistics.fmean(seconds_list), 6),
            "q25_seconds":       round(float(np.percentile(seconds_list, 25)), 6),
            "q75_seconds":       round(float(np.percentile(seconds_list, 75)), 6),
            "median_conflicts":  int(med_c),
        })

    return summaries, all_rows


# ============================================================
# LINE GRAPH
# ============================================================

def create_line_graph(summaries, param_label, policy, output_folder):
    """
    Line graph: parameter value on x-axis, median solve time on y-axis.
    Shaded band shows IQR.  Best point is marked with a star.
    """

    if not summaries:
        print("  No data to plot.")
        return

    print()
    print("=" * 60)
    print("CREATING LINE GRAPH")
    print("=" * 60)

    df = pd.DataFrame(summaries)
    x  = df["param_value"].values

    if Y_METRIC == "seconds":
        y     = df["median_seconds"].values
        y_lbl = "Median solve time (seconds)"
        q25   = df["q25_seconds"].values
        q75   = df["q75_seconds"].values
    else:
        y     = df["median_conflicts"].values
        y_lbl = "Median conflicts"
        q25   = None
        q75   = None

    best_idx = int(np.argmin(y))
    best_x   = x[best_idx]
    best_y   = y[best_idx]

    fig, ax = plt.subplots(figsize=(11, 6))

    # IQR band (seconds only).
    if q25 is not None and q75 is not None:
        ax.fill_between(x, q25, q75, alpha=0.2, label="IQR (Q1–Q3)")

    # Main line.
    ax.plot(x, y, marker="o", linewidth=2, markersize=6, label=f"Median {Y_METRIC}")

    # Best point.
    ax.plot(
        best_x, best_y,
        marker="*", markersize=18, color="gold", zorder=5,
        label=f"Best: {best_x}  →  {best_y:.4f}",
    )

    ax.axvline(best_x, color="gold", linestyle="--", linewidth=1, alpha=0.6)

    ax.set_xlabel(param_label, fontsize=12)
    ax.set_ylabel(y_lbl, fontsize=12)

    ax.set_title(
        f"Parameter sweep — {policy} policy\n"
        f"{param_label}  (n={df['n_solved'].iloc[0]} instances per point)",
        fontsize=12,
    )

    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    # Annotate each data point with its y value.
    for xi, yi in zip(x, y):
        ax.annotate(
            f"{yi:.3f}",
            (xi, yi),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            fontsize=8,
        )

    plt.tight_layout()

    slug = f"{policy}_{swept_flag.lstrip('-').replace('-', '_')}"
    filename = output_folder / f"parameter_sweep_{slug}.png"

    plt.savefig(filename, dpi=200, bbox_inches="tight")
    plt.close()

    print(f"  Saved line graph: {filename.name}")
    print(f"  Best value found: {param_label} = {best_x}  ({best_y:.4f} {Y_METRIC})")


# ============================================================
# SAVE RAW DATA
# ============================================================

def save_results(summaries, all_rows, output_folder, policy):
    """Save summary CSV and full raw-results CSV."""

    if summaries:
        summary_df = pd.DataFrame(summaries)
        path = output_folder / f"parameter_sweep_{policy}_summary.csv"
        summary_df.to_csv(path, index=False)
        print(f"  Saved summary CSV: {path.name}")

    if all_rows:
        raw_df = pd.DataFrame(all_rows)
        path = output_folder / f"parameter_sweep_{policy}_raw.csv"
        raw_df.to_csv(path, index=False)
        print(f"  Saved raw CSV    : {path.name}")


# ============================================================
# MAIN
# ============================================================

def main():

    cnf_folder = Path(CNF_FOLDER)

    if not cnf_folder.exists():
        raise FileNotFoundError(f"CNF folder not found:\n{cnf_folder}")

    output_folder = cnf_folder / "analysis_output"
    output_folder.mkdir(parents=True, exist_ok=True)

    # ---- Resolve which parameter to sweep -------------------------

    global swept_flag, param_label, param_type
    swept_flag, param_label, param_type = resolve_param(POLICY, GEOMETRIC_TARGET)

    # ---- Generate parameter values --------------------------------

    raw_values = np.linspace(RANGE_MIN, RANGE_MAX, N_STEPS)

    if param_type == int:
        # Round to integers and clip to minimum of 1
        # (restart_unit=0 would restart every conflict, causing chaos).
        swept_values = [max(1, int(round(v))) for v in raw_values]
    else:
        swept_values = [round(float(v), 4) for v in raw_values]

    # ---- Find CNF files -------------------------------------------

    cnf_files = sorted(cnf_folder.rglob("*.cnf"))

    if not cnf_files:
        raise RuntimeError(f"No .cnf files found in:\n{cnf_folder}")

    print("=" * 60)
    print("SETUP")
    print("=" * 60)
    print(f"  Solver      : {SOLVER_EXE}")
    print(f"  CNF folder  : {cnf_folder}")
    print(f"  Instances   : {len(cnf_files)}")
    print(f"  Policy      : {POLICY}")
    print(f"  Sweeping    : {swept_flag}  ({param_label})")
    print(f"  Range       : {RANGE_MIN} – {RANGE_MAX}")
    print(f"  Steps       : {N_STEPS}")
    print(f"  Values      : {swept_values}")
    print(f"  Y metric    : {Y_METRIC}")
    print(f"  Timeout     : {TIMEOUT_SECONDS}s per file")
    print()

    # ---- Run sweep ------------------------------------------------

    start = time.time()
    summaries, all_rows = run_sweep(cnf_files, POLICY, swept_flag, swept_values)
    elapsed = time.time() - start

    # ---- Save and plot --------------------------------------------

    print()
    print("=" * 60)
    print("SAVING RESULTS")
    print("=" * 60)

    save_results(summaries, all_rows, output_folder, POLICY)
    create_line_graph(summaries, param_label, POLICY, output_folder)

    # ---- Done -----------------------------------------------------

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"\n  Total sweep time : {elapsed:.1f}s")
    print(f"  Outputs saved to : {output_folder}")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()