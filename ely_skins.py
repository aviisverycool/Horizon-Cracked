import os
import re
import struct
import tempfile
from pathlib import Path

import requests


def _profile_directory(config_dir, name):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
        raise ValueError("Invalid offline profile name.")
    return config_dir / "skins" / ("player-" + name.lower())


def _replace_file(path, data):
    stream = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def download_skin(config_dir, name):
    directory = _profile_directory(config_dir, name)
    base = "https://skinsystem.ely.by"
    with requests.get(f"{base}/textures/{name}", timeout=10) as response:
        response.raise_for_status()
        skin = None if response.status_code == 204 else response.json().get("SKIN")
    if not skin:
        raise ValueError(f"Ely.by has no skin for {name}.")
    model = "slim" if skin.get("metadata", {}).get("model") == "slim" else "classic"
    with requests.get(f"{base}/skins/{name}.png", timeout=10, stream=True) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(8192):
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise ValueError("Ely.by returned an oversized skin.")
    if (len(data) < 45 or not data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            or struct.unpack(">II", data[16:24]) not in ((64, 64), (64, 32))
            or not data.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82")):
        raise ValueError("Ely.by returned an invalid skin PNG.")
    directory.mkdir(parents=True, exist_ok=True)
    _replace_file(directory / f"{model}.png", data)
    _replace_file(directory / "model", model.encode("ascii"))


def cached_skin(config_dir, name):
    directory = _profile_directory(config_dir, name)
    try:
        model = (directory / "model").read_bytes()
    except FileNotFoundError:
        return None
    if model not in (b"classic", b"slim"):
        return None
    model = model.decode("ascii")
    texture = directory / f"{model}.png"
    return texture if texture.is_file() else None


def skin_environment(config_dir, name):
    texture = cached_skin(config_dir, name)
    if texture is not None:
        return {**os.environ, "HORIZON_OFFLINE_SKIN": str(texture.resolve()),
                "HORIZON_OFFLINE_SKIN_MODEL": texture.stem}
    return None
