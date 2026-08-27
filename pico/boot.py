"""N.O.O.B Pico WH USB interface policy.

Development mode intentionally leaves CIRCUITPY storage and the CDC console
enabled. They are disabled only after the recovery path and all HID watchdog
tests have been accepted.
"""

import usb_hid

try:
    import usb_midi

    usb_midi.disable()
except (ImportError, AttributeError):
    pass

usb_hid.enable(
    (usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE),
)
