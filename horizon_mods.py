from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from time import monotonic
import tomllib
import zipfile

import flet as ft
import requests


KINDS = {"mod": "Horizon mods", "native": "Native mods (.hnmod)", "shader": "Shaders", "resource": "Resource packs"}
CATALOG_PAGE = "https://playhorizon.dev/mods"
REGISTRY_INDEX = "https://horizon-rust.github.io/horizon-mods/index.json"
JAVA_CATALOGS = ("modrinth", "curseforge")
CATALOG_TTL = 300
SEARCH_LIMIT = 20


class HorizonMods:
    def __init__(self, app, game):
        self.app = app
        self.game = game
        self.home = game / ".local" / "mod-launcher"
        self.selection = self.home / "active-build"
        self.binary = "horizon-client.exe" if os.name == "nt" else "horizon-client"
        self.kind = "mod"
        self.index_path = game / "mod-browser" / "horizon-index.json"
        self.catalog = None
        self.catalog_expires = 0
        self.catalog_offline = False
        self.search_results = OrderedDict()
        self.displayed_search = None
        self.category = ft.Dropdown(value=self.kind, width=205, label="Content",
            options=[ft.dropdown.Option(key, label) for key, label in KINDS.items()],
            on_change=self.select_kind)
        self.query = ft.TextField(hint_text="Search or browse", expand=True,
                                  on_submit=lambda _: self.search())
        self.results = ft.Column(spacing=8)
        self.caption = ft.Text("Catalog", size=16, weight=ft.FontWeight.BOLD)
        self.more = ft.TextButton("Show more", visible=False, on_click=lambda _: self.search(more=True))
        self.manage_category = ft.Dropdown(value="mod", width=205, label="Content",
            options=[ft.dropdown.Option(key, label) for key, label in KINDS.items()],
            on_change=lambda _: self.installed())
        self.installed_caption = ft.Text("Installed content", size=16, weight=ft.FontWeight.BOLD)
        self.installed_rows = ft.Column(spacing=8)
        self.picker = ft.FilePicker(on_result=self.picked)
        app.page.overlay.append(self.picker)

    def page(self):
        self.browser = self.app._panel("Content browser", "Search by name or leave the search field empty to browse.",
                ft.Row([self.category, ft.TextButton("playhorizon.dev/mods", url=CATALOG_PAGE),
                        ft.IconButton(ft.Icons.REFRESH, tooltip="Refresh catalog",
                                      on_click=lambda _: self.search(refresh=True))], wrap=True),
                ft.Row([self.query, ft.FilledButton("Search", on_click=lambda _: self.search())]))
        self.progress = ft.ProgressBar(visible=False, height=3)
        return self.app._page_shell(self.app._heading("Browse"), ft.Container(height=12),
            self.browser, self.progress, ft.Container(height=12), self.caption, self.results, self.more)

    def manage_page(self):
        self.manager = self.app._panel("Installed content", "Choose which packages this instance loads.",
                self.manage_category,
                ft.Row([ft.TextButton("Refresh", on_click=lambda _: self.installed()),
                        ft.TextButton("Import file", on_click=lambda _: self.pick_file()),
                        ft.TextButton("Build native mods", on_click=lambda _: self.confirm_build()),
                        ft.TextButton("Use bundled client", on_click=lambda _: self.use_bundled())],
                       wrap=True))
        self.manage_progress = ft.ProgressBar(visible=False, height=3)
        return self.app._page_shell(self.app._heading("Manage"), ft.Container(height=12),
            self.manager, self.manage_progress, ft.Container(height=12), self.installed_caption, self.installed_rows)

    def set_busy(self, busy):
        self.browser.disabled = self.results.disabled = self.more.disabled = busy
        self.progress.visible = busy
        self.manager.disabled = self.installed_rows.disabled = busy
        self.manage_progress.visible = busy

    def select_kind(self, _):
        if self.app.busy:
            self.category.value = self.kind
            self.app.page.update()
            return
        if self.kind != self.category.value:
            self.kind = self.category.value
            self.displayed_search = None
            self.results.controls.clear()
            self.caption.value = KINDS[self.kind]
            self.more.visible = False
        self.search()

    def pick_file(self):
        if self.app.busy:
            return
        self.pick_kind = self.manage_category.value
        extensions = {"mod": ["hmod"], "native": ["hnmod"], "resource": ["zip"], "shader": ["hshader", "zip"]}
        self.picker.pick_files(allowed_extensions=extensions[self.pick_kind], file_type=ft.FilePickerFileType.CUSTOM)

    def command(self, *args, cached_index=None, search_home=None):
        name = "horizon-launcher.exe" if os.name == "nt" else "horizon-launcher"
        candidates = [os.environ.get("HORIZON_LAUNCHER_EXE"), self.game.parent / name,
                      self.game.parent.parent.parent / "Horizon-Launcher" / name, shutil.which(name)]
        cli = next((Path(p).resolve() for p in candidates if p and Path(p).is_file()), None)
        if not cli:
            raise RuntimeError(f"Place {name} beside Crackrizon, or set HORIZON_LAUNCHER_EXE to its path.")
        self.home.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "HORIZON_HOME": str(search_home or self.game), "HORIZON_LAUNCHER_HOME": str(self.home)}
        env["HORIZON_MOD_INDEX"] = str(cached_index or REGISTRY_INDEX)
        result = subprocess.run([str(cli), "--json", *args], cwd=self.game, env=env,
            capture_output=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Horizon command failed.").strip()[-3000:])
        return result.stdout

    def installed(self):
        self.app._start_task(self.list_installed, "Reading installed content…")

    def list_installed(self):
        kind = self.manage_category.value
        rows = json.loads(self.command("mods", "list", "--instance", "horizon"))
        if kind in ("shader", "resource"):
            packs = json.loads(self.command("packs", "list", "--kind", kind, "--instance", "horizon"))
            pack_kinds = ("shader", "shaderpack") if kind == "shader" else ("resource", "resourcepack")
            managed = {Path(r["file"]).name: r["project"] for r in rows if r.get("kind") in pack_kinds}
            for p in packs:
                p.update(project=managed.get(p["file"], p["file"]), pack=True,
                         managed=p["file"] in managed,
                         number="Enabled" if p["enabled"] else "Disabled")
            self.show("Installed " + KINDS[kind].lower(), packs, installed=True)
            return
        for row in rows:
            file = Path(row.get("file") or "")
            if file.suffix.lower() == ".hnmod" or file.parent.name == "horizon-native-mods":
                row["kind"] = "native-mod"
        package_kind = "native-mod" if kind == "native" else "mod"
        rows = [r for r in rows if r.get("kind") == package_kind]
        known = {Path(r.get("file") or r["project"]).stem for r in rows}
        rows = [r for r in rows if r.get("source") not in JAVA_CATALOGS]
        native = kind == "native"
        folder = self.game / ("horizon-native-mods" if native else "horizon-mods")
        extensions = (".hnmod", ".hmod") if native else (".hmod",)
        for p in sorted(folder.glob("*")):
            if p.suffix.lower() in extensions and p.stem not in known and p.is_file():
                rows.append({"title": p.stem, "project": p.stem, "local": True, "kind": package_kind,
                             "file": str(p.relative_to(self.game)),
                             "number": "Native package · build to apply" if native else "Local content mod"})
                known.add(p.stem)
        try:
            client, _ = self.launch_paths(self.game / self.binary, None)
        except (OSError, ValueError, RuntimeError) as error:
            self.app.events.put(("log", f"Selected build unavailable: {error}. Use bundled client or rebuild."))
            client = self.game / self.binary
        manifest = client.parent / "native-mods.json"
        compiled = {}
        if native and manifest.is_file():
            for mod in json.loads(manifest.read_text("utf-8")).get("mods", []):
                compiled[mod["id"]] = mod["version"]
                if mod["id"] not in known:
                    rows.append({"project": mod["id"], "title": mod["id"], "compiled": True,
                                 "number": "Native build · " + mod["version"]})
        options = self.game / "horizon-mods.txt"
        flags = dict(line.split(":", 1) for line in options.read_text("utf-8").splitlines()
                     if line.startswith("mod.") and ":" in line) if options.is_file() else {}
        for row in rows:
            mod_id = Path(row.get("file") or row["project"]).stem
            if row.get("kind") == "native-mod" and compiled.get(mod_id) != row.get("version"):
                row["number"] = f"{row.get('number', '')} · rebuild to apply".strip(" ·")
            flag = flags.get("mod." + mod_id)
            if flag in ("true", "false"):
                row["enabled"] = flag == "true"
            if isinstance(row.get("enabled"), bool):
                state = "Enabled" if row["enabled"] else "Disabled"
                row["number"] = " · ".join(filter(None, (state, row.get("number"))))
        self.show("Installed " + KINDS[kind].lower(), rows, installed=True)

    def picked(self, event):
        if event.files:
            source = Path(event.files[0].path)
            def task():
                if self.pick_kind in ("native", "mod"):
                    self.import_content(source)
                else:
                    pack = self.add_pack(source)
                    self.manage_category.value = pack["kind"]
                self.list_installed()
            self.app._start_task(task)

    def add_pack(self, source):
        return json.loads(self.command("packs", "add", "--instance", "horizon", "--", str(source)))

    def import_content(self, source):
        native = source.suffix.lower() == ".hnmod"
        with zipfile.ZipFile(source) as archive:
            member = "horizon-native.json" if native else "horizon-mod.toml"
            if member not in archive.namelist():
                raise ValueError(f"{source.name} is missing {member}.")
            info = archive.getinfo(member)
            if info.file_size > 262144:
                raise ValueError("The mod manifest is too large.")
            content = archive.read(info).decode("utf-8")
            manifest = json.loads(content) if native else tomllib.loads(content)
            if native and f"mixins/{manifest.get('id')}/horizon-mixin.toml" not in archive.namelist():
                raise ValueError("The .hnmod has no desktop mixin manifest for its mod ID.")
            if not native and ("native" in manifest or "horizon-native.json" in archive.namelist()
                    or "Cargo.toml" in archive.namelist()):
                raise ValueError("This is a native mod. Install it through the registry and build it.")
        project = manifest.get("id")
        if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,63}", project):
            raise ValueError("The package has an invalid Horizon mod ID.")
        folder = self.game / ("horizon-native-mods" if native else "horizon-mods")
        folder.mkdir(exist_ok=True)
        dest = folder / (project + (".hnmod" if native else ".hmod"))
        if dest.exists():
            raise ValueError(f"{project} is already installed. Remove it before importing another version.")
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as f:
            temp = Path(f.name)
        try:
            shutil.copyfile(source, temp)
            os.link(temp, dest)
            try:
                self.set_enabled(project, True)
            except OSError:
                dest.unlink()
                raise
        finally:
            temp.unlink(missing_ok=True)

    def set_enabled(self, project, enabled):
        self.set_flags({project}, enabled)

    def disable_java_mods(self):
        manifest = self.game / "mod-browser.json"
        if not manifest.is_file():
            return
        data = json.loads(manifest.read_text("utf-8"))
        entries = data["entries"] if isinstance(data, dict) else data
        legacy, horizon = set(), set()
        for entry in entries:
            if entry.get("kind") not in ("mod", "native-mod") or not entry.get("file"):
                continue
            file = Path(entry["file"])
            if file.parent != Path("horizon-mods"):
                continue
            mod_id = file.stem if file.suffix.lower() == ".hmod" else file.name
            (legacy if entry.get("source") in JAVA_CATALOGS else horizon).add(mod_id)
        if legacy - horizon:
            self.set_flags(legacy - horizon, False)

    def set_flags(self, projects, enabled):
        if not all(re.fullmatch(r"[a-zA-Z0-9_.-]+", project) for project in projects):
            raise ValueError("Invalid mod ID.")
        path = self.game / "horizon-mods.txt"
        lines = path.read_text("utf-8").splitlines() if path.exists() else []
        keys = {"mod." + project for project in projects}
        current = [line for line in lines if line.partition(":")[0] in keys]
        expected = sorted(key + ":" + str(enabled).lower() for key in keys)
        if sorted(current) == expected:
            return
        lines = [line for line in lines if line.partition(":")[0] not in keys] + expected
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.game, delete=False) as f:
            f.write("\n".join(lines) + "\n")
        Path(f.name).replace(path)

    def search(self, refresh=False, more=False):
        if self.app.busy:
            return
        key = self.displayed_search if more else (self.kind, (self.query.value or "").strip())
        if key is None:
            return
        fresh = monotonic() < self.catalog_expires
        if fresh and not refresh and not more and key in self.search_results:
            self.search_results.move_to_end(key)
            if key != self.displayed_search:
                self.show_search(key)
            return

        def task():
            kind, query = key
            args = ["mods", "search", "--instance", "horizon", "--source", "horizon", "--kind",
                    "mod" if kind == "native" else kind]
            if query:
                args.extend(["--", query])
            previous = self.search_results[key][0] if more and key in self.search_results else []
            # Keep pagination on the same snapshot even if the catalog expires between clicks.
            initial = None
            if refresh or (not fresh and not previous) or self.catalog is None:
                initial = self.refresh_catalog(args)
                previous = []
            if initial is not None:
                rows = initial
                if kind in ("mod", "native"):
                    rows = [r for r in rows if any(s.lower().startswith("native code:")
                            for s in r.get("about", [])) == (kind == "native")]
                has_more = False
            else:
                rows, has_more = self.search_page(args, kind, previous)
            self.search_results[key] = (previous + rows, has_more)
            self.search_results.move_to_end(key)
            if len(self.search_results) > 24:
                self.search_results.popitem(last=False)
            self.show_search(key, append_from=len(previous))
        self.app._start_task(task, "Searching the Horizon catalog…")

    def refresh_catalog(self, args):
        try:
            initial = json.loads(self.command(*args))
            offline = False
        except RuntimeError:
            if not self.index_path.is_file():
                raise
            initial = None
            offline = True
        self.catalog = json.loads(self.index_path.read_bytes()) if self.index_path.is_file() else None
        self.catalog_offline = offline
        self.catalog_expires = monotonic() + (60 if offline else CATALOG_TTL)
        self.search_results.clear()
        # Older CLI versions may return search results without writing an index.
        return initial if self.catalog is None else None

    def search_page(self, args, kind, previous):
        kinds = {"mod": ("mod",), "native": ("native-mod",), "shader": ("shader", "shaderpack"),
                 "resource": ("resource", "resourcepack")}
        seen = {item["id"] for item in previous}
        projects = {p["id"]: p for p in self.catalog["projects"]
                    if p["kind"] in kinds[kind] and p["id"] not in seen}
        if not projects:
            return [], False
        # Horizon ranks compatible results and limits each search. Exclude earlier pages first.
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            subset = scratch / "category.json"
            subset.write_text(json.dumps({**self.catalog, "projects": list(projects.values())}), encoding="utf-8")
            lock = self.game / "research-lock.json"
            if lock.is_file():
                shutil.copyfile(lock, scratch / lock.name)
            rows = json.loads(self.command(*args, cached_index=subset, search_home=scratch))
        for item in rows:
            origin = projects[item["id"]].get("source") or ""
            if re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+(?:/(?:tree|commit|releases/tag)/[\w./-]+)?/?", origin):
                item["author_url"] = origin
            item["cached"] = self.catalog_offline
        return rows, len(rows) == SEARCH_LIMIT and len(rows) < len(projects)

    def show_search(self, key, append_from=0):
        self.displayed_search = key
        rows, has_more = self.search_results[key]
        label = KINDS[key[0]]
        title = f"{label} · cached catalog" if self.catalog_offline else f"Horizon · {label}"
        self.show(title, rows, installed=False, has_more=has_more, append_from=append_from)

    def install_author_release(self, project, origin):
        index = json.loads((self.game / "mod-browser" / "horizon-index.json").read_bytes())
        entry = next((p for p in index["projects"] if p["id"] == project), None)
        if not entry:
            raise ValueError("This project has left the cached catalog. Browse again to refresh the listing.")
        repo = "/".join(origin.split("/")[3:5])
        with requests.get(f"https://api.github.com/repos/{repo}/releases",
                          params={"per_page": 100}, timeout=20) as response:
            response.raise_for_status()
            assets = [a for release in response.json() if not release["draft"] for a in release["assets"]]
        for version in entry["versions"]:
            file = version["file"]
            expected_digest = "sha256:" + file["sha256"]
            asset = next((a for a in assets if a["size"] == file["size"]
                          and a["browser_download_url"].startswith(f"https://github.com/{repo}/releases/download/")
                          and (a.get("digest") == expected_digest or
                               (not a.get("digest") and a["name"] == file["name"]))), None)
            if asset:
                file["url"] = asset["browser_download_url"]
                entry["versions"] = [version]
                break
        else:
            raise ValueError("The author has no release matching this catalog entry. You can import a local file instead.")
        # Horizon verifies the original registry checksum, signature and revocations.
        with tempfile.TemporaryDirectory() as folder:
            catalog = Path(folder) / "index.json"
            catalog.write_text(json.dumps(index), encoding="utf-8")
            self.command("mods", "install", "--instance", "horizon", "--source", "horizon",
                         "--", project, cached_index=catalog)

    def show(self, title, rows, installed, has_more=False, append_from=0):
        def render():
            caption = self.installed_caption if installed else self.caption
            results = self.installed_rows if installed else self.results
            caption.value = f"{title} ({len(rows)})"
            if not installed:
                self.more.visible = has_more
            if append_from:
                results.controls.extend(self.row(item, installed) for item in rows[append_from:])
            else:
                results.controls = [self.row(item, installed) for item in rows] or [
                    ft.Text("No matching items.", color="#C5BFD2")]
        self.app.events.put(("ui", render))

    def row(self, item, installed):
        project = item["project" if installed else "id"]
        def change(action):
            def run():
                if action == "install":
                    try:
                        self.command("mods", "install", "--instance", "horizon", "--source", "horizon",
                                     "--", project, cached_index=self.game / "mod-browser" / "horizon-index.json"
                                     if item.get("cached") else None)
                    except RuntimeError as error:
                        message = str(error).lower()
                        if not item.get("author_url") or not any(reason in message for reason in (
                                "404", "410", "no connection:", "timed out", "dns failed")):
                            raise
                        self.install_author_release(project, item["author_url"])
                elif item.get("pack") and not (action == "remove" and item["managed"]):
                    verb = {"enable": "on", "disable": "off", "remove": "remove"}[action]
                    self.command("packs", verb, "--kind", item["kind"], "--instance", "horizon", "--", item["file"])
                elif item.get("local") or item.get("compiled"):
                    self.set_enabled(project, action == "enable")
                    if action == "remove" and item.get("local"):
                        (self.game / item["file"]).unlink(missing_ok=True)
                else:
                    args = ["mods", action, "--instance", "horizon"]
                    self.command(*args, "--", project)
                if self.app.current_page == "manage":
                    self.list_installed()
                self.app.events.put(("log_success", f"{action.capitalize()} completed for {project}."))
                self.app.events.put(("status", f"{item['title']} · {action} completed"))
            self.app._start_task(run)
        def confirm_install(_):
            details = "\n".join(filter(None, [item.get("reason"), *item.get("about", [])]))
            if item.get("author_url"):
                details = (details + "\n\nIf the registry download is unavailable, the launcher "
                           "checks the author's release against the cached registry checksum. "
                           "\n\n" + item["author_url"]).strip()
            self.app._confirm("Install " + item["title"], details or "Install in this Horizon instance?",
                              lambda: change("install"), "Install")
        buttons = []
        if installed:
            if item.get("enabled") is not True:
                buttons.append(ft.TextButton("Enable", on_click=lambda _: change("enable")))
            if item.get("enabled") is not False:
                buttons.append(ft.TextButton("Disable", on_click=lambda _: change("disable")))
            if not item.get("compiled"):
                buttons.append(ft.TextButton("Remove", on_click=lambda _: self.app._confirm(
                    "Remove from instance", f"Remove {item['title']} from this instance?",
                    lambda: change("remove"), "Remove", danger=True)))
        else:
            buttons.append(ft.TextButton("Install", on_click=confirm_install))
        details = [ft.Text(item["title"], weight=ft.FontWeight.BOLD)]
        if item.get("summary"):
            details.append(ft.Text(item["summary"], size=12))
        if installed and item.get("number"):
            details.append(ft.Text(item["number"], size=12))
        return ft.Container(bgcolor="#DF28243D", border_radius=12, padding=14, content=ft.Row([
            ft.Column(details, expand=True, spacing=3), *buttons]))

    def confirm_build(self):
        self.app._confirm("Build native mods",
            "Horizon will download the base build and Rust tools if needed, compile the enabled "
            "native mods, and use the result on your next launch. The mods run with your user permissions. "
            "Only continue if you trust their source.", lambda: self.app._start_task(self.build), "Build")

    def build(self):
        native_folder = self.game / "horizon-native-mods"
        if not any(native_folder.glob("*.hnmod")) and not any(native_folder.glob("*.hmod")):
            raise ValueError("Install a native mod package before building. Content mods load on the next game launch.")
        manifest = self.game / "native-mods.json"
        base = json.loads(manifest.read_text("utf-8")).get("base") if manifest.is_file() else None
        versions = json.loads(self.command("versions", "--installed"))
        if base and base not in versions["installed"]:
            if not self.reuse_base(base):
                self.command("install", base)
        elif not base and not versions["installed"]:
            self.command("install", "latest")
        args = ["native-build", "--instance", "horizon", "--trust-native-code"]
        if base:
            args.extend(["--version", base])
        self.app.events.put(("status", "Compiling native mods"))
        self.command(*args)
        instances = json.loads(self.command("instance", "list"))
        tag = next(i["version"] for i in instances if i["id"] == "horizon")
        directory = self.build_directory(tag)
        version = json.loads((directory / "research-lock.json").read_text("utf-8"))["minecraft"]["version"]
        if not (directory / ".local" / "horizon-client" / version / "horizon-resources.json").is_file():
            self.app.events.put(("status", "Preparing the native build's game resources"))
            self.command("prepare", "--version", tag, "--minecraft", str(self.app._selected_paths()))
        self.build_paths(directory)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.home, delete=False) as f:
            f.write(tag)
        Path(f.name).replace(self.selection)
        self.app.events.put(("status", f"Native build ready: {tag}"))

    def reuse_base(self, tag):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", tag):
            raise ValueError("Invalid native base version in the game manifest.")
        roots = [os.environ.get("HORIZON_LAUNCHER_HOME")]
        if os.name == "nt":
            roots.append(Path(os.environ["APPDATA"]) / "horizon-launcher")
        else:
            roots.append(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "horizon-launcher")
        for root in filter(None, roots):
            source = Path(root) / "versions" / tag
            if not all((source / name).is_file() for name in (self.binary, "research-lock.json", ".installed")):
                continue
            versions = self.home / "versions"
            versions.mkdir(parents=True, exist_ok=True)
            target = versions / tag
            if target.exists():
                return False
            self.app.events.put(("log", f"Reusing installed Horizon base {tag}."))
            with tempfile.TemporaryDirectory(dir=self.home) as temp:
                stage = Path(temp) / tag
                shutil.copytree(source, stage, ignore=lambda path, names: [".local"] if Path(path) == source else [])
                stage.rename(target)
            return True
        return False

    def build_directory(self, tag):
        if not isinstance(tag, str) or not re.fullmatch(r"native-[A-Za-z0-9_.-]+", tag):
            raise RuntimeError("Horizon did not select a native build. The current client is still active.")
        return self.home / "versions" / tag

    def build_paths(self, directory):
        binary = directory / self.binary
        version = json.loads((directory / "research-lock.json").read_text("utf-8"))["minecraft"]["version"]
        resources = directory / ".local" / "horizon-client" / version
        if not binary.is_file() or not (resources / "horizon-resources.json").is_file():
            raise RuntimeError("Native build is incomplete. Rebuild it or choose Use bundled client.")
        return binary, resources

    def launch_paths(self, client, resources):
        if self.selection.is_file():
            return self.build_paths(self.build_directory(self.selection.read_text("utf-8").strip()))
        return client, resources

    def use_bundled(self):
        if not self.app.busy:
            self.selection.unlink(missing_ok=True)
            self.app.events.put(("status", "Bundled Horizon client selected"))
