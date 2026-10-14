"""Phone -> MQTT bridge.

Serves a small HTTPS page (phone_controller.html). Open it on a phone on the same
WiFi: it reads the device orientation and POSTs roll/pitch here, and this script
republishes it on MQTT exactly like an ESP32 would, so the phone shows up as a car.

HTTPS is required: mobile browsers only expose motion sensors on secure pages, so a
self-signed certificate is generated on first run (accept the browser warning once).
"""
import argparse
import datetime
import ipaddress
import json
import math
import re
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt

PAGE_PATH = Path(__file__).parent / "web" / "phone_controller.html"
CERT_DIR = Path.home() / ".carritos-mc" / "certs"  # outside the repo: never commit the private key

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")  # no '/', '+', '#': safe as an MQTT topic level
MAX_BODY = 512
MAX_DRAIN = 65536
MAX_ANGLE = 90.0


def parse_phone_payload(raw):
    """Parse '{"id", "roll", "pitch"}' -> (id, roll, pitch) clamped to +-90, or None."""
    try:
        data = json.loads(raw)
        car_id = data["id"]
        roll = float(data["roll"])
        pitch = float(data["pitch"])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(car_id, str) or not ID_RE.match(car_id):
        return None
    if not (math.isfinite(roll) and math.isfinite(pitch)):
        return None
    clamp = lambda v: max(-MAX_ANGLE, min(MAX_ANGLE, v))
    return car_id, clamp(roll), clamp(pitch)


class PhoneHub:
    """Tracks connected phones and mirrors the ESP32 MQTT contract."""

    def __init__(self, publish, prefix="carritos", clock=time.monotonic, timeout=2.0):
        self._publish = publish
        self._prefix = prefix
        self._clock = clock
        self._timeout = timeout
        self._lock = threading.Lock()
        self._last_seen: dict[str, float] = {}

    def on_tilt(self, car_id: str, roll: float, pitch: float) -> None:
        with self._lock:
            is_new = car_id not in self._last_seen
            self._last_seen[car_id] = self._clock()
        if is_new:
            self._publish(f"{self._prefix}/{car_id}/status", "online", True)
            print(f"phone connected: {car_id}")
        self._publish(f"{self._prefix}/{car_id}/tilt", json.dumps({"roll": roll, "pitch": pitch}), False)

    def expire(self) -> None:
        now = self._clock()
        with self._lock:
            gone = [i for i, seen in self._last_seen.items() if now - seen > self._timeout]
            for car_id in gone:
                del self._last_seen[car_id]
        for car_id in gone:
            self._publish(f"{self._prefix}/{car_id}/status", "offline", True)
            print(f"phone disconnected: {car_id}")


def make_server(hub: PhoneHub, host: str, port: int, page: bytes) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: ~25 POSTs/s over one TLS connection

        def _reply(self, status, body=b"", content_type=None):
            self.send_response(status)
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?")[0] == "/":
                self._reply(200, page, "text/html; charset=utf-8")
            else:
                self._reply(404)

        def do_POST(self):
            if self.path != "/tilt":
                return self._reply(404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._reply(400)
            if length > MAX_BODY:
                # Drain a bounded amount first: replying and closing while the client is still
                # sending makes some stacks (Windows) reset the connection before it reads the 413.
                self.rfile.read(min(length, MAX_DRAIN))
                self.close_connection = True
                return self._reply(413)
            parsed = parse_phone_payload(self.rfile.read(length))
            if parsed is None:
                return self._reply(400)
            hub.on_tilt(*parsed)
            self._reply(204)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            print(f"connection from {client_address[0]} dropped")  # e.g. cert rejected by the phone

    return Server((host, port), Handler)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # UDP connect sends nothing; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(ip: str) -> tuple[Path, Path]:
    cert_path, key_path = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "carritos-mc phone bridge")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address(ip)),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main() -> None:
    p = argparse.ArgumentParser(description="Bridge phone tilt (HTTPS page) to MQTT")
    p.add_argument("--host", default="localhost", help="MQTT broker host")
    p.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--prefix", default="carritos")
    p.add_argument("--https-port", type=int, default=8443)
    args = p.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{args.prefix}-phone-bridge")
    if args.user:
        client.username_pw_set(args.user, args.password)
    client.reconnect_delay_set(min_delay=1, max_delay=5)
    client.connect_async(args.host, args.port)
    client.loop_start()

    hub = PhoneHub(lambda topic, payload, retain=False: client.publish(topic, payload, retain=retain), args.prefix)
    server = make_server(hub, "0.0.0.0", args.https_port, PAGE_PATH.read_bytes())

    ip = lan_ip()
    cert, key = ensure_cert(ip)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    # Handshake runs in the handler thread, so a stalled client cannot block accept()
    server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)

    def watchdog():
        while True:
            time.sleep(0.5)
            hub.expire()

    threading.Thread(target=watchdog, daemon=True).start()

    print(f"MQTT broker: {args.host}:{args.port}")
    print(f"Open on your phone (same WiFi): https://{ip}:{args.https_port}/")
    print("Accept the certificate warning once. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for car_id in list(hub._last_seen):
            client.publish(f"{args.prefix}/{car_id}/status", "offline", retain=True).wait_for_publish(2)
        server.server_close()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
