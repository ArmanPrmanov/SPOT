"""Simulation driver reproducing the evaluation of Section 5 of the paper.

Runs JPOFH against the LE / RE / SOFS benchmarks, averaging the average task
completion time over many random network/task instances (Section 5.3 uses 30
iterations, 95% confidence interval).

Examples
--------
    python simulate.py                       # default-parameter comparison
    python simulate.py --sweep tasks         # effect of number of tasks (Fig. 2)
    python simulate.py --sweep devices       # effect of number of devices (Fig. 4)
    python simulate.py --sweep paths         # effect of number of routing paths
    python simulate.py --sweep data          # effect of amount of input data
    python simulate.py --iters 100 --seed 7  # more iterations / fixed seed
"""

from __future__ import annotations

import argparse
import time
from typing import Callable, Dict, List

import numpy as np

from models import build_network, generate_tasks, MEAN_D
from jpofh import jpofh
from benchmarks import local_execution, remote_execution, sofs

# name -> solver
SOLVERS: Dict[str, Callable] = {
    "LE": local_execution,
    "RE": remote_execution,
    "SOFS": sofs,
    "JPOFH": jpofh,
}


def _ci95(samples: np.ndarray) -> float:
    """Half-width of the 95% confidence interval of the mean."""
    n = len(samples)
    if n < 2:
        return 0.0
    return 1.96 * float(np.std(samples, ddof=1)) / np.sqrt(n)


def run_point(num_tasks: int, num_devices: int, num_paths: int,
              iters: int, seed: int, fixed_device: bool,
              mean_data: float = MEAN_D) -> Dict[str, dict]:
    """Average each solver over `iters` random instances of one parameter point."""
    comp = {name: [] for name in SOLVERS}
    runtime = {name: [] for name in SOLVERS}

    for it in range(iters):
        rng = np.random.default_rng(seed + it)
        net = build_network(num_devices, num_paths, rng)
        tasks = generate_tasks(num_tasks, num_devices, rng,
                               fixed_device=fixed_device, mean_data=mean_data)
        for name, solve in SOLVERS.items():
            t0 = time.perf_counter()
            sched = solve(net, tasks)
            runtime[name].append(time.perf_counter() - t0)
            comp[name].append(sched.avg_completion_time)

    out = {}
    for name in SOLVERS:
        c = np.array(comp[name])
        r = np.array(runtime[name])
        out[name] = dict(comp=c.mean(), comp_ci=_ci95(c),
                         rt=r.mean(), rt_ci=_ci95(r))
    return out


def _print_point(title: str, res: Dict[str, dict]) -> None:
    print(f"\n{title}")
    print(f"  {'solver':6} {'completion (ms)':>22} {'runtime (ms)':>18}"
          f"   {'vs JPOFH':>9}")
    jp = res["JPOFH"]["comp"]
    for name in ["LE", "RE", "SOFS", "JPOFH"]:
        r = res[name]
        gap = "" if name == "JPOFH" else f"{100 * (r['comp'] - jp) / r['comp']:+6.1f}%"
        print(f"  {name:6} {r['comp']*1e3:10.4f} +/- {r['comp_ci']*1e3:7.4f}"
              f"   {r['rt']*1e3:9.4f} +/- {r['rt_ci']*1e3:7.4f}   {gap:>9}")


def default_comparison(args) -> None:
    """Table 3 / Table 4 style comparison at default parameters."""
    res = run_point(args.tasks, args.devices, args.paths, args.iters,
                    args.seed, fixed_device=not args.random_device)
    setting = "random device" if args.random_device else "fixed device"
    _print_point(f"Default parameters "
                 f"(N={args.tasks}, M={args.devices}, K={args.paths}, "
                 f"{setting}, {args.iters} iters)", res)
    print("\n  (negative 'vs JPOFH' = benchmark is that % slower than JPOFH)")


def _sweep(args, label: str, values: List, kwargs_fn) -> None:
    print(f"\n=== Sweep: {label} ===")
    header = f"{label:>10} | " + " | ".join(f"{n:>10}" for n in SOLVERS)
    print(header)
    print("-" * len(header))
    for v in values:
        kw = kwargs_fn(v)
        res = run_point(iters=args.iters, seed=args.seed, **kw)
        row = f"{v:>10} | " + " | ".join(
            f"{res[n]['comp']*1e3:10.4f}" for n in SOLVERS)
        print(row)
    print("(values are average completion time in ms)")


def sweep_tasks(args) -> None:
    # Fig. 2: 2..20 tasks. Fixed device while N < M (10), random device after.
    values = [2, 4, 6, 8, 10, 12, 16, 20]
    _sweep(args, "num_tasks", values, lambda n: dict(
        num_tasks=n, num_devices=max(args.devices, n),
        num_paths=args.paths, fixed_device=(n <= args.devices)))


def sweep_devices(args) -> None:
    # Fig. 4: 3..25 devices, N fixed (default 5), fixed device.
    values = [3, 5, 8, 10, 15, 20, 25]
    _sweep(args, "num_devices", [v for v in values if v >= args.tasks], lambda m: dict(
        num_tasks=args.tasks, num_devices=m, num_paths=args.paths,
        fixed_device=True))


def sweep_paths(args) -> None:
    # Fig. 6: 1..5 routing paths.
    values = [1, 2, 3, 4, 5]
    _sweep(args, "num_paths", values, lambda k: dict(
        num_tasks=args.tasks, num_devices=args.devices, num_paths=k,
        fixed_device=not args.random_device))


def sweep_data(args) -> None:
    # Fig. 8: mean input data 5 Kb .. 200 Kb.
    values = [5e3, 25e3, 50e3, 100e3, 200e3]
    print("\n=== Sweep: mean input data (Kb) ===")
    header = f"{'data(Kb)':>10} | " + " | ".join(f"{n:>10}" for n in SOLVERS)
    print(header)
    print("-" * len(header))
    for d in values:
        res = run_point(args.tasks, args.devices, args.paths, args.iters,
                        args.seed, fixed_device=not args.random_device,
                        mean_data=d)
        row = f"{d/1e3:>10.0f} | " + " | ".join(
            f"{res[n]['comp']*1e3:10.4f}" for n in SOLVERS)
        print(row)
    print("(values are average completion time in ms)")


def main() -> None:
    p = argparse.ArgumentParser(description="JPOFH simulation (Sahni et al., TPDS 2021)")
    p.add_argument("--sweep", choices=["tasks", "devices", "paths", "data"],
                   help="run a parameter sweep instead of the default comparison")
    p.add_argument("--tasks", type=int, default=5, help="number of tasks N")
    p.add_argument("--devices", type=int, default=10, help="number of devices M")
    p.add_argument("--paths", type=int, default=3, help="routing paths per pair K")
    p.add_argument("--iters", type=int, default=30, help="random iterations to average")
    p.add_argument("--seed", type=int, default=1, help="base RNG seed")
    p.add_argument("--random-device", action="store_true",
                   help="use the random-device task placement setting")
    args = p.parse_args()

    if args.sweep is None:
        default_comparison(args)
    else:
        {"tasks": sweep_tasks, "devices": sweep_devices,
         "paths": sweep_paths, "data": sweep_data}[args.sweep](args)


if __name__ == "__main__":
    main()
