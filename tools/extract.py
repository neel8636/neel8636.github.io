#!/usr/bin/env python3
"""Extract Claude Design self-extracting bundle into a static site.

Reads ./index.html (11 MB self-extracting bundle) and writes a plain static
site next to it: dist/index.html, dist/favicon.svg, dist/assets/{img,fonts,js}.

The bundle has three <script type="__bundler/..."> blocks:
  manifest      : { uuid: { mime, compressed, data (base64) } } — 37 assets
  ext_resources : [{ id: <cdn url>, uuid }] — React & ReactDOM UMD refs
  template      : JSON-encoded string of the real <html> document, with
                  every asset URL replaced by its uuid.

We decode every asset, name it meaningfully, write it under assets/, and
string-replace each uuid in the template with a RELATIVE path (./assets/...).
Head + <img> attributes are patched separately. Image optimization runs in a
follow-up pass (optimize_images.py) so we can compare byte sizes.
"""
from __future__ import annotations
import base64, gzip, hashlib, io, json, mimetypes, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC  = ROOT / "index.html"
OUT  = ROOT / "dist"
ASSETS = OUT / "assets"

# ---------- helpers ----------

def slug(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-.")
    return s[:maxlen].lower() or "asset"

def block(html: str, kind: str) -> str:
    m = re.search(
        r'<script[^>]*type="' + re.escape(kind) + r'"[^>]*>(.*?)</script>',
        html, re.S,
    )
    if not m:
        sys.exit(f"missing bundler block: {kind}")
    return m.group(1)

EXT_FOR_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "font/woff2": ".woff2",
    "text/javascript": ".js",
    "application/javascript": ".js",
}
def ext_for(mime: str) -> str:
    return EXT_FOR_MIME.get(mime) or mimetypes.guess_extension(mime) or ".bin"

FOLDER_FOR_MIME = {
    "image/png": "img", "image/jpeg": "img", "image/webp": "img",
    "image/svg+xml": "img",
    "font/woff2": "fonts",
    "text/javascript": "js", "application/javascript": "js",
}

# ---------- naming ----------
# Alt text / CSS context yields human-friendly filenames. Duplicates collapse
# to the same file (many uuids appear twice — desktop + mobile variant).

def img_names_from_template(template: str) -> dict[str, str]:
    """uuid -> slug based on first non-empty alt in template img tags."""
    names: dict[str, str] = {}
    for m in re.finditer(r"<img\b[^>]*>", template):
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag)
        alt = re.search(r'alt="([^"]*)"', tag)
        if not src: continue
        uid = src.group(1)
        # Prefer the first non-empty alt we see for a given uuid
        if uid in names and names[uid]:
            continue
        names[uid] = slug(alt.group(1)) if alt and alt.group(1) else ""
    return names

def font_names_from_template(template: str) -> dict[str, str]:
    """uuid -> slug from @font-face family/subset, walking the stylesheet."""
    names: dict[str, str] = {}
    # Every @font-face block references its source via url("uuid")
    for m in re.finditer(
        r"@font-face\s*\{([^}]*)\}", template
    ):
        body = m.group(1)
        url = re.search(r'url\("([0-9a-f-]{36})"\)', body)
        if not url: continue
        uid = url.group(1)
        fam = re.search(r"font-family:\s*'([^']+)'", body)
        family = slug(fam.group(1)) if fam else "font"
        # subset is annotated in the comment just before the block; we
        # match on the *preceding* comment via a wider look-back below.
        names.setdefault(uid, family)
    # Attach subset hint (latin / latin-ext / cyrillic / etc.) from preceding /* comment */
    for m in re.finditer(
        r"/\*\s*([a-z-]+)\s*\*/\s*@font-face\s*\{([^}]*)\}", template
    ):
        subset = m.group(1)
        body = m.group(2)
        url = re.search(r'url\("([0-9a-f-]{36})"\)', body)
        if not url: continue
        uid = url.group(1)
        # Fonts are reused across weights but keyed on subset — first subset wins.
        if uid in names and "-" not in names[uid]:
            names[uid] = f"{names[uid]}-{subset}"
    return names

