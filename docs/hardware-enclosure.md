# Protective field enclosure

The prototype's three loose UART jumpers are suitable for bench validation,
not for a crash-cart bag. The field enclosure should protect both PCBs, make
the two USB roles impossible to confuse, transfer cable strain into the case,
and preserve access to Pico recovery without changing the electrical model.

## Reference concept

Use a nonconductive ABS, polycarbonate, or PETG enclosure with a screwed,
removable lid. An internal envelope near **80 × 65 × 25 mm** is a reasonable
first cardboard/printed mock-up, but it is not a production drawing: measure
the actual FT232H revision, USB plugs, header height, and cable bend radius
before cutting or printing.

The Raspberry Pi Pico W/WH PCB is **51 × 21 mm** with 2.1 mm mounting holes.
Raspberry Pi publishes the dimensioned drawing and warns that material—metal
in particular—near the wireless antenna keep-out reduces performance. Place
the antenna edge at a plastic case edge and keep it clear of the FT232H, cable
braid, copper tape, and metal hardware. See the official
[Pico W datasheet](https://datasheets.raspberrypi.com/picow/pico-w-datasheet.pdf)
and [physical specification](https://datasheets.raspberrypi.com/picow/pico-w-product-brief.pdf).

Adafruit's current Product 2264 revision uses USB-C and retains the older board
size, mounting-hole, and pinout geometry, but older FT232H boards used
Micro-USB. Identify the board in hand before designing the port cutout. Use the
official [product page](https://www.adafruit.com/product/2264) and
[fabrication resources](https://learn.adafruit.com/adafruit-ft232h-breakout/downloads)
rather than estimating boss positions from a photograph.

## Mechanical layout

- Mount the Pico on 5–6 mm M2 nylon standoffs with nylon washers.
- Derive FT232H bosses from its fabrication print or a direct measurement.
- Put both USB receptacles at case edges and label them permanently:

  - `TARGET HID — PICO`
  - `UCONSOLE UART — FT232H`

- Add internal cable clamps or panel-mount pass-throughs. A pull on either USB
  cable must load the enclosure, not the PCB connector.
- Recess the FT232H I2C switch so a cable cannot move it accidentally. It must
  remain **OFF for UART** and accessible after removing the lid.
- Preserve Pico BOOTSEL access through a recessed opening or the removable lid.
- Add small LED inspection windows; do not leave the boards exposed merely to
  see status LEDs.
- Cap or shroud unused power pins so a field jumper cannot land on `5V`, `3V`,
  `VBUS`, or `VSYS` by mistake.
- Do not pot the assembly or rely on hot glue as structural retention. Both
  boards, both USB cables, and the UART harness should remain replaceable.

## Three-wire service harness

UART mode uses exactly these conductors:

```text
FT232H D0 / TX  ->  Pico GP1 / UART0 RX
FT232H D1 / RX  <-  Pico GP0 / UART0 TX
FT232H GND      --- Pico GND
```

Adafruit documents D0 as UART TX and D1 as UART RX in its
[Serial UART guide](https://learn.adafruit.com/adafruit-ft232h-breakout/serial-uart).
Do not use C1 in place of D1, and do not enable the I2C switch.

Build the internal harness from 24–28 AWG stranded wire, approximately
50–100 mm long. A useful color scheme is white for FT232H TX, yellow for Pico
TX, and black for ground; avoid red so no signal conductor looks like power.
Use keyed housings or a locking three-position inline connector with
pre-crimped pigtails. Add a tie anchor or printed retention clip at both board
ends so vibration cannot lift a female header contact.

Put this map inside the lid in large type:

> THREE WIRES ONLY — NO POWER WIRE BETWEEN BOARDS

The Pico stays powered by the target's USB port. The FT232H stays powered by
the uConsole hub. Only the signal reference ground is shared.

## Prototype bill of materials

| Item | Quantity | Purpose |
| --- | ---: | --- |
| Nonconductive enclosure with screwed lid | 1 | PCB and contact protection |
| M2 nylon standoffs, screws, and washers | 4–8 | Isolated board retention |
| Short panel-mount USB extensions or cable glands | 2 | Port protection and strain transfer |
| 3-position keyed pre-crimp harness | 1 | Serviceable TX/RX/GND link |
| Adhesive tie mounts or printed cable clamps | 2–4 | Internal strain relief |
| Insulating header caps | As needed | Prevent accidental power-pin contact |
| Printed internal wiring/port labels | 1 set | Field-proof connection identity |

## Acceptance after assembly

With both USB cables disconnected, continuity-test D0→GP1, D1→GP0, and
GND→GND. Prove no continuity from the harness into either board's power rail or
into a fastener. Then repeat the software proof ladder:

1. stable FTDI and capture by-id identities;
2. Pico ready event, session ACK, ping ACK, and release ACK;
3. benign target-visible keyboard, pointer, and button actions;
4. a bounded cable-wiggle test with no UART generation change or USB reset;
5. a 30–60 minute closed-case soak while watching gateway reconnects, frame
   freshness, and UART timeouts; and
6. BOOTSEL, lid, harness, and cable serviceability without disturbing the
   opposite board.

The enclosure protects the electronics from handling; it is not galvanic
isolation. HDMI already couples the target and capture side. If deployments may
span materially different ground domains, evaluate isolated UART and isolated
video together rather than assuming a three-wire UART isolator solves the full
system boundary.
