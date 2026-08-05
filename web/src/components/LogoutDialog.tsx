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

import {
  Box,
  Button,
  Center,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useConfig } from "../hooks/useConfig";
import {
  getOwnAliveSessions,
  SessionTerminationProgress,
  StreamingSession,
  terminateOwnSessions,
} from "../state/Sessions";

export interface LogoutDialogProps {
  opened: boolean;
  onClose: () => void;

  /**
   * Ends the portal session. Called once the choice of the user has been
   * carried out, so that sessions are terminated while the authentication
   * tokens are still available.
   */
  onLogOut: () => Promise<void>;
}

/**
 * Asks users how they want to log out: either keeping their streaming sessions
 * alive for later, or ending all of them. When sessions are ended, displays the
 * progress of every session and only then logs the user out.
 */
export default function LogoutDialog({
  opened,
  onClose,
  onLogOut,
}: LogoutDialogProps) {
  const config = useConfig();

  const [sessions, setSessions] = useState<StreamingSession[]>([]);
  const [results, setResults] = useState<
    Record<string, SessionTerminationProgress>
  >({});

  const logout = useMutation({ mutationFn: onLogOut });

  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  const endSessions = useMutation({
    mutationFn: async () => {
      const alive = await getOwnAliveSessions({ config });
      setSessions(alive);
      setResults({});

      let failed = 0;
      await terminateOwnSessions({
        config,
        sessions: alive,
        onProgress: (sessionId, update) => {
          if (update.state === "failed") {
            failed += 1;
          }
          setResults((current) => ({ ...current, [sessionId]: update }));
        },
      });

      return { total: alive.length, failed };
    },
    onSuccess: ({ total, failed }) => {
      if (total === 0) {
        // Nothing to show, so there is no point in delaying the logout.
        logOut();
      } else if (failed === 0) {
        // Let users see that every session ended before leaving the page.
        setSecondsLeft(SUCCESS_DELAY_SECONDS);
      }
    },
  });

  function logOut() {
    setSecondsLeft(null);
    if (!logout.isPending && !logout.isSuccess) {
      logout.mutate();
    }
  }

  const logOutRef = useRef(logOut);
  logOutRef.current = logOut;

  useEffect(() => {
    if (secondsLeft === null) {
      return;
    }
    if (secondsLeft <= 0) {
      logOutRef.current();
      return;
    }
    const timer = setTimeout(() => setSecondsLeft(secondsLeft - 1), 1000);
    return () => clearTimeout(timer);
  }, [secondsLeft]);

  // Sessions cannot be brought back once they are terminated, so the dialog
  // stays open and locked until the logout completes.
  const busy = logout.isPending || !endSessions.isIdle;

  return (
    <Modal
      centered
      size={"lg"}
      opened={opened}
      title={"Log out"}
      closeOnClickOutside={!busy}
      closeOnEscape={false}
      withCloseButton={!busy}
      onClose={onClose}
    >
      {endSessions.isIdle ? (
        <Stack gap={"md"}>
          <Text size={"sm"}>
            To log out of the Portal, please choose an option below.
          </Text>

          <Group grow>
            <Button loading={logout.isPending} onClick={() => logOut()}>
              Log out &amp; retain all sessions
            </Button>
            <Button
              color={"red"}
              disabled={logout.isPending}
              onClick={() => endSessions.mutate()}
            >
              Log out &amp; end all sessions
            </Button>
          </Group>
        </Stack>
      ) : (
        <Stack gap={"md"}>
          <Text size={"sm"}>
            Ending your streaming sessions before logging out.
          </Text>

          {endSessions.isError ? (
            <TerminationError error={endSessions.error} />
          ) : sessions.length === 0 ? (
            <Center>
              <Loader size={"sm"} />
            </Center>
          ) : (
            <>
              <Box
                mah={"45vh"}
                style={{
                  overflowY: "auto",
                  overflowX: "hidden",
                  scrollbarGutter: "stable",
                }}
              >
                <Stack gap={"xs"}>
                  {sessions.map((session) => (
                    <SessionTerminationRow
                      key={session.id}
                      session={session}
                      result={results[session.id]}
                    />
                  ))}
                </Stack>
              </Box>

              <TerminationSummary
                sessions={sessions}
                results={results}
                settled={endSessions.isSuccess}
              />
            </>
          )}

          <Group justify={"end"}>
            <Button
              disabled={endSessions.isPending}
              loading={logout.isPending}
              onClick={() => logOut()}
            >
              {secondsLeft !== null && secondsLeft > 0
                ? `Log out now (${secondsLeft})`
                : "Log out anyway"}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

interface SessionTerminationRowProps {
  session: StreamingSession;
  result?: SessionTerminationProgress;
}

function SessionTerminationRow({ session, result }: SessionTerminationRowProps) {
  const title = session.app?.title ?? "Unknown application";
  return (
    <Group gap={"sm"} wrap={"nowrap"}>
      <Center w={ICON_SLOT_SIZE} h={ICON_SLOT_SIZE} style={{ flexShrink: 0 }}>
        <SessionTerminationIcon result={result} />
      </Center>

      <Stack gap={0} style={{ minWidth: 0, flex: 1 }}>
        <Text size={"sm"} truncate title={title}>
          {title}
        </Text>
        <Text size={"xs"} c={"dimmed"} truncate title={session.id}>
          {session.id}
        </Text>
      </Stack>
    </Group>
  );
}

function SessionTerminationIcon({
  result,
}: {
  result?: SessionTerminationProgress;
}) {
  if (!result || result.state === "terminating") {
    return <Loader size={"xs"} />;
  }

  if (result.state === "terminated") {
    return <IconCheck size={18} color={"var(--mantine-color-green-6)"} />;
  }

  return (
    <Tooltip
      withArrow
      multiline
      maw={300}
      label={result.error ?? "Failed to end the session."}
    >
      <Center>
        <IconX size={18} color={"var(--mantine-color-red-6)"} />
      </Center>
    </Tooltip>
  );
}

interface TerminationSummaryProps {
  sessions: StreamingSession[];
  results: Record<string, SessionTerminationProgress>;
  settled: boolean;
}

function TerminationSummary({
  sessions,
  results,
  settled,
}: TerminationSummaryProps) {
  const states = sessions.map((session) => results[session.id]?.state);
  const ended = states.filter((state) => state === "terminated").length;
  const failed = states.filter((state) => state === "failed").length;

  let summary: string;
  if (!settled) {
    summary = `Ended ${ended} of ${sessions.length}...`;
  } else if (failed > 0) {
    summary = `${ended} ended, ${failed} failed to end and may still be running.`;
  } else {
    summary = `${ended} ${ended === 1 ? "session" : "sessions"} ended.`;
  }

  return (
    <Text size={"sm"} c={"dimmed"}>
      {summary}
    </Text>
  );
}

function TerminationError({ error }: { error: unknown }) {
  return (
    <Text size={"sm"} c={"red"}>
      {error instanceof Error ? error.message : String(error)}
    </Text>
  );
}

/**
 * How long the terminated sessions stay on screen before the dialog continues
 * with the logout, so that users can see the outcome of every session instead
 * of a redirect interrupting the progress list.
 */
const SUCCESS_DELAY_SECONDS = 3;

/**
 * Keeps the spinner and the outcome icons in a box of the same size, so rows
 * do not shift when a session finishes terminating.
 */
const ICON_SLOT_SIZE = 20;