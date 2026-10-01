"""Warm-socket transfer receiver.

Runs inside every test_server container (started by
network_graph.init_server_listener, next to `iperf -s`).
Accepts persistent TCP connections from xfer_send.py peers; per transfer it
reads an 8-byte big-endian payload length, then exactly that many bytes
(discarded through a fixed buffer, never held in memory, so the IoT hosts'
32 MB mem_limit is safe), then writes a 1-byte ack confirming the payload
fully arrived, without tearing the connection down.
"""

import os
import socket
import struct
import threading

PORT = int(os.environ.get("SPOT_XFER_PORT", "5002"))
CHUNK = 64 * 1024


def _recv_exact(conn, n):
    data = b""
    while len(data) < n:
        piece = conn.recv(n - len(data))
        if not piece:
            return None  # peer closed mid-frame
        data += piece
    return data


def _serve(conn):
    buf = bytearray(CHUNK)
    view = memoryview(buf)
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        while True:
            header = _recv_exact(conn, 8)
            if header is None:
                return  # clean end of a persistent connection
            remaining = struct.unpack(">Q", header)[0]
            while remaining > 0:
                got = conn.recv_into(view[: min(remaining, CHUNK)])
                if got == 0:
                    return  # peer died mid-payload; no ack
                remaining -= got
            conn.sendall(b"\x01")
    except OSError:
        pass  # broken connection: drop it; the sender reconnects
    finally:
        conn.close()


def main(port=PORT):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", port))
    srv.listen(16)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
