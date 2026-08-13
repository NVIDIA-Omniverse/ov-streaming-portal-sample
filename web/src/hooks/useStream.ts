/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: MIT
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 * DEALINGS IN THE SOFTWARE.
 */

import { hideNotification, notifications } from "@mantine/notifications";
import {
  AppStreamer,
  DirectConfig,
  EventAction,
  EventStatus,
  LogFormat,
  LogLevel,
  StatsEvent,
  StreamEvent,
  StreamType,
} from "@nvidia/ov-web-rtc";
import { useCallback, useEffect, useRef, useState } from "react";
import { Config } from "../providers/ConfigProvider";
import { StreamingApp } from "../state/Apps";
import { reportSessionError } from "../state/Sessions";
import { getResolution } from "../state/StreamResolution";
import { useConfig } from "./useConfig";
import useError from "./useError";
import useStreamStart, {
  showBrowserCodecWarning,
  showStreamWarning,
  streamStartNotification,
} from "./useStreamStart";

export interface UseStreamOptions {
  app: StreamingApp;
  /**
   * The payload from a deep-link that will be passed to the stream.
   */
  payload?: string;
  resolution?: string;
  sessionId: string;
  videoElementId?: string;
  audioElementId?: string;

  onCustomEvent?: (message: unknown) => void;
  onStreamStats?: (message: StatsEvent) => void;
  onStreamEnd?: () => void;
}

export interface UseStreamResult {
  loading: boolean;
  error: Error | string;
  terminate: () => Promise<void>;

  /**
   * Whether the streaming Kit app confirmed dynamic-resize support during the
   * config handshake. Only known after the stream has started.
   */
  allowDynamicResize: boolean;

  /**
   * Whether the stream resolution is currently fit to the video element size.
   */
  fitStreamResolution: boolean;
  setFitStreamResolution: (enabled: boolean) => void;
}

export default function useStream({
  app,
  payload,
  resolution,
  sessionId,
  videoElementId = "stream-video",
  audioElementId = "stream-audio",
  onCustomEvent,
  onStreamStats,
  onStreamEnd,
}: UseStreamOptions): UseStreamResult {
  const config = useConfig();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useError();
  const [allowDynamicResize, setAllowDynamicResize] = useState(false);
  const [fitStreamResolution, setFitStreamResolutionState] = useState(true);

  const initialized = useRef(false);

  const { mutateAsync: startNewSession } = useStreamStart(app.id, payload);
  const startNewSessionRef = useRef(startNewSession);
  startNewSessionRef.current = startNewSession;

  const callbacks = useRef({
    onCustomEvent,
    onStreamStats,
    onStreamEnd,
  });
  callbacks.current = {
    onCustomEvent,
    onStreamStats,
    onStreamEnd,
  };


  useEffect(() => {
    if (!sessionId) {
      return;
    }

    if (initialized.current) {
      return;
    }

    initialized.current = true;

    setLoading(true);
    setError("");

    function onUpdate(message: StreamEvent) {
      console.log("onUpdate", message);
    }

    function onStart(message: StreamEvent) {
      console.log("onStart", message);

      if (message.action === EventAction.START) {
        if (message.status === EventStatus.SUCCESS) {
          const video = document.getElementById(
            videoElementId,
          ) as HTMLVideoElement;

          video.play().catch((error) => {
            setError(error as Error);
          });
          video.focus();

          setLoading(false);
          hideNotification(streamStartNotification);
          setAllowDynamicResize(AppStreamer.allowDynamicResize);

          if (payload) {
            void AppStreamer.sendMessage({
              event_type: "apply_deeplink_request",
              payload: { data: payload },
            });
          }
        } else if (message.status === EventStatus.ERROR) {
          setError(message.info || "Unknown error.");
          setLoading(false);
        } else if (message.status === EventStatus.WARNING) {
          showStreamWarning();
        }
      }
    }

    function onStop(message: StreamEvent) {
      console.log("onStop", message);
      callbacks.current.onStreamEnd?.();
    }

    function onTerminate(message: StreamEvent) {
      console.log("onTerminate", message);
      callbacks.current.onStreamEnd?.();
    }

    function onStreamStats(message: StatsEvent) {
      // Kit's dynamic-resize support may be confirmed after the initial start
      // event, so keep watching stream stats until it is advertised.
      if (AppStreamer.allowDynamicResize) {
        setAllowDynamicResize(true);
      }
      callbacks.current.onStreamStats?.(message);
    }

    function onCustomEvent(message: unknown) {
      console.log("onCustomEvent", message);
      callbacks.current.onCustomEvent?.(message);
    }

    const params = createStreamConfig(app, sessionId, config, resolution);

    async function connect() {
      try {
        const sessionExists = await checkSession(sessionId, config);
        if (!sessionExists) {
          notifications.show({
            id: streamStartNotification,
            message:
              "This session is no longer available, starting a new streaming session...",
            loading: true,
            autoClose: 30000,
          });

          try {
            return await startNewSessionRef.current();
          } catch (error) {
            setError(error as Error);
            setLoading(false);
            return;
          }
        }

        const result = await AppStreamer.connect({
          streamSource: StreamType.NVCF,
          logLevel: LogLevel.INFO,
          logFormat: LogFormat.TEXT,
          streamConfig: {
            videoElementId,
            audioElementId,
            maxReconnects: 3,
            nativeTouchEvents: true,
            ...params,
            onUpdate,
            onStart,
            onStop,
            onTerminate,
            onStreamStats,
            onCustomEvent,
          },
        });

        if (isBrowserCodecWarning(result)) {
          showBrowserCodecWarning();
        }
      } catch (error) {
        setError(
          "info" in (error as StreamEvent)
            ? (error as StreamEvent).info
            : (error as Error),
        );
        setLoading(false);
      }
    }

    async function start() {
      console.log("Start streaming...");
      await connect();
    }

    void start();
    return () => {
      if (import.meta.env.PROD) {
        void AppStreamer.terminate();
      }
    };
  }, [
    app,
    payload,
    resolution,
    sessionId,
    videoElementId,
    audioElementId,
    config,
    setError,
  ]);

  // Forward any error reported during the streaming session to the backend
  // so it can be persisted on the session record and surfaced on the
  // session list page for diagnostics.
  useEffect(() => {
    if (!sessionId) {
      return;
    }
    if (!error) {
      return;
    }
    const message = typeof error === "string" ? error : (error as Error).message;
    void reportSessionError({ config, sessionId, error: message }).catch(
      (reportError) => {
        console.error("Failed to report session error:", reportError);
      },
    );
  }, [config, error, sessionId]);

  const terminate = useCallback(async () => {
    try {
      await AppStreamer.terminate(true);
    } catch (error) {
      setError(
        "info" in (error as StreamEvent)
          ? (error as StreamEvent).info
          : (error as Error),
      );
      console.error("Error terminating stream:", error);
    }
  }, [setError]);

  const setFitStreamResolution = useCallback((enabled: boolean) => {
    AppStreamer.setFitStreamResolution(enabled);
    setFitStreamResolutionState(enabled);
  }, []);

  return {
    loading,
    error,
    terminate,
    allowDynamicResize,
    fitStreamResolution,
    setFitStreamResolution,
  };
}

