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

import { IconCircleFilled } from "@tabler/icons-react";
import { Flex, Group, HoverCard, Stack, Text } from "@mantine/core";
import { CSSProperties } from "react";

/** Threshold (ms) below which latency is considered good. */
const LATENCY_GOOD_THRESHOLD_MS = 80;

/** Threshold (ms) below which latency is considered acceptable/warning. */
const LATENCY_WARNING_THRESHOLD_MS = 200;

/** Color used to indicate good latency. */
const LATENCY_GOOD_COLOR = "#76b900";

/** Color used to indicate warning latency. */
const LATENCY_WARNING_COLOR = "#ffa903";

/** Color used to indicate critical latency. */
const LATENCY_CRITICAL_COLOR = "#f21616";

export interface StreamLatencyIndicatorProps {
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

  style?: CSSProperties;
}

export function StreamLatencyIndicator({
  rtd,
  packetLoss,
  recentPacketLoss,
  style,
}: StreamLatencyIndicatorProps) {
  if (!rtd) {
    return null;
  }

  const color = getIndicatorColor(rtd);
  return (
    <Flex style={style} align={"center"} gap={"xs"}>
      <Text size={"xs"}>Connection Latency:</Text>
      <HoverCard position={"top"} withArrow shadow={"md"} openDelay={0}>
        <HoverCard.Target>
          <IconCircleFilled color={color} size={16} />
        </HoverCard.Target>
        <HoverCard.Dropdown>
          <Stack gap={4}>
            <Text size={"sm"} fw={600}>
              Connection Stats
            </Text>
            <Group justify={"space-between"} gap={"lg"}>
              <Text size={"xs"} c={"dimmed"}>
                Round trip delay
              </Text>
              <Text size={"xs"}>{rtd}ms</Text>
            </Group>
            <Group justify={"space-between"} gap={"lg"}>
              <Text size={"xs"} c={"dimmed"}>
                Packets lost (last 3 min)
              </Text>
              <Text size={"xs"}>{recentPacketLoss}</Text>
            </Group>
            <Group justify={"space-between"} gap={"lg"}>
              <Text size={"xs"} c={"dimmed"}>
                Packets lost (total)
              </Text>
              <Text size={"xs"}>{packetLoss}</Text>
            </Group>
          </Stack>
        </HoverCard.Dropdown>
      </HoverCard>
    </Flex>
  );
}

/**
 * Returns the indicator color for a given round-trip delay.
 *
 * @param rtd Round-trip delay in milliseconds.
 */
export function getIndicatorColor(rtd: number) {
  if (rtd <= LATENCY_GOOD_THRESHOLD_MS) {
    return LATENCY_GOOD_COLOR;
  }
  if (rtd <= LATENCY_WARNING_THRESHOLD_MS) {
    return LATENCY_WARNING_COLOR;
  }
  return LATENCY_CRITICAL_COLOR;
}
