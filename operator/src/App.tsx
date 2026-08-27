import { useMemo, useRef } from "react";
import type { ProofTone } from "./state/proof";
import { AgentChannel } from "./components/AgentChannel";
import { AppShell, type HeaderSignal } from "./components/AppShell";
import { ControlOwnership } from "./components/ControlOwnership";
import { CaptureOutputControl } from "./components/CaptureOutputControl";
import { EmergencyRelease } from "./components/EmergencyRelease";
import { InputControls } from "./components/InputControls";
import { LiveTarget } from "./components/LiveTarget";
import { LocalInputControl } from "./components/LocalInputControl";
import { ModeSwitch } from "./components/ModeSwitch";
import { ProofRail } from "./components/ProofRail";
import { TokenBootstrap } from "./components/TokenBootstrap";
import { useFrameFeed } from "./hooks/useFrameFeed";
import { useOperatorController } from "./hooks/useOperatorController";
import { deriveProofModules } from "./state/proof";

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
  const targetRef = useRef<HTMLDivElement>(null);
  const frame = useFrameFeed(
    operator.authenticated && operator.status?.video.ready === true,
    operator.config.streamUrl,
    operator.streamGeneration,
    operator.status?.video.viewers ?? null,
  );

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
    <LiveTarget
      ref={targetRef}
      imageSource={frame.imageSource}
      live={operator.connection === "live" && operator.status?.video.ready === true}
      claimed={operator.claimed}
      mode={operator.mode}
      pointerCapture={operator.pointerCapture}
      pointerLocked={operator.pointerLocked}
      usingFrameFallback={frame.usingFrameFallback}
      onStreamError={() => {
        frame.markStreamFailed();
        if (operator.claimed) void operator.releaseControl();
      }}
      onStreamRecovered={frame.resetStream}
      sendInput={operator.sendInput}
    />
  );

  const controlRail = (
    <div className="control-stack">
      <div className="control-stack__scroll">
        <ControlOwnership
          authenticated={operator.authenticated}
          claimed={operator.claimed}
          claimBlocked={
            operator.localInputBusy ||
            operator.status?.local_input.armed === true ||
            operator.status?.local_input.exclusive_grab === true
          }
          leaseRemainingMs={operator.leaseRemainingMs}
          onClaim={() => void operator.claimControl()}
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
        onSettings={() => operator.setAuthDialogOpen(true)}
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
