# N.O.O.B operator design system

Source of truth: `design/noob-operator-primary-concept.png` at 1586 × 992.

## Product shape

The operator app is one calm control surface, not a collection of dashboards.
The target video is the dominant object. A narrow right rail owns every action
that can affect the target. A bottom proof rail exposes the end-to-end transport
chain. Input capture is always explicit and emergency release is always visible.

## Allowed primary-screen copy

- N.O.O.B
- NEVER OUT OF BOUNDS
- uConsole · 192.0.2.83
- Eyes · Live
- Hands · Ready
- Control · Available
- Live target
- Control ownership
- You do not have control.
- Take control
- Your local keyboard and mouse are not sent to the target until you take control.
- Mode
- Human
- Agent
- Input controls
- Capture keyboard
- Capture pointer
- Pointer lock
- Relative mouse on target
- Type text
- Send
- Agent channel
- API ready
- Last action
- RELEASE ALL INPUT
- Session
- Video
- UART
- HID
- Target
- Loopback only · SSH tunnel · No recording

Dynamic states may replace the status words `Live`, `Ready`, `Available`, and
the ownership sentence with accurate degraded, connecting, claimed, or error
states. No promotional copy or invented metrics belongs on this surface.

## Layout

- Native reference: 1586 × 992; verify at this size and at the current laptop
  viewport.
- Header: 54–58 px, quiet single band.
- Main region: video takes approximately 76% of width; control rail 24%.
- Video: stable 16:9 content area, one-pixel frame, no color overlay.
- Right rail: 16 px inner gutter, vertically grouped controls, emergency action
  anchored near the lower edge rather than hidden by scroll.
- Proof rail: approximately 132 px high, five open modules connected by one
  signal line. It may horizontally scroll on narrow screens rather than wrap
  into a card grid.
- Footer: 30–34 px status strip.
- At narrow widths the control rail becomes a right-side drawer; video remains
  first and emergency release stays fixed and reachable.

## Tokens

```css
--bg: #080d11;
--surface: #0d1419;
--surface-raised: #111a20;
--surface-input: #10181e;
--border: #27343b;
--border-strong: #35454d;
--text: #f3f7f8;
--text-muted: #8c9aa2;
--signal: #20c7c9;
--signal-bright: #39e1df;
--healthy: #29d17d;
--danger: #e5242d;
--danger-strong: #ff3a42;
--focus: #53e5e0;
--radius-sm: 7px;
--radius-md: 10px;
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 24px;
--space-6: 32px;
--motion-fast: 120ms;
--motion-normal: 180ms;
```

The palette is true cool graphite, not warm gray. Cyan/teal means signal or
selected state. Green means a currently verified healthy state. Red is reserved
for release-all and critical failure.

## Typography

- UI family: Inter or system `-apple-system`, with deliberate sizes and line
  heights on every control.
- Technical details: SFMono-Regular or `ui-monospace`.
- Brand: 18 px / 700 / 0.12 em tracking.
- Region heading: 14 px / 650.
- Body/control: 13–14 px / 500–600.
- Detail/status: 11–12 px / 500.
- Nothing in the primary workflow may render below 11 px.

## Components

- `AppShell`: header, workspace, proof rail, footer.
- `LiveTarget`: frame freshness, focus outline, aspect-preserving video image.
- `ConnectionStatus`: text plus one small semantic dot; no decorative pills.
- `ControlOwnership`: claim/release state and lease countdown only when real.
- `ModeSwitch`: Human and Agent, two equal text buttons.
- `InputControls`: keyboard capture, pointer capture/lock, explicit text submit.
- `AgentChannel`: API readiness and last action metadata without typed content.
- `EmergencyRelease`: full-width red action, reachable without a lease.
- `ProofRail`: Session → Video → UART → HID → Target, truthful state per layer.

## Icons and interaction

Use one consistent 1.6–1.8 px rounded outline icon family. Required metaphors:
lock/control ownership, keyboard, crosshair/pointer, agent/robot, warning
triangle, settings, shield, and transport nodes. Keep icons 16–20 px and aligned
to the control-text baseline.

Visible focus uses a two-pixel cyan outline with a two-pixel offset. Pointer
capture requires an explicit click and ends on Escape, window blur, page hide,
WebSocket close, or lease loss. Reduced-motion mode disables signal-line motion.

## Intentional implementation differences

The concept contains illustrative target/session values. Production code must
replace them with live data or an em dash; it must not hard-code a fake session
ID, timestamp, resolution, port, target power state, or secure-channel claim.
