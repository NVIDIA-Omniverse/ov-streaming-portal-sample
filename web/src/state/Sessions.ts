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

import { parallel } from "radash";
import { z } from "zod";
import { Config } from "../providers/ConfigProvider";
import { HttpError } from "../util/Errors";
import { fromSnakeCaseSchema } from "../util/Schemas";
import { createPaginatedSchema } from "./Pagination";

/**
 * Schema for validating API responses for streaming sessions.
 */
const StreamingSession = fromSnakeCaseSchema(
  z.object({
    id: z.string(),
    functionId: z.string(),
    functionVersionId: z.string(),
    nvcfRequestId: z.string().nullable().optional(),
    userId: z.string(),
    userName: z.string(),
    status: z.enum([
      "CONNECTING",
      "ACTIVE",
      "IDLE",
      "STOPPED",
      "EXPIRED",
      "FAILED",
    ]),
    error: z.string().nullable().optional(),
    startDate: z.coerce.date(),
    endDate: z.coerce.date().nullable(),
    duration: z.number(),
    app: fromSnakeCaseSchema(
      z.object({
        id: z.string(),
        title: z.string(),
        productArea: z.string(),
        version: z.string(),
      }),
    ).nullable(),
  }),
);

export type StreamingSession = z.infer<typeof StreamingSession>;

/**
 * Statuses that represent a session that has ended (no longer running).
 */
export const TERMINAL_SESSION_STATUSES = [
  "STOPPED",
  "EXPIRED",
  "FAILED",
] as const satisfies ReadonlyArray<StreamingSession["status"]>;

export function isSessionEnded(status: StreamingSession["status"]): boolean {
  return (TERMINAL_SESSION_STATUSES as readonly string[]).includes(status);
}

const StreamingSessionPage = createPaginatedSchema(StreamingSession);

export type StreamingSessionPage = z.infer<typeof StreamingSessionPage>;

export interface GetSessionsParams {
  config: Config;
  page?: number;
  pageSize?: number;
  status?: string;
  appId?: string;
  orderBy?: string;

  /**
   * Restricts the result to the sessions of the current user.
   * Administrators see sessions of all users unless this is set.
   */
  own?: boolean;
}

const OWN_SESSIONS_PAGE_SIZE = 100;

export interface GetOwnAliveSessionsParams {
  config: Config;
}

/**
 * Returns every running session of the current user across all applications.
 * Administrators get their own sessions only, never the sessions of other users.
 *
 * @param config The application configuration retrieved from /config.json path.
 */
export async function getOwnAliveSessions({
  config,
}: GetOwnAliveSessionsParams): Promise<StreamingSession[]> {
  const sessions: StreamingSession[] = [];

  let page = 1;
  let totalPages = 1;
  do {
    const result = await getSessions({
      config,
      own: true,
      page,
      pageSize: OWN_SESSIONS_PAGE_SIZE,
      status: "ALIVE",
    });

    sessions.push(...result.items);
    totalPages = result.totalPages;
    page += 1;
  } while (page <= totalPages);

  return sessions;
}

/**
 * Returns streaming sessions filtered by the specified status.
 * If status is not provided, returns all streaming sessions.
 * The API uses offset pagination, the page parameter can specify the offset.
 *
 * @param config The application configuration retrieved from /config.json path.
 * @param page
 * @param status
 * @param app
 */
export async function getSessions({
  config,
  page,
  pageSize,
  status,
  appId,
  orderBy,
  own,
}: GetSessionsParams): Promise<StreamingSessionPage> {
  const params = new URLSearchParams();
  if (page) {
    params.set("page", page.toString());
  }
  if (pageSize) {
    params.set("page_size", pageSize.toString());
  }
  if (status) {
    params.set("status", status.toUpperCase());
  }
  if (appId) {
    params.set("app_id", appId.toString());
  }
  if (orderBy) {
    params.set("order_by", orderBy);
  }
  if (own) {
    params.set("own", "true");
  }

  const response = await fetch(
    `${config.endpoints.backend}/sessions/?${params.toString()}`,
  );
  if (response.ok) {
    const body: unknown = await response.json();
    return await StreamingSessionPage.parseAsync(body);
  }

  const text = await response.text();
  throw new HttpError(
    `Failed to load streaming sessions -- HTTP${response.status}.\n${text}`,
    response.status,
  );
}

