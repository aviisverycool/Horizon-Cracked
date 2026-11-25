import base64

import flet as ft


SURFACE = "#DC28253F"
SOFT = "#C23A364F"
WHITE = "#FAF9FD"
MUTED = "#C5BFD2"


def horizon_mark(size):
    return ft.Container(width=size, height=size, bgcolor="#171820", border_radius=size / 4,
        content=ft.Stack([
            ft.Container(left=size*.26, top=size*.2, width=size*.12, height=size*.6, bgcolor=WHITE),
            ft.Container(left=size*.62, top=size*.2, width=size*.12, height=size*.6, bgcolor=WHITE),
            ft.Container(left=0, right=0, top=size*.46, height=size*.07, bgcolor="#B6A4FF"),
        ]))


def tab(label, on_click):
    return ft.Container(content=ft.Text(label, size=14, weight=ft.FontWeight.W_600, color=WHITE),
        height=36, padding=ft.padding.symmetric(horizontal=12), alignment=ft.alignment.center,
        border_radius=9, ink=True, on_click=lambda _: on_click())


def player_avatar(texture, size=28):
    if texture:
        try:
            png = texture.read_bytes()
        except OSError:
            png = b""
        if png:
            data = base64.b64encode(png).decode("ascii")
            scale = size / 8
            height = int.from_bytes(png[20:24], "big") * scale
            return ft.Container(width=size, height=size, clip_behavior=ft.ClipBehavior.HARD_EDGE,
                border_radius=4, content=ft.Stack([
                    ft.Image(src_base64=data, width=64*scale, height=height, left=-x*scale, top=-size,
                             fit=ft.ImageFit.FILL, filter_quality=ft.FilterQuality.NONE)
                    for x in (8, 40)]))
    return ft.Icon(ft.Icons.ACCOUNT_CIRCLE, color="#C4B3EE", size=size)


def shell(app, artwork):
    app.nav = {key: tab(label, lambda key=key: app._go(key)) for key, label in (
        ("home", "Home"), ("mods", "Browse"), ("manage", "Manage"), ("components", "Components"), ("wardrobe", "Wardrobe"),
        ("log", "Build log"), ("settings", "Settings"))}
    app.side_dot = ft.Container(width=7, height=7, border_radius=4)
    instance = ft.Container(bgcolor=SOFT, border_radius=12, padding=9, ink=True,
        on_click=lambda _: app._go("home"),
        content=ft.Row([horizon_mark(38), ft.Column([
            ft.Text("Horizon", weight=ft.FontWeight.BOLD, color=WHITE),
            ft.Row([app.side_dot, ft.Text(f"Minecraft {app.version}", size=12, color=MUTED)], spacing=5),
        ], tight=True, spacing=1)]))
    sidebar = ft.Container(width=230, padding=14, border_radius=18, bgcolor=SURFACE,
        content=ft.Column([ft.Text("Instances", size=13, color=MUTED), instance,
            ft.Container(expand=True), ft.Text("Crackrizon Launcher", size=12, color=MUTED)], spacing=14))
    app.profile_label = ft.Text(app.selected_profile, size=13, weight=ft.FontWeight.BOLD, color=WHITE)
    app.avatar = ft.Container()
    app.profile_menu = ft.PopupMenuButton(tooltip="Switch player", items=app._profile_menu_items(),
        content=ft.Container(height=44, padding=ft.padding.symmetric(horizontal=10),
            border_radius=10, bgcolor=SURFACE, content=ft.Row([
                app.avatar, app.profile_label,
                ft.Icon(ft.Icons.EXPAND_MORE, size=16, color=MUTED)], tight=True, spacing=6)))
    app.nav["settings"].content = ft.Icon(ft.Icons.TUNE, size=18, color=WHITE)
    app.nav["settings"].tooltip = "Settings"
    top = ft.Row([ft.Container(bgcolor=SURFACE, border_radius=12, padding=4,
        content=ft.Row([app.nav[k] for k in ("home", "mods", "manage", "components", "wardrobe")], tight=True, spacing=1)),
        ft.Container(content=app.nav["log"], bgcolor=SURFACE, border_radius=10),
        app.profile_menu, ft.Container(content=app.nav["settings"], bgcolor=SURFACE, border_radius=10)],
        spacing=7, run_spacing=5, wrap=True)
    app.host = ft.Container(expand=True)
    body = ft.Row([sidebar, ft.Column([top, app.host], expand=True, spacing=12)],
        expand=True, spacing=26, vertical_alignment=ft.CrossAxisAlignment.STRETCH)
    return ft.Column([app._titlebar(), ft.Stack([
        ft.Image(src=artwork, fit=ft.ImageFit.FILL, filter_quality=ft.FilterQuality.NONE,
                 left=0, top=0, right=0, bottom=0),
        ft.Container(content=body, left=14, top=14, right=20, bottom=14),
    ], expand=True)], expand=True, spacing=0)


def home(app):
    edition = ft.Container(bgcolor=SOFT, border_radius=8, padding=ft.padding.symmetric(horizontal=10, vertical=6),
        content=ft.Row([horizon_mark(18), ft.Text("Rust Edition", size=12, color=WHITE)],
                       tight=True, spacing=6))
    version = ft.Container(bgcolor=SURFACE, padding=ft.padding.symmetric(horizontal=16), height=50,
        border_radius=12, alignment=ft.alignment.center_left, content=ft.Column([
            ft.Text("Minecraft version", size=11, color=MUTED),
            ft.Text(app.version, size=15, color=WHITE, weight=ft.FontWeight.BOLD)],
            tight=True, spacing=0, alignment=ft.MainAxisAlignment.CENTER))
    return app._page_shell(ft.Container(height=34), ft.Row([edition]), ft.Container(height=16),
        ft.Row([horizon_mark(55), ft.Text("Horizon", size=54, weight=ft.FontWeight.BOLD, color=WHITE)]),
        ft.Container(height=12), ft.Row([app.status_dot, ft.Container(content=app.status_text, expand=True)]),
        ft.Container(height=26), ft.Row([app.play_button, version]), app.progress,
        ft.Container(height=42), app._panel("Overview", "Current instance",
            ft.Text(f"Horizon  ·  Minecraft {app.version}", size=16, color=WHITE),
            ft.Row([ft.TextButton("Manage components", on_click=lambda _: app._go("components")),
                    ft.TextButton("Open build log", on_click=lambda _: app._go("log"))])))
