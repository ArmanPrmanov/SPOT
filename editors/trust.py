"""Feedback-based trust management (paper Section 4, Algorithm 1).

The paper predicts trust scores with an LSTM, but the *target* it learns is a
closed-form quantity: a Shannon-entropy-weighted sum of each edge node's energy
consumption, latency and memory usage, thresholded into trusted / untrusted
(Eqs. 3-6). Because the LSTM only regresses onto that formula, we implement the
formula directly -- no training, no dataset required -- which reproduces the
same decision (trusted vs. untrusted node) that EDITORS acts on.

Interpretation note: the paper states that nodes which over- or under-utilise
their resources are less reliable, so all three criteria (energy, latency,
memory) are treated as *cost* criteria -- lower is more trustworthy.
"""

import math


def min_max_normalise(values):
    """Scale a list of values into [0, 1] (paper's data pre-processing step)."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def entropy_weights(columns):
    """Shannon-entropy objective weights for each criterion (Eqs. 3-4).

    ``columns`` is a list of criteria, each a list of that criterion's raw
    (positive) values across all nodes. Returns one weight per criterion,
    summing to 1. A criterion whose values diverge more across nodes carries
    more information and therefore more weight.
    """
    m = len(columns[0])                      # number of edge nodes
    k = 1.0 / math.log(m) if m > 1 else 1.0  # normalisation constant
    divergences = []
    for col in columns:
        total = sum(col)
        if total == 0:
            divergences.append(0.0)
            continue
        p = [v / total for v in col]
        entropy = -k * sum(pi * math.log(pi) for pi in p if pi > 0)
        divergences.append(1.0 - entropy)   # degree of divergence d_j = 1 - H_j
    s = sum(divergences)
    if s == 0:
        return [1.0 / len(columns)] * len(columns)
    return [d / s for d in divergences]


def compute_trust(node_metrics, weights=None, threshold=None):
    """Trust score and trusted-node set (Algorithm 1, Eqs. 5-6).

    Parameters
    ----------
    node_metrics : dict
        ``{node_name: {"energy": J, "latency": ms, "memory": MB}}``.
    weights : (w_e, w_l, w_u) or None
        If None, weights are derived from the data via Shannon entropy.
        Pass the paper's fixed weights (0.5120, 0.1935, 0.3010) to reproduce
        its trust-table experiments.
    threshold : float or None
        Trust cut-off theta. If None, the mean trust score is used.

    Returns
    -------
    trust : dict     node -> trust score in [0, 1]
    trusted : set    nodes with trust >= threshold
    info : dict      weights + threshold actually used (for logging/reproduction)
    """
    names = list(node_metrics)
    energy = [node_metrics[n]["energy"] for n in names]
    latency = [node_metrics[n]["latency"] for n in names]
    memory = [node_metrics[n]["memory"] for n in names]

    if weights is None:
        weights = entropy_weights([energy, latency, memory])
    w_e, w_l, w_u = weights

    # Normalise each cost criterion so that 1.0 == worst node on that criterion.
    ne, nl, nu = min_max_normalise(energy), min_max_normalise(latency), min_max_normalise(memory)

    trust = {}
    for i, n in enumerate(names):
        badness = w_e * ne[i] + w_l * nl[i] + w_u * nu[i]  # weighted sum (Eq. 5)
        trust[n] = round(1.0 - badness, 4)                 # higher == more trustworthy

    if threshold is None:
        threshold = sum(trust.values()) / len(trust)       # MAE-style cut-off (Eq. 6)

    trusted = {n for n, ts in trust.items() if ts >= threshold}
    info = {"weights": (round(w_e, 4), round(w_l, 4), round(w_u, 4)),
            "threshold": round(threshold, 4)}
    return trust, trusted, info
