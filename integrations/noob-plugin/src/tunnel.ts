import { randomBytes } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { stat } from "node:fs/promises";
import net from "node:net";
import { addKnownHost, loadStore, readProtectedText, runtimePaths } from "./config.js";
import { GatewayApi } from "./gateway.js";
import { assertDeviceId } from "./policy.js";
import type { ConnectionView, DeviceProfile } from "./types.js";

const CONNECT_TIMEOUT_MS = 8_000;
const TOKEN_PATTERN = /^[\x21-\x7e]{32,256}$/;

interface LiveTunnel {
  view: ConnectionView;
  child: ChildProcess;
  api: GatewayApi;
}

async function ownerOnlyRegularFile(file: string): Promise<void> {
  const info = await stat(file);
  if (!info.isFile()) throw new Error("connection_file_not_regular");
  if ((info.mode & 0o077) !== 0) throw new Error("connection_file_permissions_too_open");
}

async function freeLoopbackPort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

function connectionId(): string {
  return `conn_${randomBytes(18).toString("base64url")}`;
}

async function waitForGateway(api: GatewayApi, child: ChildProcess): Promise<void> {
  const deadline = Date.now() + CONNECT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("ssh_tunnel_exited");
    try {
      await api.json("/api/v1/status", {}, 750);
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  throw new Error("gateway_tunnel_timeout");
}

export class TunnelManager {
  private readonly tunnels = new Map<string, LiveTunnel>();

  async listViews(): Promise<Map<string, ConnectionView>> {
    const views = new Map<string, ConnectionView>();
    for (const [deviceId, tunnel] of this.tunnels) {
      views.set(deviceId, {
        ...tunnel.view,
        connection_state: tunnel.child.exitCode === null ? "connected" : "degraded",
      });
    }
    return views;
  }

  async connect(deviceId: string): Promise<ConnectionView> {
    assertDeviceId(deviceId);
    const current = this.tunnels.get(deviceId);
    if (current && current.child.exitCode === null) return current.view;
    if (current) await this.close(deviceId);

    const store = await loadStore();
    const profile = store.devices.find((entry) => entry.device_id === deviceId);
    if (!profile) throw new Error("device_not_registered");
    return await this.connectProfile(profile);
  }

  private async connectProfile(profile: DeviceProfile): Promise<ConnectionView> {
    const paths = runtimePaths();
    await ownerOnlyRegularFile(paths.identity_file);
    await ownerOnlyRegularFile(paths.known_hosts_file);
    await addKnownHost(profile);
    const bearer = await readProtectedText(paths.gateway_token_file, 32, 256);
    if (!TOKEN_PATTERN.test(bearer)) throw new Error("gateway_token_invalid");
    const localPort = await freeLoopbackPort();
    const args = [
      "-N",
      "-T",
      "-p", String(profile.ssh_port),
      "-i", paths.identity_file,
      "-L", `127.0.0.1:${localPort}:127.0.0.1:${profile.gateway_port}`,
      "-o", "BatchMode=yes",
      "-o", "IdentitiesOnly=yes",
      "-o", "IdentityAgent=none",
      "-o", "StrictHostKeyChecking=yes",
      "-o", `UserKnownHostsFile=${paths.known_hosts_file}`,
      "-o", "ExitOnForwardFailure=yes",
      "-o", "ConnectTimeout=5",
      "-o", "ServerAliveInterval=15",
      "-o", "ServerAliveCountMax=2",
      `${profile.ssh_user}@${profile.address}`,
    ];
    const child = spawn("ssh", args, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: false,
      env: { PATH: process.env.PATH ?? "/usr/bin:/bin:/usr/sbin:/sbin" },
    });
    child.stdout?.resume();
    child.stderr?.resume();
    const view: ConnectionView = {
      device_id: profile.device_id,
      connection_id: connectionId(),
      connection_state: "connected",
      connected_at: new Date().toISOString(),
      local_port: localPort,
    };
    const api = new GatewayApi(`http://127.0.0.1:${localPort}`, bearer);
    const live: LiveTunnel = { view, child, api };
    this.tunnels.set(profile.device_id, live);
    child.once("exit", () => {
      const selected = this.tunnels.get(profile.device_id);
      if (selected === live) selected.view.connection_state = "degraded";
    });
    try {
      await waitForGateway(api, child);
      return view;
    } catch (error) {
      await this.close(profile.device_id);
      throw error;
    }
  }

  api(deviceId: string): { api: GatewayApi; view: ConnectionView } {
    assertDeviceId(deviceId);
    const tunnel = this.tunnels.get(deviceId);
    if (!tunnel || tunnel.child.exitCode !== null) throw new Error("device_not_connected");
    return { api: tunnel.api, view: tunnel.view };
  }

  async close(deviceId: string): Promise<void> {
    const tunnel = this.tunnels.get(deviceId);
    if (!tunnel) return;
    this.tunnels.delete(deviceId);
    if (tunnel.child.exitCode !== null) return;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        if (tunnel.child.exitCode === null) tunnel.child.kill("SIGKILL");
      }, 1_000);
      tunnel.child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      tunnel.child.kill("SIGTERM");
    });
  }

  async closeAll(): Promise<void> {
    await Promise.all([...this.tunnels.keys()].map((deviceId) => this.close(deviceId)));
  }
}
