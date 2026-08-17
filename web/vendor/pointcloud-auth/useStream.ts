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

// Type definitions for Potree2 messages
interface Potree2Message {
  event_type: string;
  payload: unknown;
}

interface Potree2OpenUrlPayload {
  url: string;
}

interface Potree2StoreDataPayload {
  key: string;
  value: unknown;
  expiration_time: number;
}

interface Potree2GetDataPayload {
  key: string;
}

interface Potree2GetDataResultPayload {
  [k: string]: unknown;
  key: string;
  value: unknown;
}

function isPotree2Message(message: unknown): message is Potree2Message {
  return (
    typeof message === "object" &&
    message !== null &&
    "event_type" in message &&
    "payload" in message &&
    typeof (message as Potree2Message).event_type === "string"
  );
}

function isPotree2OpenUrlPayload(
  payload: unknown,
): payload is Potree2OpenUrlPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "url" in payload &&
    typeof (payload as Potree2OpenUrlPayload).url === "string"
  );
}

function isPotree2StoreDataPayload(
  payload: unknown,
): payload is Potree2StoreDataPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "key" in payload &&
    "value" in payload &&
    "expiration_time" in payload &&
    typeof (payload as Potree2StoreDataPayload).key === "string" &&
    typeof (payload as Potree2StoreDataPayload).expiration_time === "number"
  );
}

function isPotree2GetDataPayload(
  payload: unknown,
): payload is Potree2GetDataPayload {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "key" in payload &&
    typeof (payload as Potree2GetDataPayload).key === "string"
  );
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
          const video = document.getElementById(videoElementId);

          if (video instanceof HTMLVideoElement) {
            video.play().catch((error) => {
              setError(error as Error);
            });
            video.focus();
          }

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

    // Prefix used for local storage items specific to the omni.pointcloud.potree2 extension
    const POTREE2_STORAGE_PREFIX: string = "omni.pointcloud.potree2:";

    // Removes expired local storage items that were received from the omni.pointcloud.potree2
    // extension. When "clear_all" is true, all those items will be remove, independent of their
    // expiration status.
    function expirePotree2Storage(clear_all: boolean = false): void {
      // Retrieve all keys that have the prefix
      const keys: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key != null && key.startsWith(POTREE2_STORAGE_PREFIX)) {
          keys.push(key);
        }
      }

      for (const key of keys) {
        if (clear_all) {
          console.info("Removing storage entry '" + key + "'");
          localStorage.removeItem(key);
        } else {
          const data_json = localStorage.getItem(key);
          if (data_json != null) {
            const data = JSON.parse(data_json);
            if (
              typeof data === "object" &&
              data !== null &&
              "expiration_time" in data
            ) {
              const now = Date.now() / 1000; // seconds since epoch
              if (
                typeof data.expiration_time === "number" &&
                now >= data.expiration_time
              ) {
                console.info("Removing expired storage entry '" + key + "'");
                localStorage.removeItem(key);
              }
            }
          }
        }
      }
    }

    // Handles messages sent by the omni.pointcloud.potree2 extension.
    // Returns true is the message was processed, false otherwise.
    function handlePotree2Event(message: unknown): boolean {
      if (!isPotree2Message(message)) {
        return false;
      }

      // open_url: Open the requested URL in a new browser tab.
      // Note: This may trigger the browser's pop-up blocker under certain conditions.
      if (message.event_type === "omni.pointcloud.potree2@open_url") {
        if (isPotree2OpenUrlPayload(message.payload)) {
          const url = message.payload.url;
          console.info(message.event_type + ": Opening URL in new tab:", url);
          window.open(url, "_blank");
        }
        return true;
      }

      // ping: Answer with "ping_result", letting Kit know that a client is connected.
      if (message.event_type === "omni.pointcloud.potree2@ping") {
        const answer = {
          event_type: message.event_type + "_result",
          payload: { result: true },
        };
        console.info(message.event_type + ": Sending answer.");
        AppStreamer.sendMessage(answer);
        return true;
      }

      // store_data: Store the received value in the browser's local storage.
      // Expiration will be handled by expirePotree2Storage().
      //
      // Note: Instead of using local storage here, the data could also be passed on to another web
      // service for storage.
      if (message.event_type === "omni.pointcloud.potree2@store_data") {
        if (isPotree2StoreDataPayload(message.payload)) {
          let key = message.payload.key;
          if (key != "") {
            const data = {
              value: message.payload.value,
              expiration_time: message.payload.expiration_time,
            };
            localStorage.setItem(
              POTREE2_STORAGE_PREFIX + key,
              JSON.stringify(data),
            );
            console.info(
              message.event_type +
                ": Stored data for key '" +
                POTREE2_STORAGE_PREFIX +
                key +
                "'",
            );
          }
        }
        return true;
      }

      // get_data: Return data from local storage that was previously sent with "store_data".
      // Answer with "get_data_result" and the retrieved value.
      if (message.event_type === "omni.pointcloud.potree2@get_data") {
        // Remove expired items
        expirePotree2Storage();

        if (isPotree2GetDataPayload(message.payload)) {
          let key = message.payload.key;
          if (key != "") {
            const data_json = localStorage.getItem(
              POTREE2_STORAGE_PREFIX + key,
            );
            let value: unknown = null;

            if (data_json != null) {
              const data = JSON.parse(data_json);
              if (
                typeof data === "object" &&
                data !== null &&
                "value" in data
              ) {
                // Valid item was found
                value = (data as { value: unknown }).value;

                // Check its expiration again
                if ("expiration_time" in data) {
                  const now = Date.now() / 1000; // seconds since epoch
                  if (
                    typeof data.expiration_time === "number" &&
                    now >= data.expiration_time
                  ) {
                    console.info(
                      message.event_type +
                        ": Requested storage entry is expired",
                    );
                    localStorage.removeItem(POTREE2_STORAGE_PREFIX + key);
                    value = null;
                  }
                }
              }
            }

            // Prepare answer; value will be null if no stored item was found
            const answer: {
              event_type: string;
              payload: Potree2GetDataResultPayload;
            } = {
              event_type: message.event_type + "_result",
              payload: { key: key, value: value },
            };
            console.info(
              message.event_type +
                ": Sending " +
                (value == null ? "empty " : "") +
                "answer for key '" +
                POTREE2_STORAGE_PREFIX +
                key +
                "'",
            );
            AppStreamer.sendMessage(answer);
          }
        }
        return true;
      }

      return false;
    }

    function onCustomEvent(message: unknown) {
      console.log("onCustomEvent", message);

      // First, try to handle as Potree2 event
      if (handlePotree2Event(message)) {
        return;
      }

      // If not handled by Potree2, pass to external callback
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
