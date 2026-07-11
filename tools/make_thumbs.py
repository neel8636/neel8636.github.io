#!/usr/bin/env python3
"""Generate 128x128 accordion thumbnails for the six project rows.

For ASN we go back to bundle-original.html (the deployed WebP is already
128x67 — the app screenshot got downsized in the first build; too small
to crop meaningfully). For the other five we crop from the current WebP,
which is 1400px on the long edge — plenty for a 128px thumb at ~11x
downsample.

Crop rules:
  ASN         — a fixed rectangle over the app's stat tiles + start of
                the file list. Deliberately off-center because a center
                crop hits blank data cells.
  Others      — center-square then resize. Photos are already composed
                subject-centered; the dashboard's biggest chart lives
                near the middle.
"""
from __future__ import annotations
import base64, gzip, io, json, re
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG  = ROOT / "assets" / "img"

# --- ASN: decode 1902x992 PNG from the bundle -----------------------------
BUNDLE = (ROOT / "bundle-original.html").read_text(encoding="utf-8")
manifest = json.loads(
    re.search(r'<script[^>]*type="__bundler/manifest"[^>]*>(.*?)</script>', BUNDLE, re.S).group(1)
)
def decode(uid: str) -> bytes:
    a = manifest[uid]
    raw = base64.b64decode(a["data"])
    return gzip.decompress(raw) if a.get("compressed") else raw

def square_center_crop(im: Image.Image) -> Image.Image:
    w, h = im.size
    s = min(w, h)
    left = (w - s) // 2
    top  = (h - s) // 2
    return im.crop((left, top, left + s, top + s))

def save_thumb(im: Image.Image, name: str) -> None:
    im = im.resize((128, 128), Image.LANCZOS)
    out = IMG / f"{name}-thumb.webp"
    im.save(out, "WEBP", quality=82, method=6)
    print(f"  {out.name:<48s} {out.stat().st_size:>6,} bytes")

# --- ASN thumbnail --------------------------------------------------------
ASN_UUID = "68203fda-004e-4d05-bc96-933b33f61de5"
asn_full = Image.open(io.BytesIO(decode(ASN_UUID)))       # 1902x992 PNG
# Distinctive region: title bar + 4 stat tiles + top of FILES list.
# Coordinates picked by inspecting the original: title text sits at ~y=50,
# stat tiles at y=170-250 spanning x=10-400. Crop a 480x480 square that
# frames title + stats + the file-list header underneath.
asn_crop = asn_full.crop((0, 20, 480, 500))
save_thumb(asn_crop, "asn-validator")

# --- The five photo/dashboard rows: center-square crop from the WebP ------
photo_rows = [
    "ai-vision-box-classification",
    "supplier-compliance-dashboard-power-bi",
    "dot-smart-shelf-device-with-companion-dashboard-app",
    "tng-team-with-prototype",
    "spicemate-loaded-rack-prototype",
]
for name in photo_rows:
    im = Image.open(IMG / f"{name}.webp")
    save_thumb(square_center_crop(im), name)

print("\ndone. verify visually and adjust crops if any read poorly at 64px.")
