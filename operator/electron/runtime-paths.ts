import os from "node:os";
import path from "node:path";

export interface OperatorSupportDirectoryOptions {
  configured?: string;
  platform?: NodeJS.Platform;
  homeDirectory?: string;
  xdgConfigHome?: string;
}

/**
 * Keep Electron and the MCP plugin on one canonical store. Electron's
 * app.getPath("userData") follows the package name and therefore resolves to
 * `noob-operator` in the packaged build, which is not the product namespace.
 */
export function operatorSupportDirectory(
  options: OperatorSupportDirectoryOptions = {},
): string {
  const configured = options.configured?.trim();
  if (configured) {
    if (!path.isAbsolute(configured) || configured.includes("\0")) {
      throw new Error("invalid_operator_support_directory");
    }
    return path.normalize(configured);
  }

  const platform = options.platform ?? process.platform;
  const homeDirectory = options.homeDirectory ?? os.homedir();
  if (!path.isAbsolute(homeDirectory) || homeDirectory.includes("\0")) {
    throw new Error("invalid_operator_home_directory");
  }
  if (platform === "darwin") {
    return path.join(homeDirectory, "Library", "Application Support", "N.O.O.B");
  }

  const configuredXdg = options.xdgConfigHome?.trim();
  const configRoot = configuredXdg || path.join(homeDirectory, ".config");
  if (!path.isAbsolute(configRoot) || configRoot.includes("\0")) {
    throw new Error("invalid_operator_config_directory");
  }
  return path.join(path.normalize(configRoot), "noob");
}
