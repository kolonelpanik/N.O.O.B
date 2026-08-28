import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GatewayConfigView, GatewayDeviceCandidate } from "../../shared/gateway-contract";
import { noobApi } from "../api/noob-client";
import { DevicePicker } from "./DevicePicker";

const config: GatewayConfigView = {
  gatewayUrl: "http://127.0.0.1:18765",
  gatewayLabel: "uConsole",
  streamUrl: "noob://gateway/stream",
  tokenConfigured: false,
  connectionMode: "fixed",
  currentDeviceId: null,
};

const fingerprint = `SHA256:${"A".repeat(43)}`;
const pairingCode = "1234-5678";
const candidate: GatewayDeviceCandidate = {
  candidateId: `candidate_${"a".repeat(24)}`,
  instanceName: "N.O.O.B. at 192.168.50.83",
  address: "192.168.50.83",
  sshPort: 22,
  hostKeyFingerprint: fingerprint,
  pairingCode,
  product: "N.O.O.B.",
  version: "0.2.0",
  capabilities: ["target-video", "hid"],
  expiresAt: "2026-08-27T20:00:00.000Z",
  source: "manual",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DevicePicker", () => {
  it("requires an exact independently displayed pairing code and keeps the full fingerprint in advanced details", async () => {
    vi.spyOn(noobApi, "listDevices").mockResolvedValue({ devices: [], currentDeviceId: null });
    const probe = vi.spyOn(noobApi, "probeDevice").mockResolvedValue(candidate);
    const pair = vi.spyOn(noobApi, "pairAndConnectDevice").mockResolvedValue({
      config: { ...config, gatewayUrl: "http://127.0.0.1:23456", connectionMode: "ssh-tunnel", currentDeviceId: "noob_test" },
      device: {
        deviceId: "noob_test",
        profileName: candidate.instanceName,
        address: candidate.address,
        sshPort: candidate.sshPort,
        hostKeyFingerprint: fingerprint,
        capabilities: candidate.capabilities,
        createdAt: "2026-08-27T20:00:00.000Z",
      },
    });
    const onConnected = vi.fn();
    render(<DevicePicker open currentConfig={config} onClose={vi.fn()} onConnected={onConnected} />);

    fireEvent.change(screen.getByLabelText("Device address"), { target: { value: "192.168.50.83" } });
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    await waitFor(() => expect(probe).toHaveBeenCalledWith("192.168.50.83", 22));
    expect(await screen.findByText(fingerprint)).toBeInTheDocument();
    expect(screen.getByText(pairingCode)).toBeInTheDocument();
    expect(screen.getByText("Advanced SSH fingerprint")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pair and connect" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Confirm pairing code"), {
      target: { value: "9999-9999" },
    });
    expect(screen.getByRole("button", { name: "Pair and connect" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Confirm pairing code"), {
      target: { value: pairingCode },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pair and connect" }));

    await waitFor(() => expect(pair).toHaveBeenCalledWith(
      candidate.candidateId,
      fingerprint,
      candidate.instanceName,
    ));
    expect(onConnected).toHaveBeenCalledWith(expect.objectContaining({ connectionMode: "ssh-tunnel" }));
  });

  it("labels discovery as untrusted and inspects a candidate before it can be reviewed", async () => {
    vi.spyOn(noobApi, "listDevices").mockResolvedValue({ devices: [], currentDeviceId: null });
    const unresolved = { ...candidate, source: "discovery" as const, hostKeyFingerprint: null, pairingCode: null };
    vi.spyOn(noobApi, "discoverDevices").mockResolvedValue({ candidates: [unresolved] });
    const inspect = vi.spyOn(noobApi, "inspectDevice").mockResolvedValue({
      ...candidate,
      source: "discovery",
    });
    render(<DevicePicker open currentConfig={config} onClose={vi.fn()} onConnected={vi.fn()} />);

    expect(screen.getByText(/Discovery is an untrusted hint/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Scan network" }));
    expect(await screen.findByText("N.O.O.B. at 192.168.50.83")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));

    await waitFor(() => expect(inspect).toHaveBeenCalledWith(unresolved.candidateId));
    expect(await screen.findByText(fingerprint)).toBeInTheDocument();
  });
});
