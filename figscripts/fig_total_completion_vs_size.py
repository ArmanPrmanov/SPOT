"""Figure: TOTAL (summed) completion time vs task size, one line per strategy.

Same join as fig_mean_completion_vs_size.py ({STRAT}_{n}.csv x tasks.json,
binned by task size) but summing survivors' completion times per bin, so a
strategy that discards tasks contributes less in the bins it lost them;
read alongside the completion-ratio figure. Options are identical:

    python figscripts/fig_total_completion_vs_size.py [--run RUN] [--n N]
                                           [--by cpu|memory] [--bins 6] [--logy]
"""
from fig_mean_completion_vs_size import main

if __name__ == "__main__":
    main("total")
