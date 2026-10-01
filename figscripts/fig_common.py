"""Shared plumbing and styling for the paper's scripted figures.

Every fig_*.py reads a finished sweep under results/{RUN}/ (plus
tasks.json) and writes PNG (300 dpi) + PDF into figures/{FIG_ID}/, where
FIG_ID is a YYYYmmdd-HHMMSS stamp taken once per process. Set the
SPOT_FIG_ID env var to land several scripts' output in one directory (as
SPOT_RUN_ID does for results/).

Styling: one fixed color + marker per strategy across every figure, so a
strategy is recognizable between figures and identity never depends on
color alone (markers/labels are the secondary encoding). The categorical
slot order is CVD-validated as a sequence, so keep STRATEGIES in this order
when a figure shows a subset.
"""
import csv
import json
import os
import re
import sys
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# scripts live in figscripts/, one level below the repo root; the root must
# be importable for network_graph and the baseline packages
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
RESULTS = os.path.join(REPO, "results")
FIG_ID = os.environ.get("SPOT_FIG_ID") or datetime.now().strftime("%Y%m%d-%H%M%S")
FIG_DIR = os.path.join(REPO, "figures", FIG_ID)

# Strategy identity: execution order of network_graph.__main__, one
# categorical palette slot each (validated adjacent-pair CVD-safe in this
# order), one marker each (line charts keep identity when printed gray).
STRATEGIES = ["POB", "POR", "BO", "CO", "LE", "JPOFH", "EDITORS"]
COLOR = {
    "POB":     "#2a78d6",   # blue    (slot 1, the proposed scheme)
    "POR":     "#eb6834",   # orange
    "BO":      "#1baf7a",   # aqua
    "CO":      "#eda100",   # yellow
    "LE":      "#e87ba4",   # magenta
    "JPOFH":   "#008300",   # green
    "EDITORS": "#4a3aa7",   # violet
}
MARKER = {"POB": "o", "POR": "s", "BO": "^", "CO": "D",
          "LE": "v", "JPOFH": "P", "EDITORS": "X"}

# Chart chrome (light/print mode only; these are paper figures)
INK, INK2 = "#0b0b0b", "#52514e"
MUTED, GRID, BASELINE = "#898781", "#e1e0d9", "#c3c2b7"


def apply_style():
    plt.rcParams.update({
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": BASELINE,
        "axes.linewidth": 1.0,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.axisbelow": True,
        "axes.labelcolor": INK2,
        "axes.titlecolor": INK,
        "axes.titlesize": 12,
        "axes.labelsize": 11,
        "font.size": 10.5,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": INK2,
        "ytick.labelcolor": INK2,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.frameon": False,
        "legend.fontsize": 10,
        "font.family": "sans-serif",
        "savefig.bbox": "tight",
    })


# ---------------------------------------------------------------------------
# results/ discovery and loading
# ---------------------------------------------------------------------------
_RUN_RE = re.compile(r"^\d{8}-\d{6}$")


def latest_run():
    runs = sorted(d for d in os.listdir(RESULTS)
                  if _RUN_RE.match(d) and os.path.isdir(os.path.join(RESULTS, d)))
    if not runs:
        raise SystemExit(f"no timestamped run directories under {RESULTS}")
    return runs[-1]


def run_dir(run):
    path = os.path.join(RESULTS, run)
    if not os.path.isdir(path):
        raise SystemExit(f"results run not found: {path}")
    return path


def available_ns(run, prefix="POB"):
    """Task counts n for which {prefix}_{n}.csv exists in the run."""
    pat = re.compile(rf"^{re.escape(prefix)}_(\d+)\.csv$")
    ns = sorted(int(m.group(1)) for f in os.listdir(run_dir(run))
                if (m := pat.match(f)))
    if not ns:
        raise SystemExit(f"no {prefix}_*.csv in results/{run}")
    return ns


