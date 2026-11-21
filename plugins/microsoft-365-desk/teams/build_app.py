# SPDX-License-Identifier: Apache-2.0
"""Build the IT desk's Teams app package: `manifest.json` with your ids filled in, and its two icons.

    python build_app.py --client-id <bot's Entra application id> --developer "Contoso IT" \
        --website https://contoso.example -o it-desk.zip

The package holds exactly the three files Teams expects at its root: `manifest.json`, `color.png`
(192x192) and `outline.png` (32x32, white on transparent). The icons are generated (a plain square and
a white ring); replace `color.png` and `outline.png` beside this script with your own before building
to use them instead. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: Every placeholder of manifest.json, and the argument that fills it.
PLACEHOLDERS = {
    "<client id>": "client_id",
    "<your organisation>": "developer",
    "https://<your organisation's site>": "website",
}


def _png(width: int, height: int, pixels: list[bytes]) -> bytes:
    """An RGBA PNG from rows of `width * 4` bytes."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def color_icon(rgb: tuple[int, int, int] = (0x2B, 0x57, 0x9A)) -> bytes:
    """192x192, an opaque square of `rgb` with a white ring: Teams' full-colour icon."""
    size, rows = 192, []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            distance = ((x - 95.5) ** 2 + (y - 95.5) ** 2) ** 0.5
            row += bytes((255, 255, 255, 255)) if 52 <= distance <= 64 else bytes((*rgb, 255))
        rows.append(bytes(row))
    return _png(size, size, rows)


def outline_icon() -> bytes:
    """32x32, a white ring on a transparent background: Teams' outline icon."""
    size, rows = 32, []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            distance = ((x - 15.5) ** 2 + (y - 15.5) ** 2) ** 0.5
            row += bytes((255, 255, 255, 255)) if 9 <= distance <= 12 else bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return _png(size, size, rows)


def manifest(client_id: str, developer: str, website: str) -> str:
    """manifest.json with its placeholders filled; refuses an id that is not a GUID, an `http:` site, and
    any placeholder left."""
    if not GUID.match(client_id):
        raise ValueError(f"the client id must be the bot's Entra application id (a GUID): {client_id!r}")
    if not website.startswith("https://") or "<" in website or ">" in website:
        raise ValueError(f"the website must be an https:// URL: {website!r}")
    if not developer.strip() or "<" in developer or ">" in developer:
        raise ValueError("the developer's name is required")
    text = (HERE / "manifest.json").read_text(encoding="utf-8")
    values = {"client_id": client_id.lower(), "developer": developer.strip(), "website": website.rstrip("/")}
    for placeholder, argument in PLACEHOLDERS.items():
        text = text.replace(placeholder, values[argument])
    if re.search(r"<[^>]+>", text):
        raise ValueError("manifest.json still holds a placeholder")
    json.loads(text)
    return text


def build(client_id: str, developer: str, website: str, output: Path) -> Path:
    """The zip Teams imports: manifest.json, color.png, outline.png, at its root."""
    own_color, own_outline = HERE / "color.png", HERE / "outline.png"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("manifest.json", manifest(client_id, developer, website))
        package.writestr("color.png", own_color.read_bytes() if own_color.is_file() else color_icon())
        package.writestr("outline.png", own_outline.read_bytes() if own_outline.is_file() else outline_icon())
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the IT desk's Teams app package.")
    parser.add_argument("--client-id", required=True, help="the bot's Entra application (client) id")
    parser.add_argument("--developer", required=True, help="your organisation's name, as Teams shows it")
    parser.add_argument("--website", required=True, help="your organisation's site (https://…)")
    parser.add_argument("-o", "--output", type=Path, default=Path("it-desk.zip"))
    args = parser.parse_args(argv)
    try:
        built = build(args.client_id, args.developer, args.website, args.output)
    except ValueError as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 2
    print(f"{built}: upload it in Teams (Apps → Manage your apps → Upload an app), then add it to the team")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
