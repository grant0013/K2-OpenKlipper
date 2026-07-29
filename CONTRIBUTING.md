# Contributing

This project welcomes protocol research, code, documentation, and supervised
hardware validation.

## Before testing

- Read `docs/PROJECT-STATUS.md`, `docs/PRTOUCH.md`, and
  `docs/CFS-PROTOCOL.md`.
- Keep a known-good stock rollback and confirm that you can use it.
- Never test new homing, heater, cutter, or CFS actuation unattended.
- Change one motor or motion variable at a time.
- Do not attach unknown-voltage CFS hardware to a controller.
- Remove credentials, tokens, serial numbers, Wi-Fi details, and private
  network information from logs.

## Reporting results

Use the hardware validation issue template. Include versions, exact config
changes, the command or print phase, expected and observed results, sanitised
logs, and any recovery action. A failed result is useful when it is
reproducible and clearly documented.

## Code changes

1. Create a branch from `main`.
2. Keep protocol transport separate from printer-specific motion where
   practical.
3. Preserve safety gates such as `CONFIRM=1`, homed-axis checks, temperature
   checks, position validation, and best-effort CFS stop.
4. Add or update tests for protocol and state-machine changes.
5. Run:

   ```text
   python -m unittest discover -s tests -v
   python -m compileall -q klippy
   ```

6. Explain live hardware testing separately from offline tests.

Do not include proprietary firmware binaries, private Creality packages, or
captured personal data in a contribution.
