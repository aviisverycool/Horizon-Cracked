"""Prepare the files pinned by the bundled Horizon release."""
import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests


def download_verified(url, destination, algorithm, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.new(algorithm)
    temporary = None
    try:
        with requests.get(url, timeout=(10, 60), stream=True) as response:
            response.raise_for_status()
            with NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                for block in response.iter_content(256 * 1024):
                    digest.update(block)
                    output.write(block)
        if digest.hexdigest() != expected:
            raise ValueError(f"The checksum for {destination.name} did not match the release.")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def matches_file(path, algorithm, expected):
    if not path.is_file():
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, algorithm).hexdigest() == expected


class GameFiles:
    def __init__(self, game, lock, events):
        self.game = game
        self.lock = lock
        self.minecraft = lock["minecraft"]
        self.version = self.minecraft["version"]
        self.events = events

    def log(self, message):
        self.events.put(("log", message))

    def manifest(self, minecraft_dir):
        path = minecraft_dir / "versions" / self.version / f"{self.version}.json"
        digest = self.minecraft["manifest_sha1"]
        if not path.is_file():
            self.log(f"Downloading the Minecraft {self.version} manifest…")
            url = f"https://piston-meta.mojang.com/v1/packages/{digest}/{self.version}.json"
            download_verified(url, path, "sha1", digest)
        data = path.read_bytes()
        accepted = {digest, *self.minecraft.get("accepted_manifest_sha1", [])}
        if hashlib.sha1(data).hexdigest() not in accepted:
            raise ValueError(f"Horizon requires an accepted Minecraft {self.version} manifest. Check {path}.")
        return json.loads(data)

    def ensure_minecraft(self, minecraft_dir):
        version_dir = minecraft_dir / "versions" / self.version
        missing_manifest = not (version_dir / f"{self.version}.json").is_file()
        data = self.manifest(minecraft_dir)
        client = version_dir / f"{self.version}.jar"
        if missing_manifest or not matches_file(client, "sha1", self.minecraft["client_sha1"]):
            from minecraft_launcher_lib.install import install_minecraft_version

            self.log(f"Installing Minecraft {self.version} files…")
            install_minecraft_version(self.version, minecraft_dir, callback={"setStatus": self.log})
        server = self.game / ".local" / "minecraft" / self.version / "jars" / "server.jar"
        expected = self.minecraft["server_sha1"]
        if not matches_file(server, "sha1", expected):
            source = data["downloads"]["server"]
            if source["sha1"] != expected:
                raise ValueError("The server checksum in the Minecraft manifest differs from Horizon's release.")
            self.log(f"Downloading the Minecraft {self.version} server…")
            download_verified(source["url"], server, "sha1", expected)
        return server

    def ensure_java(self, minecraft_dir):
        from minecraft_launcher_lib.runtime import get_executable_path, install_jvm_runtime

        java = self.manifest(minecraft_dir).get("javaVersion", {})
        component = java.get("component")
        if not component or java.get("majorVersion") != self.minecraft["java_major"]:
            raise ValueError("The Minecraft manifest does not declare Horizon's required Java runtime.")
        # Mojang's installer writes .version after it finishes installing the runtime files.
        executable = get_executable_path(component, minecraft_dir)
        if executable and (Path(executable).parents[2] / ".version").is_file():
            return
        self.log(f"Installing {component} for this computer…")
        install_jvm_runtime(component, minecraft_dir, callback={"setStatus": self.log})
        if not get_executable_path(component, minecraft_dir):
            raise RuntimeError(f"Minecraft could not install {component} for this computer.")

    def install_libraries(self, minecraft_dir):
        for key, folder, filename in (("vineflower", "vineflower", "vineflower.jar"),
                                      ("jadx_headless_mcp", "jadx", "jadx-headless-mcp.jar")):
            tool = self.lock[key]
            destination = self.game / ".local" / folder / filename
            if not matches_file(destination, "sha256", tool["sha256"]):
                self.log(f"Downloading {filename} {tool['version']}…")
                download_verified(tool["url"], destination, "sha256", tool["sha256"])
        self.ensure_java(minecraft_dir)