/**
 * Returns true when the given event matches the codec compatibility warning
 * emitted by @nvidia/ov-web-rtc 6.2.x+ when the requested 4K resolution cannot
 * be served because the browser does not advertise H264/H265 (HEVC) support
 * via RTCRtpReceiver.getCapabilities.
 */
function isBrowserCodecWarning(event: StreamEvent | undefined): boolean {
  if (!event || event.status !== EventStatus.WARNING) {
    return false;
  }
  const info = typeof event.info === "string" ? event.info : "";
  return /h\.?26[45]|hevc|codec/i.test(info);
}

async function checkSession(
  sessionId: string,
  config: Config,
): Promise<boolean> {
  const url = createStreamURL(sessionId, config);
  url.pathname += "/sign_in";

  try {
    const response = await fetch(url, { method: "HEAD" });
    return response.ok;
  } catch (error) {
    console.error(`Failed to check the current streaming session:`, error);
    return false;
  }
}

/**
 * Creates URL parameters for streaming the application from NVCF.
 * Returns URLSearchParams instance with values that must be passed to streamConfig object in
 * the `urlLocation.search` field.
 *
 * @param app
 * @param sessionId
 * @param config
 * @param resolutionKey
 * @returns {URLSearchParams}
 */
function createStreamConfig(
  app: StreamingApp,
  sessionId: string,
  config: Config,
  resolutionKey?: string,
): Partial<DirectConfig> {
  const { width, height } = getResolution(resolutionKey ?? null);

  const params: DirectConfig = {
    width,
    height,
    fps: 60,
    mic: false,
    cursor: "free",
    autoLaunch: true,

    // Adjust the stream resolution to the current size of the video element
    // so the streamed app UI auto-fits the browser window without letterboxing.
    fitStreamResolution: true,

    // Specifies that the default streaming endpoint must not be used.
    // Enables signaling parameters for the component.
    server: "",
  };

  // If specified, enables the private endpoint created in Azure
  if (app.mediaServer) {
    params.mediaServer = app.mediaServer;
    if (app.mediaPort) {
      params.mediaPort = app.mediaPort;
    }
  }

  const signalingURL = createStreamURL(sessionId, config);
  params.signalingServer = signalingURL.hostname;
  params.signalingPort = signalingURL.port
    ? Number(signalingURL.port)
    : signalingURL.protocol === "https:"
      ? 443
      : 80;
  params.signalingPath = signalingURL.pathname;
  params.signalingQuery = signalingURL.searchParams;
  return params;
}

/**
 * Constructs a URL object for streaming the specified NVCF function.
 *
 * @param sessionId
 * @param config
 * @returns {URL}
 */
function createStreamURL(sessionId: string, config: Config): URL {
  let backend = config.endpoints.backend;
  if (!backend.endsWith("/")) {
    backend += "/";
  }

  return new URL(`./sessions/${sessionId}`, backend);
}
