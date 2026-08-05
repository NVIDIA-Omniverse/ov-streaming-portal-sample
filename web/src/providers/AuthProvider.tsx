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

import Cookies from "js-cookie";
import { Log, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  AuthProvider as OIDCProvider,
  AuthProviderProps as OIDCProviderProps,
  useAuth,
} from "react-oidc-context";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useConfig } from "../hooks/useConfig";
import { setReauthenticationRequired } from "../util/reauthentication";
import { renewTokenWithLock } from "../util/tokenRenewal";

export interface AuthProviderProps {
  children?: ReactNode;
}

Log.setLogger(console);
Log.setLevel(import.meta.env.DEV ? Log.DEBUG : Log.INFO);

type AuthMessage = { type: "logout" } | { type: "renewal" };

/**
 * Integrates OpenID Connect to the portal and provides corresponding authentication information as context.
 * Stores authentication tokens in cookies.
 */
export default function AuthProvider({ children }: AuthProviderProps) {
  const [, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [channel] = useState(() => new BroadcastChannel("session"));

  const onSignIn = useCallback(() => {
    setReauthenticationRequired(false);
    setSearchParams({});
  }, [setSearchParams]);

  const onRemoveUser = useCallback(() => {
    Cookies.remove("id_token");
    Cookies.remove("access_token");
    setReauthenticationRequired(true);

    channel.postMessage({ type: "logout" });
    navigate("/");
  }, [channel, navigate]);

  const config = useConfig();
  const auth: OIDCProviderProps = useMemo(
    () => ({
      userManager: new UserManager({
        authority: config.auth.authority,
        automaticSilentRenew: false,
        client_id: config.auth.clientId,
        metadataUrl: config.auth.metadataUri,
        redirect_uri: config.auth.redirectUri,
        post_logout_redirect_uri:
          config.auth.postLogoutRedirectUri ?? homePageUri(),
        scope: config.auth.scope ?? "openid profile email",

        userStore: new WebStorageStateStore({
          store: window.localStorage,
        }),
      }),
    }),
    [config],
  );

  useEffect(() => {
    const listener = (event: MessageEvent) => {
      const message = event.data as AuthMessage;
      if (message.type === "logout") {
        setReauthenticationRequired(true);
        void auth.userManager?.removeUser();
      } else {
        void auth.userManager?.getUser();
      }
    };

    channel.addEventListener("message", listener);
    return () => channel.removeEventListener("message", listener);
  }, [channel, auth]);

  useEffect(() => {
    const onRenew = () => {
      void renewTokenWithLock({
        lockName: "auth-renewal",
        signinSilent: () => auth.userManager?.signinSilent() ?? Promise.resolve(null),
        onSuccess: () => {
          channel.postMessage({ type: "renewal" } as AuthMessage);
        },
      });
    };

    auth.userManager?.events.addAccessTokenExpiring(onRenew);
    return () => {
      auth.userManager?.events.removeAccessTokenExpiring(onRenew);
    };
  }, [auth, channel]);

  return (
    <OIDCProvider
      {...auth}
      skipSigninCallback={window.location.pathname.startsWith("/nucleus")}
      onSigninCallback={onSignIn}
      onRemoveUser={onRemoveUser}
    >
      <CookieSync />
      {children}
    </OIDCProvider>
  );
}

function homePageUri() {
  return new URL("/", window.location.origin).href;
}

function CookieSync() {
  const auth = useAuth();

  useEffect(() => {
    if (auth.user?.id_token) {
      Cookies.set("id_token", auth.user.id_token, { expires: 1, path: "/" });
    } else {
      Cookies.remove("id_token");
    }

    if (auth.user?.access_token) {
      Cookies.set("access_token", auth.user.access_token, {
        expires: 1,
        path: "/",
      });
    } else {
      Cookies.remove("access_token");
    }
  }, [auth]);

  return null;
}
