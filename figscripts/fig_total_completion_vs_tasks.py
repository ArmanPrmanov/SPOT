"""Figure: TOTAL (summed) completion time vs number of tasks.

Same two variants as fig_mean_completion_vs_tasks.py (broken-axis *_all
with LE in its own band, and *_noLE scaled to the offloaders), summing the
survivors' completion times per task count instead of averaging.

    python figscripts/fig_total_completion_vs_tasks.py [--run RUN]
"""
from fig_mean_completion_vs_tasks import main

if __name__ == "__main__":
    main("total")
