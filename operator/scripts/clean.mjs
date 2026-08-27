import { rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const operatorRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

for (const outputName of ["dist", "dist-electron"]) {
  const target = path.join(operatorRoot, outputName);
  if (path.dirname(target) !== operatorRoot) {
    throw new Error(`refusing to clean unexpected path: ${target}`);
  }
  await rm(target, { recursive: true, force: true });
}
