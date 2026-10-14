"""MQTT side of the arena. Channels (topic prefixes) can be added and removed live."""
import threading
import uuid

import paho.mqtt.client as mqtt

from .core import Fleet, handle_message, normalize_channel


class Link:
    """Runs in paho's network thread and only talks to the Fleet."""

    def __init__(self, fleet: Fleet, host: str, port: int, channels, user=None, password=None):
        self.fleet = fleet
        self.host = host
        self.port = port
        self.state = "connecting"
        self.messages = 0
        self._lock = threading.Lock()
        self._channels: list[str] = []
        self._channel_set: frozenset = frozenset()
        for channel in channels:
            self._add(channel)

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"carritos-arena-{uuid.uuid4().hex[:8]}")
        if user:
            self.client.username_pw_set(user, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)
        self.client.connect_async(host, port)
        self.client.loop_start()

    # -- channels --

    @property
    def channels(self) -> list:
        with self._lock:
            return list(self._channels)

    def _add(self, value):
        channel = normalize_channel(value)
        if channel is None:
            return None
        with self._lock:
            if channel in self._channels:
                return channel
            self._channels.append(channel)
            self._channel_set = frozenset(self._channels)
        return channel

    def add_channel(self, value):
        """Subscribe to a new channel. Returns the normalized name, or None if invalid."""
        channel = self._add(value)
        if channel and self.state == "connected":
            self._subscribe(channel)
        return channel

    def remove_channel(self, channel: str) -> None:
        with self._lock:
            if channel not in self._channels:
                return
            self._channels.remove(channel)
            self._channel_set = frozenset(self._channels)
        if self.state == "connected":
            self.client.unsubscribe([f"{channel}/+/tilt", f"{channel}/+/status"])
        self.fleet.drop_channel(channel)

    def _subscribe(self, channel: str) -> None:
        self.client.subscribe([(f"{channel}/+/tilt", 0), (f"{channel}/+/status", 0)])

    # -- paho callbacks --

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.state = f"refused ({reason_code})"
            return
        self.state = "connected"
        for channel in self.channels:
            self._subscribe(channel)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.state = "reconnecting"

    def _on_message(self, client, userdata, msg):
        self.messages += 1
        handle_message(self.fleet, self._channel_set, msg.topic, msg.payload)

    def close(self) -> None:
        # loop_stop() joins the network thread, which can sit ~2 s inside a refused connect on
        # Windows; do it off-thread so closing the window is instant.
        self.client.disconnect()
        threading.Thread(target=self.client.loop_stop, daemon=True).start()
