"""Warm-socket transfer sender.

One long-lived process per source container, spawned by exec_pool.XferPool
as `docker exec -i <container> python -u /app/xfer_send.py` and driven over
stdio -- the same pattern as the persistent shells in exec_pool.py, but
with a fixed one-reply-per-command line protocol instead of sentinel
framing:

    command:  "<dst_ip> <bytes>\n"
    reply:    "<elapsed_seconds>\n"  |  "ERR <reason>\n"

One TCP socket is kept open per destination ip across transfers. On first
use of a destination the connect and one untimed 256 KB warm-up transfer
are paid before the measurement, so TCP handshake and slow-start never
enter a recorded time. The measured quantity is: length header handed to
the warm socket -> the receiver's 1-byte ack of the full payload.

On any socket error the destination's socket is closed and ERR is replied;
the harness drops this process and the next transfer starts fresh (as in
exec_pool, a failed channel is never reused).
"""

import os
import socket
import struct
import sys
import time

PORT = int(os.environ.get("SPOT_XFER_PORT", "5002"))
CONNECT_TIMEOUT = float(os.environ.get("SPOT_XFER_CONNECT_TIMEOUT", "30"))
IO_TIMEOUT = 60.0
WARMUP_BYTES = 256 * 1024
_CHUNK = b"\x00" * (64 * 1024)


def _transfer(sock, nbytes):
    sock.sendall(struct.pack(">Q", nbytes))
    remaining = nbytes
    while remaining > 0:
        take = min(remaining, len(_CHUNK))
        sock.sendall(_CHUNK[:take])
        remaining -= take
    if not sock.recv(1):
        raise ConnectionError("receiver closed before ack")


def _connect(dst_ip):
    sock = socket.create_connection((dst_ip, PORT), timeout=CONNECT_TIMEOUT)
    sock.settimeout(IO_TIMEOUT)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    _transfer(sock, WARMUP_BYTES)  # untimed: pays slow-start up front
    return sock


def main():
    sockets = {}
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 2:
            print("ERR bad command", flush=True)
            continue
        dst_ip = parts[0]
        try:
            nbytes = int(parts[1])
        except ValueError:
            print("ERR bad size", flush=True)
            continue

        sock = sockets.get(dst_ip)
        try:
            if sock is None:
                sock = sockets[dst_ip] = _connect(dst_ip)
            t0 = time.perf_counter()
            _transfer(sock, nbytes)
            print(f"{time.perf_counter() - t0:.6f}", flush=True)
        except OSError as exc:
            sock = sockets.pop(dst_ip, None)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            print(f"ERR {exc}", flush=True)


if __name__ == "__main__":
    main()
