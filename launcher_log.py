from collections import deque
from itertools import islice
from queue import Empty
from threading import Lock
from time import monotonic

import flet as ft
from flet.core.page import PageDisconnectedException


class BuildLog:
    def __init__(self, font, default_color, success_color, error_color):
        self.view = ft.ListView(expand=True, spacing=3, auto_scroll=True)
        self.lines = deque(maxlen=2000)
        self.pending = 0
        self.lock = Lock()
        self.font = font
        self.colors = {None: default_color, "success": success_color, "error": error_color}

    def append(self, text, tag=None):
        with self.lock:
            self.lines.append((text, tag))
            self.pending = min(self.pending + 1, len(self.lines))

    def render(self):
        with self.lock:
            if not self.pending:
                return
            controls = self.view.controls
            overflow = len(controls) + self.pending - len(self.lines)
            if overflow > 0:
                del controls[:overflow]
            newest = list(islice(reversed(self.lines), self.pending))
            controls.extend(ft.Text(text, size=12, color=self.colors[tag], font_family=self.font,
                                    selectable=True) for text, tag in reversed(newest))
            self.pending = 0

    def text(self):
        with self.lock:
            return "\n".join(text for text, _ in self.lines)

    def clear(self):
        with self.lock:
            self.lines.clear()
            self.view.controls.clear()
            self.pending = 0


def process_events(app):
    while True:
        event, value = app.events.get()
        redraw = False
        # Coalesce visible log bursts into frames. Deliver interactive events immediately.
        deadline = monotonic() + 0.04
        for index in range(512):
            if event == "close":
                return
            try:
                app._handle_event(event, value)
            except Exception as error:
                app.build_log.append(f"UI error: {error}", "error")
            is_log = event in ("log", "log_success")
            redraw |= not is_log or app.current_page == "log"
            if index == 511:
                break
            try:
                if is_log and app.current_page == "log" and monotonic() < deadline:
                    event, value = app.events.get(timeout=max(0, deadline - monotonic()))
                else:
                    event, value = app.events.get_nowait()
            except Empty:
                break
        if redraw:
            if app.current_page == "log":
                app.build_log.render()
            try:
                app.page.update()
            except PageDisconnectedException:
                continue
            except Exception as error:
                app.build_log.append(f"Window update failed: {error}", "error")