def js_names_from_ext(ext_resources: list, manifest: dict) -> dict[str, str]:
    """uuid -> URL basename from ext_resources (React UMDs)."""
    out: dict[str, str] = {}
    for e in ext_resources:
        base = e["id"].rsplit("/", 1)[-1]      # react.production.min.js
        out[e["uuid"]] = base.removesuffix(".js")
    return out

def peek_js_role(data: bytes) -> str:
    head = data[:2000].decode("utf-8", errors="replace")
    if "dc-runtime/src" in head: return "dc-runtime"
    if "image-slot"    in head: return "image-slot"
    if "react-dom"     in head: return "react-dom.production.min"
    if "react.production" in head: return "react.production.min"
    return ""

# ---------- main ----------

def main() -> None:
    html = SRC.read_text(encoding="utf-8")
    manifest      = json.loads(block(html, "__bundler/manifest"))
    ext_resources = json.loads(block(html, "__bundler/ext_resources"))
    template      = json.loads(block(html, "__bundler/template"))

    # Fresh output
    if OUT.exists(): shutil.rmtree(OUT)
    for sub in ("img", "fonts", "js"):
        (ASSETS / sub).mkdir(parents=True, exist_ok=True)

    img_names  = img_names_from_template(template)
    font_names = font_names_from_template(template)
    js_names   = js_names_from_ext(ext_resources, manifest)

    # counters to disambiguate collisions
    used: dict[str, int] = {}
    def unique(stem: str, folder: str, ext: str) -> str:
        base = stem or "asset"
        n = 0
        while True:
            name = base if n == 0 else f"{base}-{n}"
            path = ASSETS / folder / (name + ext)
            key = str(path).lower()
            if key not in used:
                used[key] = 1
                return name + ext
            n += 1

    uuid_to_rel: dict[str, str] = {}

    for uid, entry in manifest.items():
        raw = base64.b64decode(entry["data"])
        if entry.get("compressed"):
            raw = gzip.decompress(raw)
        mime = entry["mime"]
        folder = FOLDER_FOR_MIME.get(mime, "misc")
        (ASSETS / folder).mkdir(parents=True, exist_ok=True)
        ext = ext_for(mime)

        if folder == "js":
            stem = js_names.get(uid) or peek_js_role(raw) or f"script-{uid[:8]}"
        elif folder == "fonts":
            stem = font_names.get(uid) or f"font-{uid[:8]}"
        elif folder == "img":
            hint = img_names.get(uid, "")
            stem = hint or f"image-{uid[:8]}"
        else:
            stem = f"asset-{uid[:8]}"

        fname = unique(stem, folder, ext)
        path  = ASSETS / folder / fname
        path.write_bytes(raw)
        rel = f"./assets/{folder}/{fname}"
        uuid_to_rel[uid] = rel

    # write mapping for the next stages
    map_path = ROOT / "tools" / "asset-map.json"
    map_path.write_text(json.dumps({
        "uuid_to_rel": uuid_to_rel,
        "ext_resources": [
            {"url": e["id"], "rel": uuid_to_rel[e["uuid"]]}
            for e in ext_resources
        ],
        "template_len": len(template),
        "asset_count": len(manifest),
    }, indent=2))

    # ----- rewrite the template: uuid -> relative path -----
    body = template
    # Longest uuids first (they're all 36 chars so ordering doesn't matter,
    # but stable order keeps diffs sane).
    for uid, rel in uuid_to_rel.items():
        body = body.replace(uid, rel)

    # sanity: no uuid should survive
    surv = [u for u in uuid_to_rel if u in body]
    if surv:
        sys.exit(f"uuid survived rewrite: {surv[:3]}")

    # save the raw (uuid-substituted) template — head/img patches happen in a
    # later stage so we can review the diff step by step.
    (ROOT / "tools" / "template.rewritten.html").write_text(body, encoding="utf-8")
    print(f"assets: {len(manifest)}  images: {sum(1 for u,e in manifest.items() if e['mime'].startswith('image/'))}  fonts: {sum(1 for u,e in manifest.items() if e['mime']=='font/woff2')}  js: {sum(1 for u,e in manifest.items() if e['mime']=='text/javascript')}")
    print(f"template rewritten -> tools/template.rewritten.html ({len(body):,} chars)")
    print(f"asset-map -> tools/asset-map.json")

if __name__ == "__main__":
    main()
