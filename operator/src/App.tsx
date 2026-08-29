import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ProofTone } from "./state/proof";
import { AgentChannel } from "./components/AgentChannel";
import { AppShell, type HeaderSignal } from "./components/AppShell";
import { ControlOwnership } from "./components/ControlOwnership";
import { CaptureOutputControl } from "./components/CaptureOutputControl";
import { DevicePicker } from "./components/DevicePicker";
import { EmergencyRelease } from "./components/EmergencyRelease";
import { EnvironmentCameraPanel } from "./components/EnvironmentCameraPanel";
import { InputControls } from "./components/InputControls";
import { LiveTarget } from "./components/LiveTarget";
import { LocalInputControl } from "./components/LocalInputControl";
import { MediaToolbar } from "./components/MediaToolbar";
import { ModeSwitch } from "./components/ModeSwitch";
import { ProofRail } from "./components/ProofRail";
import { SourceTabs, type VideoSource } from "./components/SourceTabs";
import { TokenBootstrap } from "./components/TokenBootstrap";
import { OperatorApiError } from "./api/noob-client";
import { useEnvironmentCamera } from "./hooks/useEnvironmentCamera";
import { useFrameFeed } from "./hooks/useFrameFeed";
import { useOperatorController } from "./hooks/useOperatorController";
import { saveCurrentGatewayFrame } from "./media/save-current-frame";
import { deriveProofModules } from "./state/proof";
import { releaseTargetBeforeObservation } from "./state/source-safety";
import type { GatewayConfigView } from "../shared/gateway-contract";

function dynamicSignal(
  label: string,
  ready: boolean | null,
  healthyValue: string,
  connection: "connecting" | "live" | "degraded" | "unauthenticated",
): HeaderSignal {
  if (connection === "connecting") return { label, value: "Connecting", tone: "unknown" };
  if (connection === "unauthenticated") return { label, value: "—", tone: "unknown" };
  if (connection === "degraded" || ready === false) return { label, value: "Degraded", tone: "degraded" };
  return {
    label,
    value: ready === true ? healthyValue : "—",
    tone: ready === true ? "healthy" : "unknown",
  };
}

