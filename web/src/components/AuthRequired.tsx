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

import { Loader } from "@mantine/core";
import Cookies from "js-cookie";
import { ReactNode, useEffect, useState } from "react";
import { hasAuthParams, useAuth } from "react-oidc-context";
import { useLocation } from "react-router-dom";
import { isReauthenticationRequired } from "../util/reauthentication";

export interface AuthRequiredProps {
  children?: ReactNode;
}

export interface AuthState {
  /**
   * The page where the user should be redirected back to after authentication.
   */
  redirectTo?: string;
}

/**
 * Redirects the user to the identity provider if they are not authenticated.
 * All web pages in this example require authentication.
 */
export default function AuthRequired({ children }: AuthRequiredProps) {
  const auth = useAuth();
  const location = useLocation();
  const [hasTriedSignIn, setHasTriedSignIn] = useState(false);

  useEffect(() => {
    if (
      !hasAuthParams() &&
      !auth.isAuthenticated &&
      !auth.activeNavigator &&
      !auth.isLoading &&
      !hasTriedSignIn
    ) {
      const redirectTo = `${location.pathname}${location.search}`;
      const state: AuthState = { redirectTo };

      setHasTriedSignIn(true);

      if (isReauthenticationRequired()) {
        void auth.signinRedirect({ state, prompt: "login" });
        return;
      }

      const handleError = (error: Error) => {
        console.error(error);
      };

      auth.events.addSilentRenewError(handleError);

      void auth
        .signinSilent({ state })
        .then((user) => {
          if (user) {
            if (user.id_token) {
              Cookies.set("id_token", user.id_token, { expires: 1, path: "/" });
            }
            if (user.access_token) {
              Cookies.set("access_token", user.access_token, {
                expires: 1,
                path: "/",
              });
            }
            setHasTriedSignIn(false);
          } else {
            Cookies.remove("id_token");
            Cookies.remove("access_token");
            return auth.signinRedirect({ state });
          }
        })
        .catch((error) => {
          console.warn(error);
          return auth.signinRedirect({ state });
        });

      return () => {
        auth.events.removeSilentRenewError(handleError);
      };
    }
  }, [auth, location, hasTriedSignIn]);

  if ((auth.isLoading && !auth.isAuthenticated) || hasTriedSignIn) {
    return <Loader m={"md"} />;
  }

  if (!auth.isAuthenticated) {
    return null;
  }

  return children;
}