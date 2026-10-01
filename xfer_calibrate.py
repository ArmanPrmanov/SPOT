"""Calibration check for the warm-socket transfer path.

Run on the Linux host with the topology up and test_server:latest rebuilt:

    python xfer_calibrate.py [src] [dst]     # default mn.mec1 mn.mec2

For each payload size it measures 20 warm-socket transfers and 20 one-shot
iperf transfers (SPOT_XFER=iperf) between the same pair, and prints both
against the theoretical wire time of the shaped links.

Expected: warm-socket time scales roughly linearly with bytes, approaches
the theoretical value for 50M, and the 1K residual overhead is at most
~10% of a 300K transfer.
"""

import os
import statistics
import sys

import network_graph as ng

SRC = sys.argv[1] if len(sys.argv) > 1 else "mn.mec1"
DST = sys.argv[2] if len(sys.argv) > 2 else "mn.mec2"
REPS = 20
SIZES = ["1K", "300K", "50M"]
# The default pair mn.mec1 -> mn.mec2 crosses mec1-s1 (1 Gbit), s1-s2
# (500 Mbit) and s2-mec2 (1 Gbit) in topology.py, so the serialization
# bottleneck is the 500 Mbit inter-switch link, not the 1 Gbit access
# links. Each direction also adds 3 x 1 ms of shaped delay, and the
# measured quantity waits for the receiver's ack, so every sample contains
# ~6 ms of propagation + ack RTT. Adjust both constants when calibrating a
# different pair.
BOTTLENECK_BITS_PER_S = 500e6
PATH_RTT_S = 0.006

mapping = ng.get_docker_details()
ng.init_server_listener(mapping, [SRC, DST])


def run(label):
    print(f"\n--- {label} ({SRC} -> {DST}, {REPS} reps) ---")
    for size in SIZES:
        times = []
        for _ in range(REPS):
            t = ng.docker_send_to(SRC, DST, size, mapping, "calibrate")
            if t is not None:
                times.append(t)
        theo = ng.size_to_bytes(size) * 8 / BOTTLENECK_BITS_PER_S + PATH_RTT_S
        if times:
            print(f"{size:>5}: mean={statistics.mean(times):.5f}s "
                  f"median={statistics.median(times):.5f}s "
                  f"stdev={statistics.pstdev(times):.5f}s "
                  f"n={len(times)}  theoretical(wire+RTT)={theo:.5f}s")
        else:
            print(f"{size:>5}: ALL FAILED")


os.environ.pop("SPOT_XFER", None)
run("warm socket")
os.environ["SPOT_XFER"] = "iperf"
run("one-shot iperf")
os.environ.pop("SPOT_XFER", None)
