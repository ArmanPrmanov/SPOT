"""Persistent `docker exec` channels for the transfer measurements.

Spawning one `docker exec` process per transfer costs ~0.33 s of wall clock
and steals CPU from OVS forwarding while the measurement is in flight,
inflating recorded transfer times ~2x. Instead, keep one long-lived
`docker exec -i <container> sh` per container and pipe every command through
it (~4 ms channel overhead).

A hung or broken command returns None (the caller treats it as a failed
transfer), and the channel it failed on is dropped, never reused, so
late-arriving output cannot desynchronize a later call. The next call for
that container gets a fresh shell.
"""

import queue
import subprocess
import threading
import time

_SENTINEL = "__SPOT_EOF__"


class _Channel:
    """One long-lived shell; a daemon thread drains its stdout into a queue
    so reads can time out (pipes have no portable read timeout)."""

    def __init__(self, argv):
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._lines = queue.Queue()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # EOF: the shell itself died

    def run(self, cmd, timeout):
        self.proc.stdin.write(f"{cmd}; echo {_SENTINEL}\n")
        self.proc.stdin.flush()
        out = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(cmd)
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(cmd)
            if line is None:
                raise EOFError(cmd)
            # endswith, not ==: if cmd's output lacked a trailing newline the
            # sentinel lands on the same line as the output's last chunk
            if line.rstrip("\n").endswith(_SENTINEL):
                tail = line[: line.rfind(_SENTINEL)]
                if tail:
                    out.append(tail)
                return "".join(out)
            out.append(line)

    def ask(self, line, timeout):
        """One-line command -> one-line reply (xfer_send.py's protocol; no
        sentinel needed because the reply is exactly one line)."""
        self.proc.stdin.write(f"{line}\n")
        self.proc.stdin.flush()
        try:
            reply = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(line)
        if reply is None:
            raise EOFError(line)
        return reply.rstrip("\n")

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


class ExecPool:
    """One persistent shell per container id, created lazily on first use."""

    def __init__(self, timeout=60.0, argv_factory=None):
        self.timeout = timeout
        self._argv = argv_factory or (
            lambda container_id: ["docker", "exec", "-i", container_id, "sh"])
        self._channels = {}

    def run(self, container_id, cmd, timeout=None):
        """Run `cmd` in the container's shell; return its combined
        stdout+stderr, or None if it timed out or the channel broke."""
        channel = self._channels.get(container_id)
        if channel is None or channel.proc.poll() is not None:
            channel = self._channels[container_id] = _Channel(self._argv(container_id))
        try:
            return channel.run(cmd, timeout if timeout is not None else self.timeout)
        except (TimeoutError, EOFError, OSError, ValueError):
            # ValueError covers writes to an already-closed stdin
            self._channels.pop(container_id, None)
            channel.close()
            return None

    def close(self):
        while self._channels:
            _, channel = self._channels.popitem()
            channel.close()


class XferPool:
    """One long-lived xfer_send.py per source container.

    `send` returns the measured transfer seconds as float, or None on any
    failure -- timeout, dead process, or an ERR reply. Failure semantics
    mirror ExecPool: the failed channel is dropped, never reused (a stale
    reply from a timed-out command could otherwise desynchronize the
    one-line protocol), and the next send for that container starts a
    fresh sender process, which re-warms its sockets from scratch.
    """

    def __init__(self, timeout=120.0, argv_factory=None):
        self.timeout = timeout
        self._argv = argv_factory or (
            lambda container_id: ["docker", "exec", "-i", container_id,
                                  "python", "-u", "/app/xfer_send.py"])
        self._channels = {}

    def send(self, container_id, dst_ip, nbytes, timeout=None):
        channel = self._channels.get(container_id)
        if channel is None or channel.proc.poll() is not None:
            channel = self._channels[container_id] = _Channel(self._argv(container_id))
        try:
            reply = channel.ask(f"{dst_ip} {nbytes}",
                                timeout if timeout is not None else self.timeout)
            result = float(reply)  # "ERR ..." falls into ValueError below
        except (TimeoutError, EOFError, OSError, ValueError):
            self._channels.pop(container_id, None)
            channel.close()
            return None
        return result

    def close(self):
        while self._channels:
            _, channel = self._channels.popitem()
            channel.close()


if __name__ == "__main__":
    # Self-test over a plain local `sh` (no Docker needed): exercises the
    # sentinel framing, multi-line and newline-less output, the
    # timeout -> None path, and channel replacement after a failure.
    pool = ExecPool(timeout=2.0, argv_factory=lambda _: ["sh"])

    assert pool.run("c1", "echo one; echo two") == "one\ntwo\n"
    assert pool.run("c1", "printf 'no trailing newline'") == "no trailing newline"
    assert pool.run("c1", "true") == ""
    assert pool.run("c1", "echo oops >&2") == "oops\n"  # stderr is captured too

    first_pid = pool._channels["c1"].proc.pid
    assert pool.run("c1", "sleep 10") is None            # timeout -> None
    assert "c1" not in pool._channels                    # hung channel dropped
    assert pool.run("c1", "echo alive") == "alive\n"     # fresh shell works
    assert pool._channels["c1"].proc.pid != first_pid

    assert pool.run("c2", "echo other") == "other\n"     # second container
    assert len(pool._channels) == 2

    pool.close()
    assert not pool._channels
    print("exec_pool self-test OK")
