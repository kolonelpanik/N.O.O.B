import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  GatewayConfigView,
  GatewayInputCommand,
  GatewayStatus,
  VideoMode,
} from "../../shared/gateway-contract";
import { noobApi, OperatorApiError } from "../api/noob-client";
import { BoundedInputFifo } from "../input/input-fifo";
import { describeInput, mapKeyboardEvent } from "../input/input-mapping";

export type OperatorMode = "human" | "agent";
export type ConnectionState = "connecting" | "live" | "degraded" | "unauthenticated";

const DEFAULT_CONFIG: GatewayConfigView = {
  gatewayUrl: "http://127.0.0.1:18765",
  gatewayLabel: "uConsole · 192.0.2.83",
  streamUrl: "",
  tokenConfigured: false,
  connectionMode: "fixed",
  currentDeviceId: null,
};

function releasePointerLock(): void {
  if (document.pointerLockElement != null && typeof document.exitPointerLock === "function") {
    void document.exitPointerLock();
  }
}

function isAuthenticationFailure(error: unknown): boolean {
  return error instanceof OperatorApiError &&
    (error.status === 401 || error.code === "token_required" || error.code === "invalid_token");
}

export interface OperatorController {
  config: GatewayConfigView;
  status: GatewayStatus | null;
  connection: ConnectionState;
  authenticated: boolean;
  authDialogOpen: boolean;
  authError: string | null;
  sessionStartedAt: Date | null;
  claimed: boolean;
  leaseRemainingMs: number | null;
  mode: OperatorMode;
  keyboardCapture: boolean;
  pointerCapture: boolean;
  pointerLocked: boolean;
  localInputBusy: boolean;
  localInputError: string | null;
  videoModes: VideoMode[];
  videoModeBusy: boolean;
  videoModeError: string | null;
  lastAction: string;
  streamGeneration: number;
  setAuthDialogOpen(open: boolean): void;
  adoptConnection(config: GatewayConfigView): Promise<void>;
  bootstrapToken(token: string): Promise<boolean>;
  clearToken(): Promise<void>;
  claimControl(): Promise<void>;
  releaseControl(): Promise<boolean>;
  emergencyRelease(): Promise<void>;
  armLocalInput(): Promise<void>;
  disarmLocalInput(): Promise<boolean>;
  switchVideoMode(modeId: string): Promise<void>;
  setMode(mode: OperatorMode): void;
  toggleKeyboardCapture(): void;
  togglePointerCapture(): void;
  requestPointerLock(element: HTMLElement): Promise<void>;
  sendInput(command: GatewayInputCommand, recordAction?: boolean): Promise<boolean>;
}

