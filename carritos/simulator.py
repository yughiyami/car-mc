"""Fake ESP32 controllers: publishes tilt over MQTT so you can test without hardware."""
import argparse
import json
import math
import time

import paho.mqtt.client as mqtt


def make_client(args, car_id: str) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=car_id)
    if args.user:
        client.username_pw_set(args.user, args.password)
    client.will_set(f"{args.prefix}/{car_id}/status", "offline", retain=True)
    client.connect(args.host, args.port)
    client.loop_start()
    client.publish(f"{args.prefix}/{car_id}/status", "online", retain=True)
    return client


def main() -> None:
    p = argparse.ArgumentParser(description="Simulate N ESP32 tilt controllers")
    p.add_argument("--cars", type=int, default=4)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--prefix", default="carritos")
    p.add_argument("--rate", type=float, default=25.0, help="messages per second per car")
    args = p.parse_args()

    ids = [f"sim-{i + 1}" for i in range(args.cars)]
    clients = {car_id: make_client(args, car_id) for car_id in ids}
    print(f"{args.cars} simulated cars -> {args.host}:{args.port} (Ctrl+C to stop)")

    start = time.monotonic()
    try:
        while True:
            t = time.monotonic() - start
            for i, (car_id, client) in enumerate(clients.items()):
                phase = i * 1.3
                payload = {
                    "pitch": round(18 + 12 * math.sin(t * 0.4 + phase), 1),   # mostly forward
                    "roll": round(28 * math.sin(t * 0.7 + phase * 1.7), 1),   # weaving
                }
                client.publish(f"{args.prefix}/{car_id}/tilt", json.dumps(payload))
            time.sleep(1 / args.rate)
    except KeyboardInterrupt:
        pass
    finally:
        for car_id, client in clients.items():
            client.publish(f"{args.prefix}/{car_id}/status", "offline", retain=True).wait_for_publish(2)
            client.loop_stop()
            client.disconnect()


if __name__ == "__main__":
    main()
