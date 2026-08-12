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

import { render, screen } from "@testing-library/react";
import {
  getIndicatorColor,
  StreamLatencyIndicator,
} from "../StreamLatencyIndicator";

describe("StreamLatencyIndicator", () => {
  describe("getIndicatorColor", () => {
    it("returns green at or below 80ms", () => {
      expect(getIndicatorColor(0)).toBe("#76b900");
      expect(getIndicatorColor(80)).toBe("#76b900");
    });

    it("returns yellow between 81ms and 200ms", () => {
      expect(getIndicatorColor(81)).toBe("#ffa903");
      expect(getIndicatorColor(200)).toBe("#ffa903");
    });

    it("returns red above 200ms", () => {
      expect(getIndicatorColor(201)).toBe("#f21616");
      expect(getIndicatorColor(1000)).toBe("#f21616");
    });
  });

  it("hides the indicator when rtd is zero", () => {
    render(<StreamLatencyIndicator rtd={0} packetLoss={0} recentPacketLoss={0} />);
    expect(screen.queryByText("Connection Latency:")).toBeNull();
  });

  it("renders the indicator when rtd is non-zero", () => {
    render(<StreamLatencyIndicator rtd={100} packetLoss={0} recentPacketLoss={0} />);
    expect(screen.getByText("Connection Latency:")).toBeTruthy();
  });
});
