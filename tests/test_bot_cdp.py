"""The stdlib WebSocket speaks to a CDP-like server: handshake, masked sends, pings, fragments, big frames."""

import json
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import cdp  # noqa: E402


def frame(op, data, fin=True):
    n = len(data)
    head = bytes([(0x80 if fin else 0) | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n)
                                                  if n < 65536 else bytes([127]) + struct.pack(">Q", n))
    return head + data


def read_frame(conn):
    b1, b2 = conn.recv(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", conn.recv(2))[0]
    mask = conn.recv(4)
    data = b""
    while len(data) < n:
        data += conn.recv(n - len(data))
    return b1 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def server(sock, log):
    conn, _ = sock.accept()
    head = b""
    while b"\r\n\r\n" not in head:
        head += conn.recv(1024)
    log.append(head.decode())
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
    op, data = read_frame(conn)
    req = json.loads(data)
    log.append(req)
    conn.sendall(frame(0x9, b"hi"))                                   # a ping first
    conn.sendall(frame(0x1, json.dumps({"method": "Page.loadEventFired"}).encode()))  # an event to skip
    body = json.dumps({"id": req["id"], "result": {"result": {"value": "x" * 70000}}}).encode()
    conn.sendall(frame(0x1, body[:100], fin=False) + frame(0x0, body[100:]))  # fragmented, > 65535 bytes
    log.append(read_frame(conn))                                      # the pong
    conn.close()


def test_websocket_round_trip_through_a_tab_call():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    log = []
    th = threading.Thread(target=server, args=(sock, log), daemon=True)
    th.start()
    ws = cdp.WebSocket(f"ws://127.0.0.1:{sock.getsockname()[1]}/devtools/page/1")
    tab = cdp.Tab.__new__(cdp.Tab)
    tab.ws, tab.n = ws, 0
    assert tab.js("document.body.innerText") == "x" * 70000
    th.join(2)
    assert "Upgrade: websocket" in log[0] and log[1]["method"] == "Runtime.evaluate"
    assert log[2] == (0xA, b"hi")


def test_settle_waits_for_text_to_stop_changing():
    tab = cdp.Tab.__new__(cdp.Tab)
    pages = iter(["", "loading", "a" * 300, "b" * 500, "b" * 500, "b" * 500, "c"])
    tab.text = lambda: next(pages)
    assert tab.settle(sleep=lambda s: None) == "b" * 500
