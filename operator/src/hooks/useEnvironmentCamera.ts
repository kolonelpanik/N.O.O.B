import { useCallback, useEffect, useRef, useState } from "react";
import type {
  EnvironmentCameraJob,
  EnvironmentCameraMediaItem,
  EnvironmentCameraStatus,
  EnvironmentCameraStorageStatus,
} from "../../shared/gateway-contract";
import { noobApi, OperatorApiError } from "../api/noob-client";

const PAGE_SIZE = 12;
const JOB_POLL_MS = 750;
const TERMINAL_JOB_STATES = new Set<EnvironmentCameraJob["state"]>([
  "complete",
  "failed",
  "cancelled",
]);

function errorCode(error: unknown): string {
  return error instanceof OperatorApiError ? error.code : "operator_request_failed";
}

function mergeUniqueMedia(
  current: EnvironmentCameraMediaItem[],
  incoming: EnvironmentCameraMediaItem[],
): EnvironmentCameraMediaItem[] {
  const seen = new Set<string>();
  return [...current, ...incoming].filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

export interface EnvironmentCameraController {
  camera: EnvironmentCameraStatus | null;
  storage: EnvironmentCameraStorageStatus | null;
  media: EnvironmentCameraMediaItem[];
  nextCursor: string | null;
  activeJob: EnvironmentCameraJob | null;
  activeJobId: string | null;
  busy: boolean;
  mediaBusy: boolean;
  error: string | null;
  setStreaming(enabled: boolean): Promise<boolean>;
  captureSnapshot(): Promise<EnvironmentCameraMediaItem | null>;
  startClip(durationSeconds: number, fps: number): Promise<boolean>;
  stopClip(): Promise<boolean>;
  refreshMedia(): Promise<void>;
  loadMore(): Promise<void>;
}

export function useEnvironmentCamera(
  authenticated: boolean,
  status: EnvironmentCameraStatus | null | undefined,
): EnvironmentCameraController {
  const [camera, setCamera] = useState<EnvironmentCameraStatus | null>(status ?? null);
  const [storage, setStorage] = useState<EnvironmentCameraStorageStatus | null>(status?.storage ?? null);
  const [media, setMedia] = useState<EnvironmentCameraMediaItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<EnvironmentCameraJob | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(status?.storage.active_job_id ?? null);
  const [busy, setBusy] = useState(false);
  const [mediaBusy, setMediaBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mediaRequestRef = useRef(0);
  const authenticatedRef = useRef(authenticated);
  authenticatedRef.current = authenticated;

  useEffect(() => {
    if (!authenticated || status === undefined || status === null) {
      mediaRequestRef.current += 1;
      setCamera(null);
      setStorage(null);
      setMedia([]);
      setNextCursor(null);
      setMediaBusy(false);
      setActiveJob(null);
      setActiveJobId(null);
      return;
    }
    setCamera((current) => (
      current !== null && current.generation > status.generation ? current : status
    ));
    setStorage(status.storage);
    if (status.storage.active_job_id !== null) {
      setActiveJobId((current) => current ?? status.storage.active_job_id);
    }
  }, [authenticated, status]);

  const fetchMediaPage = useCallback(async (cursor: string | undefined, append: boolean) => {
    const request = ++mediaRequestRef.current;
    setMediaBusy(true);
    try {
      const page = await noobApi.listEnvironmentMedia(PAGE_SIZE, cursor);
      if (request !== mediaRequestRef.current || !authenticatedRef.current) return;
      setStorage(page.storage);
      setMedia((current) => append ? mergeUniqueMedia(current, page.items) : page.items);
      setNextCursor(page.next_cursor);
      setError(null);
    } catch (caught) {
      if (request === mediaRequestRef.current) setError(errorCode(caught));
    } finally {
      if (request === mediaRequestRef.current) setMediaBusy(false);
    }
  }, []);

  const refreshMedia = useCallback(async () => {
    await fetchMediaPage(undefined, false);
  }, [fetchMediaPage]);

  const loadMore = useCallback(async () => {
    if (nextCursor === null || mediaBusy) return;
    await fetchMediaPage(nextCursor, true);
  }, [fetchMediaPage, mediaBusy, nextCursor]);

  useEffect(() => {
    if (!authenticated || status?.configured !== true || status.reachable !== true) {
      setMedia([]);
      setNextCursor(null);
      return;
    }
    void refreshMedia();
  }, [authenticated, refreshMedia, status?.configured, status?.device_id, status?.reachable]);

  useEffect(() => {
    if (!authenticated || activeJobId === null) return undefined;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const result = await noobApi.getEnvironmentClipJob(activeJobId);
        if (cancelled) return;
        setActiveJob(result.job);
        setError(null);
        if (TERMINAL_JOB_STATES.has(result.job.state)) {
          setActiveJobId(null);
          await refreshMedia();
          return;
        }
      } catch (caught) {
        if (!cancelled) setError(errorCode(caught));
      }
      if (!cancelled) timer = window.setTimeout(poll, JOB_POLL_MS);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeJobId, authenticated, refreshMedia]);

  const setStreaming = useCallback(async (enabled: boolean): Promise<boolean> => {
    if (camera === null || busy || !camera.configured) return false;
    setBusy(true);
    setError(null);
    try {
      const result = await noobApi.setEnvironmentCamera(enabled, camera.generation);
      if (!authenticatedRef.current) return false;
      setCamera(result.environment_camera);
      setStorage(result.environment_camera.storage);
      return true;
    } catch (caught) {
      setError(errorCode(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }, [busy, camera]);

  const captureSnapshot = useCallback(async (): Promise<EnvironmentCameraMediaItem | null> => {
    if (
      camera === null || busy || !camera.frame_ready || !camera.storage.mounted ||
      !camera.storage.writable
    ) return null;
    setBusy(true);
    setError(null);
    try {
      const result = await noobApi.captureEnvironmentSnapshot(camera.generation);
      if (!authenticatedRef.current) return null;
      setMedia((current) => mergeUniqueMedia([result.item], current));
      await refreshMedia();
      return result.item;
    } catch (caught) {
      setError(errorCode(caught));
      return null;
    } finally {
      setBusy(false);
    }
  }, [busy, camera, refreshMedia]);

  const startClip = useCallback(async (durationSeconds: number, fps: number): Promise<boolean> => {
    if (
      camera === null || busy || activeJobId !== null || !camera.frame_ready ||
      !camera.storage.mounted || !camera.storage.writable
    ) return false;
    setBusy(true);
    setError(null);
    try {
      const result = await noobApi.startEnvironmentClip(durationSeconds, fps, camera.generation);
      if (!authenticatedRef.current) return false;
      setActiveJob(null);
      setActiveJobId(result.job_id);
      return true;
    } catch (caught) {
      setError(errorCode(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }, [activeJobId, busy, camera]);

  const stopClip = useCallback(async (): Promise<boolean> => {
    if (activeJobId === null || busy) return false;
    setBusy(true);
    setError(null);
    try {
      const result = await noobApi.stopEnvironmentClip(activeJobId);
      if (!authenticatedRef.current) return false;
      setActiveJob((current) => current === null ? current : { ...current, state: result.state });
      return true;
    } catch (caught) {
      setError(errorCode(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }, [activeJobId, busy]);

  return {
    camera,
    storage,
    media,
    nextCursor,
    activeJob,
    activeJobId,
    busy,
    mediaBusy,
    error,
    setStreaming,
    captureSnapshot,
    startClip,
    stopClip,
    refreshMedia,
    loadMore,
  };
}
