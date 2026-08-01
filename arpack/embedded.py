from __future__ import annotations

import json
import re
import tomllib
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

_PLACEHOLDER_RE = re.compile(r"^\$\{[^}]+\}$")


@dataclass(frozen=True)
class EmbeddedModMetadata:
    mod_id: str
    name: str
    version: str
    loader: str


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _manifest_attributes(archive: zipfile.ZipFile) -> dict[str, str]:
    name = "META-INF/MANIFEST.MF"
    if name not in archive.namelist():
        return {}

    text = archive.read(name).decode("utf-8", errors="replace")
    logical_lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith(" ") and logical_lines:
            logical_lines[-1] += line[1:]
        else:
            logical_lines.append(line)

    result: dict[str, str] = {}
    for line in logical_lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip().casefold()] = value.strip()
    return result


def _display_name(value: Any, mod_id: str, fallback_name: str) -> str:
    name = _clean(value)
    if not name or _PLACEHOLDER_RE.fullmatch(name):
        return mod_id or fallback_name
    return name


def _resolve_version(value: Any, archive: zipfile.ZipFile, filename: str) -> str:
    version = _clean(value)
    if version and not _PLACEHOLDER_RE.fullmatch(version):
        return version

    attributes = _manifest_attributes(archive)
    for key in (
        "implementation-version",
        "specification-version",
        "bundle-version",
        "jar-version",
    ):
        candidate = _clean(attributes.get(key))
        if candidate:
            return candidate

    return PurePosixPath(filename).stem


def _first_mod_table(data: dict[str, Any]) -> dict[str, Any]:
    mods = data.get("mods", [])
    if isinstance(mods, list):
        for item in mods:
            if isinstance(item, dict):
                return item
    return {}


def read_embedded_mod_metadata(data: bytes, filename: str) -> EmbeddedModMetadata:
    """Read the primary mod identity from a JAR.

    The parser intentionally focuses on the metadata formats used by the current
    Arcomua product lines. Unknown or malformed JARs fall back to a stable
    filename-based identity so they still participate in changelog diffs.
    """

    fallback_name = PurePosixPath(filename).stem
    fallback = EmbeddedModMetadata(
        mod_id="",
        name=fallback_name,
        version=fallback_name,
        loader="unknown",
    )

    try:
        with zipfile.ZipFile(BytesIO(data), "r") as archive:
            names = set(archive.namelist())

            if "fabric.mod.json" in names:
                try:
                    payload = json.loads(archive.read("fabric.mod.json").decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return fallback
                if not isinstance(payload, dict):
                    return fallback
                mod_id = _clean(payload.get("id"))
                name = _display_name(payload.get("name"), mod_id, fallback_name)
                version = _resolve_version(payload.get("version"), archive, filename)
                return EmbeddedModMetadata(mod_id, name, version, "fabric")

            if "quilt.mod.json" in names:
                try:
                    payload = json.loads(archive.read("quilt.mod.json").decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return fallback
                loader = payload.get("quilt_loader", {}) if isinstance(payload, dict) else {}
                if not isinstance(loader, dict):
                    return fallback
                metadata = loader.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                mod_id = _clean(loader.get("id"))
                name = _display_name(metadata.get("name"), mod_id, fallback_name)
                version = _resolve_version(loader.get("version"), archive, filename)
                return EmbeddedModMetadata(mod_id, name, version, "quilt")

            metadata_path = None
            loader_name = ""
            if "META-INF/neoforge.mods.toml" in names:
                metadata_path = "META-INF/neoforge.mods.toml"
                loader_name = "neoforge"
            elif "META-INF/mods.toml" in names:
                metadata_path = "META-INF/mods.toml"
                loader_name = "forge"

            if metadata_path:
                try:
                    payload = tomllib.loads(
                        archive.read(metadata_path).decode("utf-8-sig", errors="replace")
                    )
                except tomllib.TOMLDecodeError:
                    return fallback
                mod = _first_mod_table(payload)
                mod_id = _clean(mod.get("modId"))
                name = _display_name(mod.get("displayName"), mod_id, fallback_name)
                version = _resolve_version(mod.get("version"), archive, filename)
                return EmbeddedModMetadata(mod_id, name, version, loader_name)

            if "mcmod.info" in names:
                try:
                    payload = json.loads(archive.read("mcmod.info").decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return fallback
                if isinstance(payload, list):
                    mod = next((item for item in payload if isinstance(item, dict)), {})
                elif isinstance(payload, dict) and isinstance(payload.get("modList"), list):
                    mod = next(
                        (item for item in payload["modList"] if isinstance(item, dict)),
                        {},
                    )
                elif isinstance(payload, dict):
                    mod = payload
                else:
                    mod = {}
                mod_id = _clean(mod.get("modid"))
                name = _display_name(mod.get("name"), mod_id, fallback_name)
                version = _resolve_version(mod.get("version"), archive, filename)
                return EmbeddedModMetadata(mod_id, name, version, "legacy-forge")

    except (zipfile.BadZipFile, OSError):
        return fallback

    return fallback
