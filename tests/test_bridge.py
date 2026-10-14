import http.client
import json
import threading

import pytest

from carritos.phone_bridge import PhoneHub, make_server, parse_phone_payload


# --- payload validation -----------------------------------------------------

def test_parse_valid_payload():
    raw = b'{"id": "phone-ab12", "roll": 10.5, "pitch": -3}'
    assert parse_phone_payload(raw) == ("phone-ab12", 10.5, -3.0)


@pytest.mark.parametrize("car_id", ["", "a/b", "a+b", "#", "x" * 25, "has space", "ñandú", 123, None])
def test_parse_rejects_unsafe_ids(car_id):
    raw = json.dumps({"id": car_id, "roll": 0, "pitch": 0}).encode()
    assert parse_phone_payload(raw) is None


@pytest.mark.parametrize("raw", [
    b"", b"nope", b"[]", b"7", b'{"id": "a"}', b'{"id": "a", "roll": "x", "pitch": 1}',
    b'{"id": "a", "roll": NaN, "pitch": 0}', b'{"id": "a", "roll": 0, "pitch": Infinity}', b"\xff",
])
def test_parse_rejects_garbage(raw):
    assert parse_phone_payload(raw) is None


def test_parse_clamps_angles_to_90():
    assert parse_phone_payload(b'{"id": "a", "roll": 500, "pitch": -500}') == ("a", 90.0, -90.0)


# --- hub --------------------------------------------------------------------

class Recorder:
    def __init__(self):
        self.sent = []

    def __call__(self, topic, payload, retain=False):
        self.sent.append((topic, payload, retain))


class FakeClock:
    t = 0.0

    def __call__(self):
        return self.t


def make_hub():
    rec, clock = Recorder(), FakeClock()
    return PhoneHub(rec, prefix="carritos", clock=clock, timeout=2.0), rec, clock


def test_first_tilt_announces_online_then_publishes_tilt():
    hub, rec, _ = make_hub()
    hub.on_tilt("phone-1", 12.0, -4.5)
    assert rec.sent[0] == ("carritos/phone-1/status", "online", True)
    topic, payload, retain = rec.sent[1]
    assert topic == "carritos/phone-1/tilt" and retain is False
    assert json.loads(payload) == {"roll": 12.0, "pitch": -4.5}


def test_online_is_announced_only_once():
    hub, rec, _ = make_hub()
    hub.on_tilt("phone-1", 0, 0)
    hub.on_tilt("phone-1", 1, 1)
    assert [t for t, _, _ in rec.sent].count("carritos/phone-1/status") == 1


def test_silent_phone_is_published_offline_once():
    hub, rec, clock = make_hub()
    hub.on_tilt("phone-1", 0, 0)
    clock.t = 2.5
    hub.expire()
    hub.expire()
    offline = [s for s in rec.sent if s[1] == "offline"]
    assert offline == [("carritos/phone-1/status", "offline", True)]


def test_phone_can_rejoin_after_expiring():
    hub, rec, clock = make_hub()
    hub.on_tilt("phone-1", 0, 0)
    clock.t = 5.0
    hub.expire()
    hub.on_tilt("phone-1", 0, 0)
    assert [s[1] for s in rec.sent if s[0].endswith("/status")] == ["online", "offline", "online"]


# --- HTTP layer (plain HTTP on an ephemeral port; TLS is wrapped on top in main) ---

@pytest.fixture
def server():
    hub, rec, _ = make_hub()
    srv = make_server(hub, "127.0.0.1", 0, page=b"<html>page</html>")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1], rec
    srv.shutdown()
    srv.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def test_get_root_serves_the_page(server):
    port, _ = server
    status, data = request(port, "GET", "/")
    assert status == 200 and data == b"<html>page</html>"


def test_post_valid_tilt_is_forwarded(server):
    port, rec = server
    body = json.dumps({"id": "phone-1", "roll": 20, "pitch": 5}).encode()
    status, _ = request(port, "POST", "/tilt", body, {"Content-Type": "application/json"})
    assert status == 204
    assert ("carritos/phone-1/tilt", '{"roll": 20.0, "pitch": 5.0}', False) in rec.sent


def test_post_invalid_tilt_is_rejected(server):
    port, rec = server
    status, _ = request(port, "POST", "/tilt", b'{"id": "a/b", "roll": 0, "pitch": 0}')
    assert status == 400 and rec.sent == []


def test_oversized_body_is_rejected(server):
    port, rec = server
    status, _ = request(port, "POST", "/tilt", b"x" * 5000)
    assert status == 413 and rec.sent == []


def test_unknown_paths_404(server):
    port, _ = server
    assert request(port, "GET", "/nope")[0] == 404
    assert request(port, "POST", "/nope", b"{}")[0] == 404