export interface GetSessionParams {
  config: Config;
  sessionId: string;
}

export async function getSession({
  config,
  sessionId,
}: GetSessionParams): Promise<StreamingSession> {
  const response = await fetch(
    `${config.endpoints.backend}/sessions/${sessionId}`,
  );
  if (response.ok) {
    const body: unknown = await response.json();
    return await StreamingSession.parseAsync(body);
  }

  const text = await response.text();
  throw new HttpError(
    `Failed to load the session -- HTTP${response.status}.\n${text}`,
    response.status,
  );
}

export interface StartSessionParams {
  config: Config;
  appId: string;
}

export async function startSession({
  config,
  appId,
}: StartSessionParams): Promise<StreamingSession> {
  const params = new URLSearchParams();
  params.set("app_id", appId.toString());

  const response = await fetch(
    `${config.endpoints.backend}/sessions/?${params.toString()}`,
    {
      method: "POST",
    },
  );
  if (response.ok) {
    const body: unknown = await response.json();
    return await StreamingSession.parseAsync(body);
  }

  const text = await response.text();
  throw new HttpError(
    `Failed to start a streaming session -- HTTP${response.status}.\n${text}`,
    response.status,
  );
}

/**
 * How many sessions are terminated at the same time. Keeps a user with an
 * unusually large number of running sessions from opening a request per session
 * all at once.
 */
const TERMINATION_CONCURRENCY = 5;

export type SessionTerminationState = "terminating" | "terminated" | "failed";

export interface SessionTerminationProgress {
  state: SessionTerminationState;
  error?: string;
}

export interface TerminateOwnSessionsParams {
  config: Config;
  sessions: StreamingSession[];
  onProgress: (sessionId: string, progress: SessionTerminationProgress) => void;
}

/**
 * Terminates the specified sessions and reports the outcome of every session
 * through `onProgress` as soon as it is known. A session that cannot be
 * terminated does not stop the remaining ones.
 */
export async function terminateOwnSessions({
  config,
  sessions,
  onProgress,
}: TerminateOwnSessionsParams): Promise<void> {
  await parallel(TERMINATION_CONCURRENCY, sessions, async (session) => {
    try {
      await terminateSession({ config, sessionId: session.id });
      onProgress(session.id, { state: "terminated" });
    } catch (error) {
      onProgress(session.id, {
        state: "failed",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  });
}

export interface TerminateSessionParams {
  config: Config;
  sessionId: string;
}

/**
 * Terminates the session with the specified ID.
 * The user will get disconnected from the stream and will have to establish a new streaming session.
 *
 * @param config
 * @param sessionId
 */
export async function terminateSession({
  config,
  sessionId,
}: TerminateSessionParams): Promise<void> {
  const response = await fetch(
    `${config.endpoints.backend}/sessions/${sessionId}`,
    {
      method: "DELETE",
    },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new HttpError(
      `Failed to terminate the session -- HTTP${response.status}.\n${text}`,
      response.status,
    );
  }
}

export interface ReportSessionErrorParams {
  config: Config;
  sessionId: string;
  error: string | null;
}

/**
 * Records a client-side error against the streaming session so it can be
 * surfaced on the session list and inspected by administrators.
 * Pass `null` as `error` to clear a previously reported error.
 */
export async function reportSessionError({
  config,
  sessionId,
  error,
}: ReportSessionErrorParams): Promise<void> {
  const response = await fetch(
    `${config.endpoints.backend}/sessions/${sessionId}/`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ error }),
    },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new HttpError(
      `Failed to report a session error -- HTTP${response.status}.\n${text}`,
      response.status,
    );
  }
}
