import { afterEach, describe, expect, it } from "vitest";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer } from "./server.js";
import { NoobRuntime } from "./runtime.js";

const runtimes: NoobRuntime[] = [];

afterEach(async () => {
  await Promise.all(runtimes.splice(0).map((runtime) => runtime.close()));
});

describe("MCP contract", () => {
  it("publishes the bounded cross-client tool surface with strict schemas", async () => {
    const runtime = new NoobRuntime();
    runtimes.push(runtime);
    const server = createServer(runtime);
    const client = new Client({ name: "contract-test", version: "1.0.0" });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
    const listed = await client.listTools();
    const names = listed.tools.map((tool) => tool.name).sort();

    expect(names).toHaveLength(25);
    expect(names).toContain("noob_open_console");
    expect(names).toContain("noob_emergency_release_all");
    expect(names).toContain("noob_get_recording_status");
    expect(names).toContain("noob_get_clip_frame");
    expect(names).not.toContain("shell");
    expect(names).not.toContain("delete_media");
    for (const tool of listed.tools) {
      expect(tool.inputSchema.additionalProperties).toBe(false);
      expect(tool.description?.length).toBeGreaterThan(20);
      expect(tool.annotations).toBeDefined();
    }

    const open = listed.tools.find((tool) => tool.name === "noob_open_console");
    expect(open?._meta?.ui).toMatchObject({ resourceUri: "ui://noob/operator-console/v1/index.html" });
    const poll = listed.tools.find((tool) => tool.name === "noob_widget_poll_frame");
    expect(poll?._meta?.ui).toMatchObject({ visibility: ["app"] });
    const type = listed.tools.find((tool) => tool.name === "noob_type_text");
    expect(type?.annotations).toMatchObject({ destructiveHint: true, idempotentHint: false });
    const media = listed.tools.find((tool) => tool.name === "noob_list_media");
    expect(media?.inputSchema.properties?.cursor).toMatchObject({ maxLength: 128 });
    const stop = listed.tools.find((tool) => tool.name === "noob_stop_recording");
    expect(stop?.description).toContain("Cancels");
    expect(stop?.description).not.toContain("finalizes");
    const clipFrame = listed.tools.find((tool) => tool.name === "noob_get_clip_frame");
    expect(clipFrame?.inputSchema.properties?.frame_index).toMatchObject({
      minimum: 0,
      maximum: 149,
    });

    await client.close();
    await server.close();
  });
});