def load_times(run, strategy, n):
    """{task_id: completion seconds} for one strategy CSV (deadline
    survivors only, as written by save_results). None if missing."""
    path = os.path.join(run_dir(run), f"{strategy}_{n}.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="") as f:
        return {row["Task"]: float(row["Time"]) for row in csv.DictReader(f)}


def load_assigned(run, kind, n):
    """{server: (assigned Mcycles, rate)} from balanced_/random_{n}.csv."""
    path = os.path.join(run_dir(run), f"{kind}_{n}.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="") as f:
        return {row["Server"]: (float(row["AssignedMcycles"]), float(row["Rate"]))
                for row in csv.DictReader(f)}


def load_tasks(n=None):
    """Raw tasks.json entries (identical workload across strategies)."""
    with open(os.path.join(REPO, "tasks.json")) as f:
        tasks = json.load(f)
    return tasks if n is None else tasks[:n]


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
def save(fig, stem, note=""):
    """Write figures/{FIG_ID}/{stem}.png+.pdf and log provenance."""
    os.makedirs(FIG_DIR, exist_ok=True)
    png = os.path.join(FIG_DIR, f"{stem}.png")
    fig.savefig(png, dpi=300)
    fig.savefig(os.path.join(FIG_DIR, f"{stem}.pdf"))
    with open(os.path.join(FIG_DIR, "sources.txt"), "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {stem}  {note}\n")
    print(f"wrote {png} (+ .pdf)")
    return png


def bar_value_labels(ax, bars, fmt="{:.2f}", dy=3):
    """Direct value labels above bars. The low-contrast palette slots
    (aqua/yellow/magenta) need them; they help everywhere else too."""
    for b in bars:
        ax.annotate(fmt.format(b.get_height()),
                    (b.get_x() + b.get_width() / 2, b.get_height()),
                    xytext=(0, dy), textcoords="offset points",
                    ha="center", va="bottom", fontsize=9, color=INK)


# ---------------------------------------------------------------------------
# shared builder: completion time vs NUMBER OF TASKS (mean / total variants)
# ---------------------------------------------------------------------------
def _plot_series(ax, strat, xs, ys):
    ax.plot(xs, ys, color=COLOR[strat], marker=MARKER[strat],
            markersize=7, markeredgecolor="white", markeredgewidth=1.2,
            linewidth=2, label=strat)


def completion_vs_tasks(run, stat="mean", include_le=True):
    """One line per strategy over the run's task counts. LE runs seconds
    while the offloaders sit ~10x lower, so the include_le variant uses a
    broken y-axis: LE alone in a top band (with break marks), everything
    else scaled to its own range below. include_le=False drops LE and notes
    its off-scale range in the footnote instead."""
    ns = available_ns(run)
    agg = np.mean if stat == "mean" else np.sum
    series = {}
    for strat in STRATEGIES:
        xs, ys = [], []
        for n in ns:
            times = load_times(run, strat, n)
            if times:
                xs.append(n)
                ys.append(float(agg(list(times.values()))))
        if xs:
            series[strat] = (xs, ys)
        else:
            print(f"  (skipping {strat}: no {strat}_*.csv in results/{run})")

    label = ("Mean" if stat == "mean" else "Total") + " completion time (s)"
    title = (("Mean" if stat == "mean" else "Total")
             + " completion time vs number of tasks"
             + ("" if stat == "mean" else "  (survivors)"))
    others_max = max(max(ys) for s, (_, ys) in series.items() if s != "LE")
    le = series.get("LE")

    if not (include_le and le):
        fig, ax = plt.subplots(figsize=(7.2, 4.4))
        for strat, (xs, ys) in series.items():
            if strat != "LE":
                _plot_series(ax, strat, xs, ys)
        ax.set_ylim(0, others_max * 1.15)
        ax.set_xticks(ns)
        ax.set_xlabel("Number of tasks")
        ax.set_ylabel(label)
        ax.set_title(title)
        ax.legend(ncols=2, loc="upper left")
        if le:
            fig.text(0.01, -0.02,
                     f"LE omitted: off this scale ({min(le[1]):.1f}-"
                     f"{max(le[1]):.1f} s over these task counts); see the "
                     "companion figure with the broken axis",
                     fontsize=8.5, color=MUTED)
        fig.tight_layout()
        return fig

    # broken axis: LE band on top, everything else below
    # constrained layout: tight_layout can't place supylabel
    fig, (ax_top, ax) = plt.subplots(
        2, 1, sharex=True, figsize=(7.2, 4.8),
        height_ratios=[1, 2.1], layout="constrained")
    fig.get_layout_engine().set(hspace=0.04)
    for strat, (xs, ys) in series.items():
        _plot_series(ax_top if strat == "LE" else ax, strat, xs, ys)
    lo, hi = min(le[1]), max(le[1])
    pad = (hi - lo) * 0.15 or hi * 0.1
    ax_top.set_ylim(lo - pad, hi + pad)
    ax.set_ylim(0, others_max * 1.15)

    # the break: hidden facing spines + diagonal marks
    ax_top.spines.bottom.set_visible(False)
    ax.spines.top.set_visible(False)
    ax_top.tick_params(bottom=False)
    marks = dict(marker=[(-1, -0.5), (1, 0.5)], markersize=10,
                 linestyle="none", color=BASELINE, mec=BASELINE, mew=1,
                 clip_on=False)
    ax_top.plot([0], [0], transform=ax_top.transAxes, **marks)
    ax.plot([0], [1], transform=ax.transAxes, **marks)

    # LE is alone in its band: direct label there, legend below for the rest
    x_last, y_last = le[0][-1], le[1][-1]
    ax_top.annotate("LE", (x_last, y_last), xytext=(8, 0),
                    textcoords="offset points", va="center",
                    color=COLOR["LE"], fontweight="bold")
    ax.legend(ncols=2, loc="lower right")

    ax.set_xticks(ns)
    ax.set_xlabel("Number of tasks")
    ax_top.set_title(title)
    fig.supylabel(label, fontsize=plt.rcParams["axes.labelsize"], color=INK2)
    return fig


# ---------------------------------------------------------------------------
# shared builder: completion time vs task size (mean / total variants)
# ---------------------------------------------------------------------------
SIZE_AXIS = {"cpu": "Task size (Mcycles)", "memory": "Task input data (KB)"}


def completion_vs_size(run, n, stat="mean", by="cpu", bins=6, logy=False):
    """One line per strategy: {mean|total} completion time of deadline
    survivors, binned by task size (JPOFH Fig. 8 style, derived from one
    run)."""
    tasks = load_tasks(n)
    size = {t["task_id"]: float(t[by]) for t in tasks}
    lo, hi = min(size.values()), max(size.values())
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2

    # bin index per task (last edge inclusive)
    which = {tid: min(np.searchsorted(edges, s, side="right") - 1, bins - 1)
             for tid, s in size.items()}
    bin_counts = np.bincount(list(which.values()), minlength=bins)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for strat in STRATEGIES:
        times = load_times(run, strat, n)
        if times is None:
            print(f"  (skipping {strat}: no {strat}_{n}.csv in results/{run})")
            continue
        ys = np.full(bins, np.nan)
        for b in range(bins):
            vals = [t for tid, t in times.items() if which.get(tid) == b]
            if vals:
                ys[b] = np.mean(vals) if stat == "mean" else np.sum(vals)
        ax.plot(centers, ys, color=COLOR[strat], marker=MARKER[strat],
                markersize=7, markeredgecolor="white", markeredgewidth=1.2,
                linewidth=2, label=strat)

    if logy:
        ax.set_yscale("log")
    ax.set_xlabel(SIZE_AXIS[by])
    ax.set_ylabel(("Mean" if stat == "mean" else "Total")
                  + " completion time (s)")
    ax.set_title(("Mean" if stat == "mean" else "Total")
                 + " completion time vs task size"
                 + (f"  (n = {n})" if stat == "mean" else f"  (n = {n}, survivors)"))
    # center right stays clear: LE occupies the upper left, the offloading
    # strategies hug the bottom
    ax.legend(ncols=1, loc="center right")
    fig.text(0.01, -0.02,
             f"tasks per bin: {', '.join(str(c) for c in bin_counts)}"
             ": survivors only; discarded tasks are excluded",
             fontsize=8.5, color=MUTED)
    fig.tight_layout()
    return fig, bin_counts
