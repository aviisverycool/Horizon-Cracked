import json
import re
from threading import Thread
from time import time
from uuid import uuid4

import flet as ft
import keyring
import requests

from ely_skins import cached_skin, download_skin
import ely_session


AUTH = "https://authserver.ely.by/auth/"


class SignInError(ValueError):
    pass


def restore_account(record):
    payload = {k: record[k] for k in ("accessToken", "clientToken")}
    name = record.get("name")
    if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
        with requests.post(AUTH + "validate", json={"accessToken": payload["accessToken"]},
                           timeout=12, allow_redirects=False) as response:
            if response.status_code in (200, 204):
                return name, payload
            if response.status_code not in (400, 401, 403):
                raise RuntimeError("Ely.by session check is unavailable.")
    return exchange("refresh", payload)


def exchange(endpoint, payload):
    with requests.post(AUTH + endpoint, json=payload, timeout=12, allow_redirects=False) as result:
        if result.status_code in (400, 401, 403):
            detail = result.json().get("errorMessage", "")
            if "two factor" in detail.lower():
                raise SignInError("Enter your authenticator code and try again.")
            raise SignInError("Please check your Ely.by sign-in details and try again.")
        if result.status_code != 200:
            raise RuntimeError("Ely.by is unavailable. Try again later.")
        data = result.json()
    name = data.get("selectedProfile", {}).get("name")
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name)
            or not isinstance(data.get("accessToken"), str) or not data["accessToken"]
            or data.get("clientToken") != payload["clientToken"]):
        raise RuntimeError("Ely.by returned an incomplete account profile.")
    return name, {k: data[k] for k in ("accessToken", "clientToken")}


class ElyAccountMenu:
    def __init__(self, launcher, config_dir):
        self.launcher = launcher
        self.directory = config_dir
        self.name = None
        self.session = None
        self.checking = False
        self.reconnect = False
        self.account = ft.Text("Checking saved account…")
        self.action = ft.TextButton("Sign in to Ely.by", on_click=lambda _: self.open())
        self.sign_out = ft.TextButton("Sign out", visible=False, on_click=lambda _: self.logout())
        self.controls = ft.Row([self.account, self.action, self.sign_out], wrap=True, disabled=True)

    def ui(self, action):
        self.launcher.events.put(("ui", action))

    def restore(self):
        if self.checking:
            return
        self.checking = True
        self.controls.disabled = True
        self.account.value = "Checking saved account…"
        self.launcher.page.update()

        def task():
            try:
                self.restore_saved()
            except (keyring.errors.KeyringError, OSError):
                self.ui(lambda: self.show_saved(None, "Account storage unavailable. Sign in for this session."))
            except (json.JSONDecodeError, UnicodeError):
                self.ui(lambda: self.show_saved(None, "Sign in to reconnect your account"))
            finally:
                def finish():
                    self.checking = False
                    self.controls.disabled = False
                self.ui(finish)
        Thread(target=task, name="Ely account restore", daemon=True).start()

    def restore_saved(self):
        saved = ely_session.read(self.directory)
        if not isinstance(saved, dict) or not all(isinstance(saved.get(k), str) and saved[k]
                                                  for k in ("accessToken", "clientToken")):
            self.ui(lambda: self.show_saved(None, "Ely.by account"))
            return
        try:
            name, session = restore_account(saved)
        except SignInError:
            self.ui(lambda: self.show_saved(None, "Session expired. Sign in to refresh your skin."))
            return
        except (requests.RequestException, RuntimeError, ValueError):
            name = saved.get("name")
            label = f"Saved account · {name} (offline)" if name else "Ely.by is offline"
            self.ui(lambda: self.show_saved(name, label, retry=True))
            return
        self.connected(name, session, restoring=True, persist={**session, "name": name} != saved)

    def show_saved(self, name, label, retry=False):
        self.name, self.session = name, None
        self.reconnect = retry
        self.account.value = label
        self.action.text = "Retry connection" if retry else "Sign in to Ely.by"
        self.sign_out.visible = bool(name)

    def open(self):
        if self.checking or self.launcher.busy:
            return
        if self.reconnect:
            self.restore()
        elif self.session:
            self.launcher._start_task(lambda: self.fetch(self.name))
        else:
            self.prompt()

    def prompt(self, message=None, login=""):
        page = self.launcher.page
        user = ft.TextField(label="Ely.by username or email", value=login, autofocus=True)
        password = ft.TextField(label="Password", password=True, can_reveal_password=True)
        code = ft.TextField(label="Authenticator code (if enabled)", max_length=6)
        def cancel(_):
            password.value = code.value = ""
            page.close(dialog)

        def submit(_):
            if self.launcher.busy:
                return
            username = (user.value or "").strip()
            secret = password.value or ""
            if not username or not secret:
                password.error_text = "Enter your username and password."
                page.update()
                return
            otp = (code.value or "").strip()
            cancel(None)

            def task():
                try:
                    result = exchange("authenticate", {
                        "username": username, "password": f"{secret}:{otp}" if otp else secret,
                        "clientToken": str(uuid4()),
                    })
                except SignInError as error:
                    text = str(error)
                    self.ui(lambda: self.prompt(text, username))
                    return
                self.connected(*result)
            self.launcher._start_task(task)

        code.on_submit = password.on_submit = submit
        dialog = ft.AlertDialog(modal=True, bgcolor="#29243E",
            shape=ft.RoundedRectangleBorder(radius=16),
            title=ft.Text("Sign in to Ely.by", size=22, weight=ft.FontWeight.BOLD),
            content=ft.Column([ft.Text(message or "Sign in to load your account’s skin automatically."),
                               user, password, code], width=370, tight=True),
            actions=[ft.TextButton("Continue offline", on_click=cancel),
                     ft.FilledButton("Sign in", on_click=submit)])
        page.open(dialog)

    def connected(self, name, session, restoring=False, persist=True):
        if persist:
            try:
                ely_session.save(self.directory, {**session, "name": name})
            except (keyring.errors.KeyringError, OSError):
                self.launcher.events.put(("log", "Ely.by sign-in will last for this launcher session."))
        def apply():
            self.name, self.session = name, session
            self.reconnect = False
            self.account.value = f"Signed in as {name}"
            self.action.text = "Refresh skin"
            self.sign_out.visible = True
            if not restoring or (not self.launcher.offline_profiles_file.exists()
                                 and self.launcher.selected_profile == "Player"):
                self.launcher._activate_ely_profile(name)
        self.ui(apply)
        if restoring:
            try:
                texture = cached_skin(self.directory, name)
                if texture and -1 <= time() - texture.stat().st_mtime < 900:
                    return
            except OSError:
                pass
        self.fetch(name)

    def fetch(self, name):
        try:
            download_skin(self.directory, name)
        except (requests.RequestException, OSError, ValueError) as error:
            self.ui(lambda: setattr(self.account, "value", f"Signed in as {name} · skin sync unavailable"))
            self.launcher.events.put(("log", f"Skin sync for {name}: {error}"))
            return
        def apply():
            self.account.value = f"Signed in as {name}"
            if self.launcher.selected_profile.casefold() == name.casefold():
                self.launcher._render_avatar()
        self.ui(apply)

    def logout(self):
        if self.checking or self.launcher.busy:
            return
        def task():
            ely_session.save(self.directory, None)
            self.ui(lambda: self.show_saved(None, "Ely.by account"))
            self.launcher.events.put(("status", "Signed out of Ely.by"))
        self.launcher._start_task(task)
