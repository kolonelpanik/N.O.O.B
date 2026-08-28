#!/usr/bin/env node
import { createServer } from "./server.js";
import { NoobRuntime } from "./runtime.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import type { Request, Response } from "express";

const runtime = new NoobRuntime();

async function runStdio(): Promise<void> {
  const server = createServer(runtime);
  await server.connect(new StdioServerTransport());
  console.error("N.O.O.B. MCP server ready on stdio");
  const close = async () => {
    await server.close();
    await runtime.close();
  };
  process.once("SIGINT", () => void close().finally(() => process.exit(0)));
  process.once("SIGTERM", () => void close().finally(() => process.exit(0)));
}

function allowedOrigins(): Set<string> {
  return new Set((process.env.NOOB_MCP_ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean));
}

async function runHttp(): Promise<void> {
  const bearer = process.env.NOOB_MCP_HTTP_TOKEN?.trim() ?? "";
  if (!/^[\x21-\x7e]{32,256}$/.test(bearer)) {
    throw new Error("NOOB_MCP_HTTP_TOKEN with 32-256 visible ASCII characters is required for HTTP mode");
  }
  const origins = allowedOrigins();
  const port = Number.parseInt(process.env.NOOB_MCP_PORT ?? "3099", 10);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) throw new Error("invalid NOOB_MCP_PORT");
  const app = createMcpExpressApp({ host: "127.0.0.1" });
  app.all("/mcp", async (req: Request, res: Response) => {
    const authorization = req.get("authorization") ?? "";
    const origin = req.get("origin");
    if (authorization !== `Bearer ${bearer}`) {
      res.status(401).json({ error: "unauthorized" });
      return;
    }
    if (origin && (origins.size === 0 || !origins.has(origin))) {
      res.status(403).json({ error: "origin_not_allowed" });
      return;
    }
    const server = createServer(runtime);
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on("close", () => {
      void transport.close();
      void server.close();
    });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch {
      if (!res.headersSent) {
        res.status(500).json({ jsonrpc: "2.0", error: { code: -32603, message: "Internal server error" }, id: null });
      }
    }
  });
  const listener = app.listen(port, "127.0.0.1", () => {
    console.error(`N.O.O.B. MCP server ready on http://127.0.0.1:${port}/mcp`);
  });
  const close = async () => {
    listener.close();
    await runtime.close();
  };
  process.once("SIGINT", () => void close().finally(() => process.exit(0)));
  process.once("SIGTERM", () => void close().finally(() => process.exit(0)));
}

async function main(): Promise<void> {
  if (process.argv.includes("--http")) await runHttp();
  else await runStdio();
}

main().catch(async (error: unknown) => {
  console.error(error instanceof Error ? error.message : "N.O.O.B. MCP startup failed");
  await runtime.close();
  process.exit(1);
});
