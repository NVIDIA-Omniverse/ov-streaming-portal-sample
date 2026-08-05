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

const reauthenticationKey = "reauthentication";

/**
 * Marks that the user must authenticate on the identity provider again instead of
 * reusing the session the identity provider keeps for them.
 *
 * The mark is kept per browser tab and survives redirects to the identity provider,
 * so it also applies to the tab that started the logout once it returns to the portal.
 */
export function setReauthenticationRequired(required: boolean): void {
  if (required) {
    window.sessionStorage.setItem(reauthenticationKey, "true");
  } else {
    window.sessionStorage.removeItem(reauthenticationKey);
  }
}

export function isReauthenticationRequired(): boolean {
  return window.sessionStorage.getItem(reauthenticationKey) !== null;
}
