# Interface refresh verification

The arena artwork is generated with the built-in image model and shipped in the Python package. The desktop and phone controller retain their existing MQTT contract.

## Checks (2026-10-14)

- `.venv/Scripts/python.exe -m pytest -q`: **130 passed**.
- `git diff --check`: passed.
- `.venv/Scripts/python.exe -m pip wheel . --no-deps --wheel-dir dist`: built successfully; verified the wheel contains `carritos/assets/arena.png` and `carritos/web/phone_controller.html`.
- Desktop runtime: headless pygame at 1360×820 and 640×480, demo vehicles and selected inspector; captures in `interface.png` and `interface-compact.png`. Background caching, keyboard toggles, focus and compact inspector scrolling have regression tests.
- Browser runtime: Chromium at 390px and 320px, no horizontal overflow, 48px-high action buttons. Synthetic orientation and mocked HTTP: first sample centers; second sample reports pitch 20.0°; recalibrate returns 0.0°; Stop restores the start action. Node VM tests also cover permission rejection, insecure context, stale readings, invalid samples, corrupt stored IDs and late responses.
- Physical phone sensors, ESP32 hardware and a live MQTT broker were **not** tested in this refresh.
- Receipt-driven development: **disabled/unmanaged**, decided by global configuration. No review receipt is claimed.

## Rollback boundary

- Desktop unit: revert desktop app/widgets/rendering, their smoke tests, arena asset and package-data addition together. Desktop screenshots and corresponding README section document this unit.
- Phone unit: revert the phone HTML and browser contract tests together. Node CI setup and phone screenshot/README instructions accompany this unit.

The original arena renderer remains a fallback if artwork cannot be loaded. No additional runtime dependency or image-generation API key is required.
