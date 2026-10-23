"""The home connection: the server's proxy hands each request to an idle home connection; the home side decides what
it may open (listed sites, ports 80/443, public addresses only)."""

import json
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path[:0] = [str(ROOT / "bot"), str(ROOT / "home")]
import agent  # noqa: E402
import homeproxy  # noqa: E402


def fake_dns(table):
    def resolve(host, port, type=None):
        if host not in table:
            raise OSError("no such host")
        return [(None, None, None, None, (ip, port)) for ip in table[host]]
    return resolve


def test_home_side_opens_only_listed_public_sites_on_web_ports():
    dns = fake_dns({"www.zomato.com": ["104.18.1.1"], "evil.zomato.com": ["192.168.1.1"], "b.zmtcdn.com": ["1.1.1.1"],
                    "router.local": ["192.168.1.1"], "example.com": ["93.184.216.34"]})
    sites = ["zomato.com", "zmtcdn.com"]
    assert agent.allowed("www.zomato.com", 443, sites, dns) == ("104.18.1.1", "")
    assert agent.allowed("b.zmtcdn.com", 80, sites, dns)[0] == "1.1.1.1"
    assert agent.allowed("evil.zomato.com", 443, sites, dns)[1] == "evil.zomato.com points at a non-public address"
    assert agent.allowed("example.com", 443, sites, dns)[1] == "example.com isn't a listed site"
    assert agent.allowed("notzomato.com", 443, sites, dns)[1] == "notzomato.com isn't a listed site"
    assert agent.allowed("www.zomato.com", 22, sites, dns)[1] == "port 22"
    assert agent.allowed("router.local", 80, sites, dns)[0] is None


def test_requests_go_through_home_and_say_plainly_when_home_is_offline(monkeypatch):
    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"from home: {self.path}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    proxy = homeproxy.HomeProxy(agent_port=0, proxy_port=0)
    via = urllib.request.build_opener(urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy.proxy_port}"}))
    status = f"http://127.0.0.1:{proxy.proxy_port}/status"

    try:  # no home helper yet: offline, said plainly
        via.open("http://www.zomato.com/x", timeout=5)
        raise AssertionError("expected 502")
    except urllib.error.HTTPError as e:
        assert e.code == 502 and e.read() == b"home connection offline"
    assert homeproxy.home_status(status)["online"] is False

    # the helper at home (a test site stands in for Zomato: the address check is the only thing replaced)
    monkeypatch.setattr(agent, "allowed", lambda host, port: ("127.0.0.1", "") if host == "www.zomato.com"
                        else (None, f"{host} isn't a listed site"))
    for _ in range(3):
        threading.Thread(target=agent.worker, args=(("127.0.0.1", proxy.agent_port),), daemon=True).start()
    end = time.time() + 3
    while time.time() < end and not homeproxy.home_status(status)["online"]:
        time.sleep(0.02)
    assert homeproxy.home_status(status)["online"] is True
    url = f"http://www.zomato.com:{site.server_address[1]}/bangalore?q=dosa"
    with via.open(url, timeout=5) as r:
        assert r.read() == b"from home: /bangalore?q=dosa"
    with via.open(url, timeout=5) as r:  # the used connection was replaced: works again
        assert r.read() == b"from home: /bangalore?q=dosa"
    try:
        via.open(f"http://example.com:{site.server_address[1]}/", timeout=5)
        raise AssertionError("expected 403")
    except urllib.error.HTTPError as e:
        assert e.code == 403 and b"isn't a listed site" in e.read()

    # https goes as CONNECT: the tunnel is opened, then bytes flow both ways
    s = socket.create_connection(("127.0.0.1", proxy.proxy_port), timeout=5)
    s.sendall(f"CONNECT www.zomato.com:{site.server_address[1]} HTTP/1.1\r\n\r\n".encode())
    assert s.recv(100).startswith(b"HTTP/1.1 200 Connection established")
    s.sendall(b"GET /tls-stand-in HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    got = b""
    while chunk := s.recv(4096):
        got += chunk
    assert got.endswith(b"from home: /tls-stand-in")
    assert json.loads(urllib.request.urlopen(status, timeout=3).read())["idle"] >= 1
