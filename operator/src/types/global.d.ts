import type { NoobBridge } from "../../shared/gateway-contract";

declare global {
  interface Window {
    noob?: NoobBridge;
  }
}

export {};