export default function App() {
  const operator = useOperatorController();
  const adoptConnection = operator.adoptConnection;
  const targetRef = useRef<HTMLDivElement>(null);
  const mediaWorkspaceRef = useRef<HTMLDivElement>(null);
  const [source, setSource] = useState<VideoSource>("target");
  const [deviceDialogOpen, setDeviceDialogOpen] = useState(false);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [fit, setFit] = useState(true);
  const [zoomPercent, setZoomPercent] = useState(100);
  const [fullscreen, setFullscreen] = useState(false);
  const [screenshotBusy, setScreenshotBusy] = useState(false);
  const environment = useEnvironmentCamera(
    operator.authenticated,
    operator.status?.environment_camera,
  );
  const environmentConfigured = operator.status?.environment_camera?.configured === true;
  const environmentReady = environment.camera?.frame_ready === true;
  const streamReady = source === "target"
    ? operator.status?.video.ready === true
    : environmentReady;
  const streamUrl = source === "target"
    ? operator.config.streamUrl
    : "noob://gateway/environment-stream";
  const streamGeneration = source === "target"
    ? operator.streamGeneration
    : environment.camera?.generation ?? 0;
  const viewerCount = source === "target"
    ? operator.status?.video.viewers ?? null
    : environment.camera?.viewers ?? null;
  const frame = useFrameFeed(
    operator.authenticated && streamReady,
    streamUrl,
    streamGeneration,
    viewerCount,
    source,
  );

  useEffect(() => {
    const update = () => setFullscreen(document.fullscreenElement === mediaWorkspaceRef.current);
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);

  useEffect(() => {
    if (!environmentConfigured && source === "environment") {
      setSource("target");
      setFit(true);
      setZoomPercent(100);
      setSourceError(null);
    }
  }, [environmentConfigured, source]);

  const changeSource = useCallback(async (nextSource: VideoSource) => {
    if (nextSource === source || sourceBusy) return;
    setSourceBusy(true);
    setSourceError(null);
    try {
      if (nextSource === "environment") {
        const localInput = operator.status?.local_input;
        const safe = await releaseTargetBeforeObservation(
          {
            claimed: operator.claimed,
            localArmed: localInput?.armed === true || localInput?.exclusive_grab === true,
          },
          {
            releaseRemote: operator.releaseControl,
            disarmLocal: operator.disarmLocalInput,
          },
        );
        if (!safe) {
          setSourceError("Target input release was not confirmed. Environment view remains locked.");
          return;
        }
      }
      setSource(nextSource);
      setFit(true);
      setZoomPercent(100);
    } finally {
      setSourceBusy(false);
    }
  }, [operator, source, sourceBusy]);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement === mediaWorkspaceRef.current) {
        await document.exitFullscreen();
      } else {
        await mediaWorkspaceRef.current?.requestFullscreen();
      }
    } catch {
      setSourceError("Fullscreen is unavailable in this window state.");
    }
  }, []);

  const captureScreenshot = useCallback(async () => {
    setSourceError(null);
    if (screenshotBusy) return;
    setScreenshotBusy(true);
    try {
      await saveCurrentGatewayFrame(source);
    } catch (error) {
      const code = error instanceof OperatorApiError ? error.code : "snapshot_unavailable";
      setSourceError(code.replaceAll("_", " "));
    } finally {
      setScreenshotBusy(false);
    }
  }, [screenshotBusy, source]);

  const adoptDeviceConnection = useCallback(async (nextConfig: GatewayConfigView) => {
    await adoptConnection(nextConfig);
    setDeviceDialogOpen(false);
  }, [adoptConnection]);

  const signals = useMemo<HeaderSignal[]>(() => {
    const eyes = dynamicSignal(
      "Eyes",
      operator.status?.video.ready ?? null,
      "Live",
      operator.connection,
    );
    const hands = dynamicSignal(
      "Hands",
      operator.status?.serial.ready ?? null,
      "Ready",
      operator.connection,
    );
    let controlValue = "—";
    let controlTone: ProofTone = "unknown";
    if (operator.connection === "degraded") {
      controlValue = "Degraded";
      controlTone = "degraded";
    } else if (operator.connection === "live") {
      if (operator.status?.control.release_required) {
        controlValue = "Release pending";
        controlTone = "degraded";
      } else if (operator.status?.local_input.armed) {
        controlValue = "Local armed";
        controlTone = "healthy";
      } else if (operator.claimed) {
        controlValue = "Claimed";
        controlTone = "healthy";
      } else if (operator.status?.control.active) {
        controlValue = "Busy";
        controlTone = "degraded";
      } else {
        controlValue = "Available";
        controlTone = "healthy";
      }
    }
    return [eyes, hands, { label: "Control", value: controlValue, tone: controlTone }];
  }, [operator.claimed, operator.connection, operator.status]);

  const proofModules = useMemo(
    () => deriveProofModules(
      operator.status,
      operator.connection === "live",
      operator.sessionStartedAt,
    ),
    [operator.connection, operator.sessionStartedAt, operator.status],
  );

  const workspace = (
    <div ref={mediaWorkspaceRef} className="media-workspace">
      <SourceTabs
        source={source}
        busy={sourceBusy}
        environmentConfigured={environmentConfigured}
        error={sourceError}
        onChange={(nextSource) => void changeSource(nextSource)}
      />
      <MediaToolbar
        fit={fit}
        zoomPercent={zoomPercent}
        fullscreen={fullscreen}
        screenshotBusy={screenshotBusy}
        screenshotDisabled={!streamReady}
        onFit={() => {
          setFit(true);
          setZoomPercent(100);
        }}
        onZoomOut={() => {
          setFit(false);
          setZoomPercent((current) => Math.max(50, current - 25));
        }}
        onZoomIn={() => {
          setFit(false);
          setZoomPercent((current) => Math.min(200, fit ? 125 : current + 25));
        }}
        onScreenshot={() => void captureScreenshot()}
        onFullscreen={() => void toggleFullscreen()}
      />
      <div className="media-display">
        <LiveTarget
          ref={targetRef}
          imageSource={frame.imageSource}
          live={operator.connection === "live" && streamReady}
          claimed={source === "target" && !sourceBusy && operator.claimed}
          mode={operator.mode}
          pointerCapture={source === "target" && operator.pointerCapture}
          pointerLocked={source === "target" && operator.pointerLocked}
          usingFrameFallback={frame.usingFrameFallback}
          interactive={source === "target"}
          title={source === "target" ? "Live target" : "Environmental camera · non-target"}
          imageAlt={source === "target" ? "Live target video" : "Live environmental camera video"}
          fit={fit}
          zoomPercent={zoomPercent}
          intrinsicWidth={source === "target" ? operator.status?.video.width : environment.camera?.width}
          intrinsicHeight={source === "target" ? operator.status?.video.height : environment.camera?.height}
          onStreamError={() => {
            frame.markStreamFailed();
            if (source === "target" && operator.claimed) void operator.releaseControl();
          }}
          onStreamRecovered={frame.resetStream}
          sendInput={operator.sendInput}
        />
      </div>
    </div>
  );

  const controlRail = (
    <div className="control-stack">
      <div className="control-stack__scroll">
        {source === "environment" ? (
          <EnvironmentCameraPanel controller={environment} />
        ) : (
          <>
        <ControlOwnership
          authenticated={operator.authenticated}
          claimed={operator.claimed}
          claimBlocked={
            sourceBusy ||
            operator.localInputBusy
          }
          leaseRemainingMs={operator.leaseRemainingMs}
          onClaim={() => {
            if (targetRef.current) void operator.takeDirectControl(targetRef.current);
          }}
          onRelease={() => void operator.releaseControl()}
        />
        <LocalInputControl
          authenticated={operator.authenticated}
          connection={operator.connection}
          status={operator.status?.local_input ?? null}
          controlActive={operator.status?.control.active === true}
          electronClaimed={operator.claimed}
          busy={operator.localInputBusy}
          actionError={operator.localInputError}
          onArm={() => void operator.armLocalInput()}
          onDisarm={() => void operator.disarmLocalInput()}
        />
        <CaptureOutputControl
          video={operator.status?.video ?? null}
          modes={operator.videoModes}
          busy={operator.videoModeBusy}
          error={operator.videoModeError}
          disabled={
            operator.connection !== "live" ||
            operator.status === null ||
            (operator.status?.video.state === "switching" ||
              operator.status?.video.state === "rolling_back") ||
            operator.status?.control.active === true ||
            operator.status?.local_input.armed === true ||
            operator.status?.local_input.exclusive_grab === true
          }
          onChange={(modeId) => void operator.switchVideoMode(modeId)}
        />
        <ModeSwitch mode={operator.mode} onChange={operator.setMode} />
        <InputControls
          claimed={operator.claimed}
          mode={operator.mode}
          keyboardCapture={operator.keyboardCapture}
          pointerCapture={operator.pointerCapture}
          pointerLocked={operator.pointerLocked}
          onToggleKeyboard={operator.toggleKeyboardCapture}
          onTogglePointer={operator.togglePointerCapture}
          onPointerLock={() => {
            if (targetRef.current) void operator.requestPointerLock(targetRef.current);
          }}
          sendInput={operator.sendInput}
        />
        <AgentChannel
          authenticated={operator.authenticated}
          claimed={operator.claimed}
          connection={operator.connection}
          mode={operator.mode}
          lastAction={operator.lastAction}
          sendInput={operator.sendInput}
        />
          </>
        )}
      </div>
      <EmergencyRelease
        enabled={operator.authenticated}
        onRelease={() => void operator.emergencyRelease()}
      />
    </div>
  );

  return (
    <>
      <AppShell
        gatewayLabel={operator.config.gatewayLabel}
        signals={signals}
        workspace={workspace}
        controlRail={controlRail}
        proofRail={<ProofRail modules={proofModules} />}
        onDevices={() => setDeviceDialogOpen(true)}
        onSettings={() => operator.setAuthDialogOpen(true)}
      />
      <DevicePicker
        open={deviceDialogOpen}
        currentConfig={operator.config}
        onClose={() => setDeviceDialogOpen(false)}
        onConnected={adoptDeviceConnection}
      />
      <TokenBootstrap
        open={operator.authDialogOpen}
        authenticated={operator.authenticated}
        gatewayUrl={operator.config.gatewayUrl}
        error={operator.authError}
        onClose={() => operator.setAuthDialogOpen(false)}
        onConnect={operator.bootstrapToken}
        onClear={operator.clearToken}
      />
    </>
  );
}