export function useOperatorController(): OperatorController {
  const [config, setConfig] = useState<GatewayConfigView>(DEFAULT_CONFIG);
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [authenticated, setAuthenticated] = useState(false);
  const [authDialogOpen, setAuthDialogOpen] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [sessionStartedAt, setSessionStartedAt] = useState<Date | null>(null);
  const [claimed, setClaimed] = useState(false);
  const [leaseExpiresAt, setLeaseExpiresAt] = useState<number | null>(null);
  const [leaseRemainingMs, setLeaseRemainingMs] = useState<number | null>(null);
  const [mode, setModeState] = useState<OperatorMode>("human");
  const [keyboardCapture, setKeyboardCapture] = useState(false);
  const [pointerCapture, setPointerCapture] = useState(false);
  const [pointerLocked, setPointerLocked] = useState(false);
  const [localInputBusy, setLocalInputBusy] = useState(false);
  const [localInputError, setLocalInputError] = useState<string | null>(null);
  const [videoModes, setVideoModes] = useState<VideoMode[]>([]);
  const [videoModeBusy, setVideoModeBusy] = useState(false);
  const [videoModeError, setVideoModeError] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState("—");
  const [streamGeneration, setStreamGeneration] = useState(0);

  const authenticatedRef = useRef(false);
  const claimedRef = useRef(false);
  const claimStartedAtRef = useRef(0);
  const leaseTtlRef = useRef(0);
  const keyboardCaptureRef = useRef(false);
  const pointerCaptureRef = useRef(false);
  const modeRef = useRef<OperatorMode>("human");
  const emergencyInFlightRef = useRef<Promise<void> | null>(null);
  const videoModeInFlightRef = useRef(false);
  const ownershipTransitionRef = useRef<"claim" | "release" | "arm" | "disarm" | null>(null);
  const statusMutationGenerationRef = useRef(0);
  const pressedKeysRef = useRef(new Set<string>());
  const inputFifoRef = useRef<BoundedInputFifo | null>(null);
  if (inputFifoRef.current === null) inputFifoRef.current = new BoundedInputFifo(128);

  const applyLocalRelease = useCallback((action = "Input state released") => {
    inputFifoRef.current?.invalidate();
    claimedRef.current = false;
    keyboardCaptureRef.current = false;
    pointerCaptureRef.current = false;
    pressedKeysRef.current.clear();
    setClaimed(false);
    setKeyboardCapture(false);
    setPointerCapture(false);
    setPointerLocked(false);
    setLeaseExpiresAt(null);
    setLeaseRemainingMs(null);
    setLastAction(action);
    releasePointerLock();
  }, []);

  const loseControl = useCallback(
    (action = "Control lost") => {
      const ownedControl = claimedRef.current;
      applyLocalRelease(action);
      if (authenticatedRef.current && ownedControl) {
        void noobApi.release().catch(() => undefined);
      }
    },
    [applyLocalRelease],
  );

  const installStatus = useCallback((nextStatus: GatewayStatus) => {
    setStatus(nextStatus);
    setLocalInputError(null);
    setConnection("live");
    setSessionStartedAt((current) => current ?? new Date());
    setStreamGeneration(nextStatus.video.generation);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      try {
        const nextConfig = await noobApi.getConfig();
        if (cancelled) return;
        setConfig(nextConfig);
        if (!nextConfig.tokenConfigured) {
          setConnection("unauthenticated");
          setAuthDialogOpen(true);
          return;
        }
        const nextStatus = await noobApi.status();
        if (cancelled) return;
        authenticatedRef.current = true;
        setAuthenticated(true);
        installStatus(nextStatus);
      } catch (error) {
        if (cancelled) return;
        authenticatedRef.current = false;
        setAuthenticated(false);
        setConnection(isAuthenticationFailure(error) ? "unauthenticated" : "degraded");
        setAuthDialogOpen(true);
      }
    };
    void initialize();
    return () => {
      cancelled = true;
    };
  }, [installStatus]);

  const adoptConnection = useCallback(
    async (nextConfig: GatewayConfigView): Promise<void> => {
      const generation = ++statusMutationGenerationRef.current;
      authenticatedRef.current = false;
      applyLocalRelease("Device route changed");
      setConfig(nextConfig);
      setStatus(null);
      setAuthenticated(false);
      setConnection("connecting");
      setAuthError(null);
      setAuthDialogOpen(false);
      setSessionStartedAt(null);
      setVideoModes([]);
      setVideoModeError(null);
      setStreamGeneration(0);

      if (!nextConfig.tokenConfigured) {
        if (generation !== statusMutationGenerationRef.current) return;
        setConnection("unauthenticated");
        setAuthDialogOpen(true);
        return;
      }

      try {
        const nextStatus = await noobApi.status();
        if (generation !== statusMutationGenerationRef.current) return;
        authenticatedRef.current = true;
        setAuthenticated(true);
        installStatus(nextStatus);
      } catch (error) {
        if (generation !== statusMutationGenerationRef.current) return;
        authenticatedRef.current = false;
        setAuthenticated(false);
        setStatus(null);
        setConnection(isAuthenticationFailure(error) ? "unauthenticated" : "degraded");
        setAuthDialogOpen(true);
        const code = error instanceof OperatorApiError ? error.code : "operator_request_failed";
        setAuthError(code.replaceAll("_", " "));
      }
    },
    [applyLocalRelease, installStatus],
  );

  const bootstrapToken = useCallback(
    async (token: string): Promise<boolean> => {
      setAuthError(null);
      try {
        const nextStatus = await noobApi.bootstrapToken(token);
        authenticatedRef.current = true;
        setAuthenticated(true);
        setConfig((current) => ({ ...current, tokenConfigured: true }));
        setAuthDialogOpen(false);
        installStatus(nextStatus);
        return true;
      } catch (error) {
        authenticatedRef.current = false;
        setAuthenticated(false);
        setConnection("unauthenticated");
        const code = error instanceof OperatorApiError ? error.code : "operator_request_failed";
        setAuthError(code.replaceAll("_", " "));
        return false;
      }
    },
    [installStatus],
  );

  const clearToken = useCallback(async () => {
    applyLocalRelease();
    try {
      await noobApi.clearToken();
    } finally {
      authenticatedRef.current = false;
      setAuthenticated(false);
      setStatus(null);
      setConnection("unauthenticated");
      setConfig((current) => ({ ...current, tokenConfigured: false }));
      setAuthDialogOpen(true);
    }
  }, [applyLocalRelease]);

  useEffect(() => {
    if (!authenticated) return undefined;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      const generation = statusMutationGenerationRef.current;
      try {
        const nextStatus = await noobApi.status();
        if (cancelled || generation !== statusMutationGenerationRef.current) return;
        setStatus(nextStatus);
        setStreamGeneration((current) =>
          current === nextStatus.video.generation ? current : nextStatus.video.generation,
        );
        setLocalInputError(null);
        setConnection("live");
        if (
          claimedRef.current &&
          (!nextStatus.control.active || !nextStatus.serial.ready || !nextStatus.video.ready) &&
          Date.now() - claimStartedAtRef.current > 800
        ) {
          loseControl("Control lost");
        }
      } catch (error) {
        if (cancelled || generation !== statusMutationGenerationRef.current) return;
        setConnection(isAuthenticationFailure(error) ? "unauthenticated" : "degraded");
        if (isAuthenticationFailure(error)) {
          authenticatedRef.current = false;
          setAuthenticated(false);
          setStatus(null);
          setAuthDialogOpen(true);
          applyLocalRelease("Control lost");
        }
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, 1_000);
      }
    };
    timer = window.setTimeout(poll, 1_000);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [applyLocalRelease, authenticated, loseControl]);

  useEffect(() => {
    if (!authenticated) {
      setVideoModes([]);
      setVideoModeError(null);
      return undefined;
    }
    let cancelled = false;
    void noobApi.videoModes()
      .then((result) => {
        if (cancelled) return;
        setVideoModes(result.modes.filter((mode) => mode.validated));
        setVideoModeError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const code = error instanceof OperatorApiError ? error.code : "operator_request_failed";
        setVideoModeError(code);
      });
    return () => {
      cancelled = true;
    };
  }, [authenticated]);

  const sendInput = useCallback(
    async (command: GatewayInputCommand, recordAction = true): Promise<boolean> => {
      if (!claimedRef.current) return false;
      const fifo = inputFifoRef.current;
      if (fifo === null) return false;
      const generation = fifo.generation;
      try {
        const result = await fifo.enqueue(
          generation,
          async () => {
            await noobApi.send(command);
            if (recordAction && generation === fifo.generation && claimedRef.current) {
              setLastAction(describeInput(command));
            }
            return true;
          },
          () => {
            if (generation === fifo.generation) loseControl("Control lost");
          },
        );
        return result.executed && result.value === true;
      } catch {
        return false;
      }
    },
    [loseControl],
  );

  const claimControl = useCallback(async () => {
    if (
      !authenticatedRef.current ||
      claimedRef.current ||
      ownershipTransitionRef.current !== null ||
      status?.local_input.armed === true ||
      status?.local_input.exclusive_grab === true
    ) return;
    ownershipTransitionRef.current = "claim";
    const generation = ++statusMutationGenerationRef.current;
    try {
      await inputFifoRef.current?.whenIdle();
      if (!authenticatedRef.current || claimedRef.current) return;
      const result = await noobApi.claim();
      if (!authenticatedRef.current || generation !== statusMutationGenerationRef.current) {
        await noobApi.release().catch(() => undefined);
        return;
      }
      inputFifoRef.current?.invalidate();
      claimedRef.current = true;
      claimStartedAtRef.current = Date.now();
      leaseTtlRef.current = result.ttlMs;
      setClaimed(true);
      setLeaseExpiresAt(Date.now() + result.ttlMs);
      setLeaseRemainingMs(result.ttlMs);
      statusMutationGenerationRef.current += 1;
      setStatus((current) => current === null
        ? current
        : {
            ...current,
            control: {
              ...current.control,
              active: true,
              expires_in_ms: result.ttlMs,
            },
          });
      setLastAction("Control claimed");
    } catch {
      setLastAction("Control unavailable");
    } finally {
      ownershipTransitionRef.current = null;
    }
  }, [status?.local_input.armed, status?.local_input.exclusive_grab]);

  const releaseControl = useCallback(async (): Promise<boolean> => {
    if (!claimedRef.current) return true;
    if (ownershipTransitionRef.current !== null) return false;
    ownershipTransitionRef.current = "release";
    statusMutationGenerationRef.current += 1;
    let confirmed = false;
    try {
      await sendInput({ op: "release_all" }, false);
      await noobApi.release();
      confirmed = true;
    } catch {
      try {
        await noobApi.release();
        confirmed = true;
      } catch {
        confirmed = false;
      }
    } finally {
      applyLocalRelease(confirmed ? "Control released" : "Control release unconfirmed");
      ownershipTransitionRef.current = null;
    }
    return confirmed;
  }, [applyLocalRelease, sendInput]);

  const emergencyRelease = useCallback(async () => {
    statusMutationGenerationRef.current += 1;
    applyLocalRelease();
    if (!authenticatedRef.current) return;
    if (emergencyInFlightRef.current !== null) {
      await emergencyInFlightRef.current;
      return;
    }
    const operation = noobApi.releaseAll()
      .then(() => undefined)
      .catch(() => {
        setLastAction("Release request failed");
      })
      .finally(() => {
        emergencyInFlightRef.current = null;
      });
    emergencyInFlightRef.current = operation;
    await operation;
  }, [applyLocalRelease]);

  const armLocalInput = useCallback(async () => {
    const localInput = status?.local_input;
    if (
      !authenticatedRef.current ||
      connection !== "live" ||
      claimedRef.current ||
      ownershipTransitionRef.current !== null ||
      status?.control.active === true ||
      localInput === undefined ||
      !localInput.enabled ||
      !localInput.ready ||
      localInput.armed ||
      localInput.last_error !== null
    ) return;

    ownershipTransitionRef.current = "arm";
    const generation = ++statusMutationGenerationRef.current;
    setLocalInputBusy(true);
    setLocalInputError(null);
    try {
      const result = await noobApi.armLocalInput();
      if (!authenticatedRef.current || generation !== statusMutationGenerationRef.current) {
        await noobApi.disarmLocalInput().catch(() => undefined);
        return;
      }
      statusMutationGenerationRef.current += 1;
      setStatus((current) => current === null
        ? current
        : { ...current, local_input: result.local_input });
      setLastAction("uConsole input armed");
    } catch (error) {
      const code = error instanceof OperatorApiError ? error.code : "operator_request_failed";
      setLocalInputError(code);
      setLastAction("uConsole input unavailable");
      if (isAuthenticationFailure(error)) {
        authenticatedRef.current = false;
        setAuthenticated(false);
        setStatus(null);
        setConnection("unauthenticated");
        setAuthDialogOpen(true);
        applyLocalRelease("Control lost");
        return;
      }
      try {
        const nextStatus = await noobApi.status();
        if (authenticatedRef.current && generation === statusMutationGenerationRef.current) {
          statusMutationGenerationRef.current += 1;
          setStatus(nextStatus);
          setConnection("live");
        }
      } catch {
        if (code === "gateway_unreachable" || code === "operator_request_failed") {
          setConnection("degraded");
        }
      }
    } finally {
      ownershipTransitionRef.current = null;
      setLocalInputBusy(false);
    }
  }, [applyLocalRelease, connection, status]);

  const disarmLocalInput = useCallback(async (): Promise<boolean> => {
    const localInput = status?.local_input;
    if (
      !authenticatedRef.current ||
      connection !== "live" ||
      ownershipTransitionRef.current !== null ||
      (localInput?.armed !== true && localInput?.exclusive_grab !== true)
    ) return localInput?.armed !== true && localInput?.exclusive_grab !== true;

    ownershipTransitionRef.current = "disarm";
    const generation = ++statusMutationGenerationRef.current;
    setLocalInputBusy(true);
    setLocalInputError(null);
    try {
      const result = await noobApi.disarmLocalInput();
      if (!authenticatedRef.current || generation !== statusMutationGenerationRef.current) return false;
      const disarmed = result.local_input.armed !== true && result.local_input.exclusive_grab !== true;
      statusMutationGenerationRef.current += 1;
      setStatus((current) => current === null
        ? current
        : { ...current, local_input: result.local_input });
      setLastAction(disarmed ? "uConsole input disarmed" : "uConsole disarm unconfirmed");
      if (!disarmed) setLocalInputError("local_input_disarm_unconfirmed");
      return disarmed;
    } catch (error) {
      const code = error instanceof OperatorApiError ? error.code : "operator_request_failed";
      setLocalInputError(code);
      setLastAction("uConsole disarm unconfirmed");
      if (isAuthenticationFailure(error)) {
        authenticatedRef.current = false;
        setAuthenticated(false);
        setStatus(null);
        setConnection("unauthenticated");
        setAuthDialogOpen(true);
        applyLocalRelease("Control lost");
        return false;
      }
      try {
        const nextStatus = await noobApi.status();
        if (authenticatedRef.current && generation === statusMutationGenerationRef.current) {
          statusMutationGenerationRef.current += 1;
          setStatus(nextStatus);
          setConnection("live");
        }
      } catch {
        if (code === "gateway_unreachable" || code === "operator_request_failed") {
          setConnection("degraded");
        }
      }
      return false;
    } finally {
      ownershipTransitionRef.current = null;
      setLocalInputBusy(false);
    }
  }, [applyLocalRelease, connection, status?.local_input]);

  const switchVideoMode = useCallback(async (modeId: string) => {
    const video = status?.video;
    const selected = videoModes.find((mode) => mode.id === modeId && mode.validated);
    if (
      !authenticatedRef.current ||
      connection !== "live" ||
      video === undefined ||
      (video.state === "switching" || video.state === "rolling_back") ||
      video.active_mode_id === modeId ||
      selected === undefined ||
      videoModeInFlightRef.current ||
      claimedRef.current ||
      status?.control.active === true ||
      status?.local_input.armed === true ||
      status?.local_input.exclusive_grab === true
    ) return;

    videoModeInFlightRef.current = true;
    setVideoModeBusy(true);
    setVideoModeError(null);
    const mutationGeneration = ++statusMutationGenerationRef.current;
    try {
      const result = await noobApi.setVideoMode(modeId, video.generation);
      if (!authenticatedRef.current || mutationGeneration !== statusMutationGenerationRef.current) return;
      setStreamGeneration(result.video.generation);
      setStatus((current) => current === null ? current : {
        ...current,
        video: result.video,
      });
      setLastAction(`Capture output set to ${selected.label}`);
    } catch (error) {
      const code = error instanceof OperatorApiError ? error.code : "operator_request_failed";
      setVideoModeError(code);
      setLastAction(
        code === "video_mode_unconfirmed"
          ? "Capture output unconfirmed"
          : "Capture output unchanged",
      );
      try {
        const nextStatus = await noobApi.status();
        if (authenticatedRef.current && mutationGeneration === statusMutationGenerationRef.current) {
          setStatus(nextStatus);
          setStreamGeneration(nextStatus.video.generation);
          setConnection("live");
        }
      } catch {
        if (code === "gateway_unreachable" || code === "operator_request_failed") {
          setConnection("degraded");
        }
      }
    } finally {
      videoModeInFlightRef.current = false;
      setVideoModeBusy(false);
    }
  }, [connection, status, videoModes]);

  useEffect(() => {
    if (!claimed) return undefined;
    let cancelled = false;
    let timer: number | undefined;
    const renew = async () => {
      try {
        const result = await noobApi.renew();
        if (cancelled) return;
        leaseTtlRef.current = result.ttlMs;
        setLeaseExpiresAt(Date.now() + result.ttlMs);
        setLeaseRemainingMs(result.ttlMs);
        timer = window.setTimeout(renew, Math.max(350, Math.min(2_000, result.ttlMs * 0.45)));
      } catch {
        if (!cancelled) loseControl("Control lost");
      }
    };
    timer = window.setTimeout(
      renew,
      Math.max(350, Math.min(2_000, leaseTtlRef.current * 0.45)),
    );
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [claimed, loseControl]);

  useEffect(() => {
    if (!claimed || leaseExpiresAt === null) return undefined;
    const update = () => setLeaseRemainingMs(Math.max(0, leaseExpiresAt - Date.now()));
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [claimed, leaseExpiresAt]);

  const releaseCapturedInput = useCallback(() => {
    keyboardCaptureRef.current = false;
    pointerCaptureRef.current = false;
    pressedKeysRef.current.clear();
    setKeyboardCapture(false);
    setPointerCapture(false);
    setPointerLocked(false);
    releasePointerLock();
    if (claimedRef.current) {
      void sendInput({ op: "release_all" }, false);
    }
  }, [sendInput]);

  const setMode = useCallback(
    (nextMode: OperatorMode) => {
      if (nextMode === modeRef.current) return;
      releaseCapturedInput();
      modeRef.current = nextMode;
      setModeState(nextMode);
    },
    [releaseCapturedInput],
  );

  const toggleKeyboardCapture = useCallback(() => {
    if (!claimedRef.current || modeRef.current !== "human") return;
    const next = !keyboardCaptureRef.current;
    keyboardCaptureRef.current = next;
    setKeyboardCapture(next);
    if (!next) {
      pressedKeysRef.current.clear();
      void sendInput({ op: "release_all" }, false);
    }
  }, [sendInput]);

  const togglePointerCapture = useCallback(() => {
    if (!claimedRef.current || modeRef.current !== "human") return;
    const next = !pointerCaptureRef.current;
    pointerCaptureRef.current = next;
    setPointerCapture(next);
    if (!next) {
      setPointerLocked(false);
      releasePointerLock();
      void sendInput({ op: "release_all" }, false);
    }
  }, [sendInput]);

  const requestPointerLock = useCallback(async (element: HTMLElement) => {
    if (!claimedRef.current || !pointerCaptureRef.current || modeRef.current !== "human") return;
    try {
      await element.requestPointerLock();
    } catch {
      setPointerLocked(false);
    }
  }, []);

  useEffect(() => {
    const onPointerLockChange = () => setPointerLocked(document.pointerLockElement !== null);
    document.addEventListener("pointerlockchange", onPointerLockChange);
    return () => document.removeEventListener("pointerlockchange", onPointerLockChange);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code === "Escape") {
        if (claimedRef.current || keyboardCaptureRef.current || pointerCaptureRef.current) {
          event.preventDefault();
        }
        void emergencyRelease();
        return;
      }
      if (
        !claimedRef.current ||
        !keyboardCaptureRef.current ||
        modeRef.current !== "human"
      ) return;
      const command = mapKeyboardEvent(event, "down");
      if (command === null) return;
      event.preventDefault();
      pressedKeysRef.current.add(event.code);
      void sendInput(command, false);
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (
        !claimedRef.current ||
        !keyboardCaptureRef.current ||
        modeRef.current !== "human"
      ) return;
      const command = mapKeyboardEvent(event, "up");
      if (command === null) return;
      event.preventDefault();
      pressedKeysRef.current.delete(event.code);
      void sendInput(command, false);
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  }, [emergencyRelease, sendInput]);

  useEffect(() => {
    const onBlur = () => applyLocalRelease();
    const onPageHide = () => {
      const ownedControl = claimedRef.current;
      applyLocalRelease();
      if (authenticatedRef.current && ownedControl) {
        void noobApi.release().catch(() => undefined);
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") onPageHide();
    };
    const stopListening = noobApi.onControlLost(() => applyLocalRelease("Control lost"));
    window.addEventListener("blur", onBlur);
    window.addEventListener("pagehide", onPageHide);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopListening();
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("pagehide", onPageHide);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [applyLocalRelease]);

  return useMemo(
    () => ({
      config,
      status,
      connection,
      authenticated,
      authDialogOpen,
      authError,
      sessionStartedAt,
      claimed,
      leaseRemainingMs,
      mode,
      keyboardCapture,
      pointerCapture,
      pointerLocked,
      localInputBusy,
      localInputError,
      videoModes,
      videoModeBusy,
      videoModeError,
      lastAction,
      streamGeneration,
      setAuthDialogOpen,
      adoptConnection,
      bootstrapToken,
      clearToken,
      claimControl,
      releaseControl,
      emergencyRelease,
      armLocalInput,
      disarmLocalInput,
      switchVideoMode,
      setMode,
      toggleKeyboardCapture,
      togglePointerCapture,
      requestPointerLock,
      sendInput,
    }),
    [
      authDialogOpen,
      authError,
      adoptConnection,
      armLocalInput,
      authenticated,
      bootstrapToken,
      claimControl,
      claimed,
      clearToken,
      config,
      connection,
      emergencyRelease,
      keyboardCapture,
      lastAction,
      leaseRemainingMs,
      localInputBusy,
      localInputError,
      videoModeBusy,
      videoModeError,
      videoModes,
      mode,
      pointerCapture,
      pointerLocked,
      releaseControl,
      disarmLocalInput,
      switchVideoMode,
      requestPointerLock,
      sendInput,
      sessionStartedAt,
      setMode,
      status,
      streamGeneration,
      toggleKeyboardCapture,
      togglePointerCapture,
    ],
  );
}
