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

import { useCallback, useRef, useState } from "react";
import { StatsEvent } from "@nvidia/ov-web-rtc";

const PACKET_LOSS_WINDOW_MS = 3 * 60 * 1000;

export interface LatencyStats {
  /**
   * Round trip delay in milliseconds.
   */
  rtd: number;

  /**
   * Total packet loss.
   */
  packetLoss: number;

  /**
   * Packets lost in the last 3 minutes.
   */
  recentPacketLoss: number;
}

interface PacketLossSample {
  time: number;
  lost: number;
}

const EMPTY_STATS: LatencyStats = {
  rtd: 0,
  packetLoss: 0,
  recentPacketLoss: 0,
};

export function useLatencyIndicator() {
  const [stats, setStats] = useState<LatencyStats>(EMPTY_STATS);
  const samplesRef = useRef<PacketLossSample[]>([]);
  const lastPacketLossRef = useRef<number | null>(null);

  const recordStats = useCallback((event: StatsEvent) => {
    const nextStats = event.data?.stats;
    if (!nextStats) {
      return;
    }

    const { rtd, packetLoss } = nextStats;
    const now = Date.now();
    const previousPacketLoss = lastPacketLossRef.current;
    lastPacketLossRef.current = packetLoss;

    const lostSinceLast =
      previousPacketLoss === null
        ? 0
        : Math.max(0, packetLoss - previousPacketLoss);

    const samples = samplesRef.current;
    samples.push({ time: now, lost: lostSinceLast });

    const windowStart = now - PACKET_LOSS_WINDOW_MS;
    while (samples.length > 0 && samples[0].time < windowStart) {
      samples.shift();
    }

    const recentPacketLoss = samples.reduce(
      (total, sample) => total + sample.lost,
      0,
    );

    setStats((prev) =>
      prev.rtd === rtd &&
      prev.packetLoss === packetLoss &&
      prev.recentPacketLoss === recentPacketLoss
        ? prev
        : { rtd, packetLoss, recentPacketLoss },
    );
  }, []);

  const resetStats = useCallback(() => {
    samplesRef.current = [];
    lastPacketLossRef.current = null;
    setStats(EMPTY_STATS);
  }, []);

  return [stats, recordStats, resetStats] as const;
}
