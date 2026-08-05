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
  ActionIcon,
  Avatar,
  Button,
  Flex,
  Group,
  Image,
  Menu,
  Text,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import useNucleusSession from "@omniverse/auth/react/hooks/NucleusSession";
import {
  IconChevronDown,
  IconDeviceDesktop,
  IconInfoCircle,
  IconLogout,
} from "@tabler/icons-react";
import { useAuth } from "react-oidc-context";
import { NavLink, useLocation } from "react-router-dom";
import Logo from "../static/logo.png";
import LogoutDialog from "./LogoutDialog";

export default function Header() {
  const location = useLocation();
  const auth = useAuth();
  const nucleus = useNucleusSession();

  const [logoutOpened, { open: openLogout, close: closeLogout }] =
    useDisclosure(false);

  const givenName =
    auth.user?.profile.given_name ??
    auth.user?.profile.name?.split(" ")[0] ??
    "";
  const familyName =
    auth.user?.profile.family_name ??
    auth.user?.profile.name?.split(" ")[1] ??
    "";
  const fullName = `${givenName} ${familyName}`;
  const initials = `${givenName.substring(0, 1)}${familyName.substring(0, 1)}`;

  /**
   * Ends the portal session in all tabs and then ends the identity provider session,
   * so that the identity provider asks the user for their credentials on the next login
   * instead of signing them in again. Once their session is closed, the identity provider
   * redirects the user back to the configured post logout redirect URI, which defaults to
   * the home page.
   *
   * Called by the logout dialog after it carried out the choice of the user, because
   * removing the user clears the authentication cookies that the session API requires.
   */
  async function logOut() {
    const idToken = auth.user?.id_token;

    try {
      nucleus.setSession(null);
      await auth.removeUser();
      await auth.signoutRedirect({ id_token_hint: idToken });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : error?.toString?.() ?? "";

      notifications.show({
        title: "Failed to log out",
        message,
        color: "red",
        autoClose: 20000,
      });
    }
  }

  return (
    <Flex bg={"black.0"} p={"md"} align={"center"} gap={"xs"}>
      <NavLink to={"/"} style={{ textDecoration: "none" }}>
        <Group>
          <Image src={Logo} w={40} h={40} />
          <Text fw={700} c={"white"} tt={"uppercase"} size={"xl"}>
            Omniverse
          </Text>
        </Group>
      </NavLink>
      <Flex justify={"end"} flex={1}>
        <Menu>
          <Menu.Target>
            <Group gap={"xs"}>
              <Button p={0} h={"40"} title={fullName} variant={"transparent"}>
                <Avatar bg={"cyan"}>{initials}</Avatar>
              </Button>
              <ActionIcon variant={"subtle"} color={"gray"} title={"Settings"}>
                <IconChevronDown />
              </ActionIcon>
            </Group>
          </Menu.Target>

          <Menu.Dropdown>
            <Menu.Label>{auth.user?.profile.email}</Menu.Label>
            <Menu.Item
              disabled={location.pathname === "/sessions"}
              component={NavLink}
              leftSection={<IconDeviceDesktop />}
              to={location.pathname === "/sessions" ? "" : "/sessions"}
              target={location.pathname === "/sessions" ? "" : "_blank"}
            >
              Sessions
            </Menu.Item>
            <Menu.Item
              disabled={location.pathname === "/about"}
              component={NavLink}
              leftSection={<IconInfoCircle />}
              to={"/about"}
              target={location.pathname === "/about" ? "" : "_blank"}
            >
              About
            </Menu.Item>
            <Menu.Item leftSection={<IconLogout />} onClick={openLogout}>
              Log out
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Flex>

      <LogoutDialog
        opened={logoutOpened}
        onClose={closeLogout}
        onLogOut={logOut}
      />
    </Flex>
  );
}
