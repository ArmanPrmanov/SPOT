"""Loopback self-test for the warm-socket transfer path.

Runs on any machine, no Docker: starts xfer_server.py in-process on a test
port, drives xfer_send.py subprocesses through exec_pool.XferPool exactly
as network_graph does, and checks the framing, the ERR -> None path, and
channel replacement after a failure.

    python xfer_selftest.py
"""

import os
import socket
import threading
import time

TEST_PORT = 15002
os.environ["SPOT_XFER_PORT"] = str(TEST_PORT)
os.environ["SPOT_XFER_CONNECT_TIMEOUT"] = "2"

import xfer_server
from exec_pool import XferPool

HERE = os.path.dirname(os.path.abspath(__file__))

threading.Thread(target=xfer_server.main, args=(TEST_PORT,),
                 daemon=True).start()
time.sleep(0.3)  # let the listener bind

pool = XferPool(
    timeout=10.0,
    argv_factory=lambda _cid: ["python", "-u",
                               os.path.join(HERE, "xfer_send.py")])

# happy path: framing + ack round-trips, reply parses as seconds
t_small = pool.send("c1", "127.0.0.1", 1024)
t_large = pool.send("c1", "127.0.0.1", 300 * 1024)
assert t_small is not None and t_small >= 0.0, t_small
assert t_large is not None and t_large >= 0.0, t_large
print(f"loopback 1K = {t_small*1e3:.3f} ms, 300K = {t_large*1e3:.3f} ms")

# several transfers reuse one warm socket (same sender process throughout)
first_pid = pool._channels["c1"].proc.pid
for _ in range(5):
    assert pool.send("c1", "127.0.0.1", 4096) is not None
assert pool._channels["c1"].proc.pid == first_pid

# unreachable destination (TEST-NET-3): ERR reply -> None, channel dropped
assert pool.send("c1", "203.0.113.1", 1024) is None
assert "c1" not in pool._channels
# ... and the next send gets a fresh process and works again
t_after = pool.send("c1", "127.0.0.1", 1024)
assert t_after is not None
assert pool._channels["c1"].proc.pid != first_pid

# malformed size never reaches the sender: XferPool only sends ints; but a
# dead receiver mid-payload must also come back as None, not a hang
victim = socket.socket()  # occupy a port with no reader: connect succeeds,
victim.bind(("127.0.0.1", TEST_PORT + 1))
victim.listen(1)          # but no ack ever arrives -> IO timeout -> ERR
os.environ["SPOT_XFER_PORT"] = str(TEST_PORT + 1)
pool2 = XferPool(
    timeout=5.0,
    argv_factory=lambda _cid: ["python", "-u",
                               os.path.join(HERE, "xfer_send.py")])
# the sender's warm-up itself stalls (no ack): expect None via pool timeout
assert pool2.send("c2", "127.0.0.1", 1024) is None
pool2.close()
victim.close()

pool.close()
assert not pool._channels
print("xfer self-test OK")
