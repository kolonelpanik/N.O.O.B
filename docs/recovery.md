# Recovery

## Emergency input release

Use the authenticated `POST /api/v1/release-all` endpoint. It does not require
the caller to own the current controller lease. If the gateway is unreachable,
disconnect the Pico's Micro-USB cable from the target; USB disconnect releases
all host-side HID state. The Pico firmware also releases all input after its
serial watchdog expires.

When the built-in uConsole controls are armed, press **Ctrl+Alt+Esc** to disarm
them locally. The chord is reserved and is not forwarded to the target. A
successful disarm releases the evdev grabs and requests Pico `release_all`.
If the gateway or serial lane is already unavailable, unplugging the Pico from
the target remains the final physical release mechanism.

## Pico firmware recovery

Initial installation and recovery cannot be performed through the FT232H UART.
The Pico must enter its RP2040 BOOTSEL loader:

1. Unplug the Pico Micro-USB cable.
2. Confirm the FT232H I2C switch is **OFF**.
3. Hold Pico **BOOTSEL**, reconnect Micro-USB to the programming Mac, and release
   BOOTSEL after `RPI-RP2` mounts.
4. Run `scripts/install_pico.sh` with the pinned Pico W/WH CircuitPython UF2 and
   matching major-version library bundle.
5. Require the `CIRCUITPY` volume, inspect `boot_out.txt`, then confirm a UART
   `ready` event before reconnecting operational control.

Development firmware intentionally leaves USB storage and CDC enabled. They
must not be disabled until BOOTSEL recovery and all stuck-input tests have been
demonstrated and documented.

## uConsole gateway recovery

The service starts degraded when either USB device is absent and reconnects
without accepting input. Use these read-only checks first:

```bash
systemctl status noob-gateway.service --no-pager
journalctl -u noob-gateway.service -n 100 --no-pager
ls -l /dev/serial/by-id /dev/v4l/by-id /dev/input/by-id
id noob
curl --fail --silent http://127.0.0.1:8765/healthz
```

Never substitute an arbitrary `/dev/videoN` or `/dev/ttyUSBN` node merely to
make readiness green. Verify its durable USB identity and capabilities.

The local-control evdev readers reconnect after a device returns but remain
disarmed. Re-authenticate and arm only after both stable links are ready. Never
substitute `/dev/input/eventN`: those names can change across reboots or replugs
and are rejected by configuration validation.

## Wiring recovery

Disconnect both USB power sources before changing Dupont wires. The only valid
cross-board wiring is:

```text
FT232H D0/TX -> Pico GP1/UART0 RX
FT232H D1/RX <- Pico GP0/UART0 TX
FT232H GND   -- Pico GND
```

Do not connect FT232H 5V or 3V to any Pico power pin. These are 3.3 V TTL UART
signals, not a DB9/RS-232 electrical interface.
