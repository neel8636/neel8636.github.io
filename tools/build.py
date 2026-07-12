#!/usr/bin/env python3
"""Build stage — turns tools/template.rewritten.html into dist/index.html.

Steps (in order):
  1. Optimize every dist/assets/img/* to WebP (quality 82, resize to 2x rendered
     CSS size, strip EXIF) and record intrinsic dimensions.
  2. Update image references in the template (`.png`/`.jpg` -> `.webp`).
  3. Patch the <html> tag with lang="en".
  4. Replace the <head> with a proper head: title, meta description, canonical,
     OG + Twitter cards, favicon link, font preloads for the two above-the-fold
     woff2 files, and window.__resources map injected BEFORE dc-runtime.
  5. Delete the Google Fonts preconnect links (fonts are self-hosted now).
  6. Add width/height/loading/decoding to every <img>; hero gets fetchpriority.
  7. Write dist/index.html, dist/favicon.svg (viewBox fix), dist/.nojekyll.
  8. Generate dist/assets/img/og-preview.png (1200x630) using PIL.

Preserves byte-for-byte: <script type="text/x-dc">, <x-dc>, <sc-for>.
"""
from __future__ import annotations
import json, re, shutil, sys, io, os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
IMG_DIR = DIST / "assets" / "img"
TEMPLATE_IN = ROOT / "tools" / "template.rewritten.html"
ASSET_MAP = json.loads((ROOT / "tools" / "asset-map.json").read_text())

SITE_URL   = "https://neel8636.github.io/"
TITLE      = "Neel Arvind Desai — Supply Chain & Logistics Packaging Engineer"
DESCRIPTION = (
    "Neel Desai — supply chain analyst and logistics packaging engineer. "
    "I find the story inside supply-chain data, then build the tool that fixes it."
)
OG_IMAGE_URL = "https://neel8636.github.io/assets/img/og-preview.png"

# --- image optimization -------------------------------------------------------

# hint width/height (in CSS px, before DPR) for select images. Unlisted images
# default to LARGE_WIDTH long-edge. Small logos default to LOGO_LONG.
HERO_RENDERED   = 300
LOGO_LONG       = 128    # 64×64 in CSS × 2 DPR
LARGE_WIDTH     = 1400   # covers 1240px section × 55% column at 2× DPR

# Timeline feature flags. Google's brand guidelines restrict the four-colour
# "G"; the video card already carries GDSC branding, so this defaults OFF.
# Flip to True to ship the mark as a chip beside CoronAI.
SHOW_GOOGLE_G   = False
SQUARE_LOGO_FILES = {
    "northeastern-university.png",
    "university-of-california-berkeley.png",
    "srm-institute-of-science-and-technology.png",
    "image-68203fda.png",  # tiny circle icon used at 16px and 64px
}
def target_width(name: str, w: int, h: int) -> int | None:
    if name == "neel-arvind-desai.jpg":
        return 600                       # rendered as 300×300 circle w/ cover
    if name in SQUARE_LOGO_FILES:
        return LOGO_LONG
    return min(LARGE_WIDTH, w)           # never upscale

def optimize_images() -> dict[str, dict]:
    """Convert every image in IMG_DIR to WebP q=82, resize per target_width().
    Returns { old_basename: {new_basename, w, h, bytes} } for src rewrite."""
    out: dict[str, dict] = {}
    total_before = total_after = 0
    for src in sorted(IMG_DIR.iterdir()):
        if src.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)   # bake in EXIF rotation
        # strip alpha for JPEG sources (PNGs with alpha keep RGBA -> lossy WebP handles it)
        w0, h0 = img.size
        tw = target_width(src.name, w0, h0) or w0
        if tw < w0:
            th = round(h0 * tw / w0)
            img = img.resize((tw, th), Image.LANCZOS)
        dst = src.with_suffix(".webp")
        # keep alpha for images that had it (PNGs)
        save_kwargs = dict(quality=82, method=6)
        img.save(dst, "WEBP", **save_kwargs)
        before = src.stat().st_size
        after  = dst.stat().st_size
        total_before += before
        total_after  += after
        src.unlink()
        out[src.name] = {
            "new_basename": dst.name,
            "w": img.size[0], "h": img.size[1],
            "bytes": after,
        }
        print(f"  {src.name:<48s} {w0}×{h0} {before:>8,} -> {dst.name} {img.size[0]}×{img.size[1]} {after:>8,}")
    print(f"  images: {total_before:,} -> {total_after:,} bytes  (saved {total_before-total_after:,})")
    return out

# --- OG preview --------------------------------------------------------------

def make_og_preview() -> None:
    """1200×630 PNG matching site palette: cream bg + terracotta monogram + name."""
    W, H = 1200, 630
    im = Image.new("RGB", (W, H), (0xF7, 0xF2, 0xE9))
    d = ImageDraw.Draw(im)
    # Terracotta ring
    cx, cy, r = 300, H // 2, 170
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0xC2, 0x68, 0x3F))
    # Try to find a serif font on the system; fall back to PIL default
    def find_font(names, size):
        candidates = [
            "C:/Windows/Fonts/georgia.ttf", "C:/Windows/Fonts/georgiab.ttf",
            "C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf",
            "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
        ]
        for c in candidates:
            if any(n in c.lower() for n in names) and os.path.exists(c):
                try: return ImageFont.truetype(c, size)
                except Exception: pass
        for c in candidates:
            if os.path.exists(c):
                try: return ImageFont.truetype(c, size)
                except Exception: pass
        return ImageFont.load_default()
    serif  = find_font(["georgia","times"], 180)
    serifs = find_font(["georgia","times"], 64)
    sans   = find_font(["segoeui","arial"], 32)
    # "ND" centered in the ring
    bb = d.textbbox((0,0), "ND", font=serif)
    tw, th = bb[2]-bb[0], bb[3]-bb[1]
    d.text((cx - tw/2 - bb[0], cy - th/2 - bb[1] - 10), "ND",
           font=serif, fill=(0xF7, 0xF2, 0xE9))
    # Name and role to the right
    x = 550
    d.text((x, 220), "Neel Arvind Desai",       font=serifs, fill=(0x2A, 0x24, 0x1C))
    d.text((x, 320), "Supply Chain Analyst",    font=sans,   fill=(0x54, 0x4B, 0x3F))
    d.text((x, 370), "Logistics Packaging Engineer", font=sans, fill=(0x54, 0x4B, 0x3F))
    # bottom rule
    d.rectangle((x, 460, x + 400, 466), fill=(0xC2, 0x68, 0x3F))
    d.text((x, 490), "neel8636.github.io", font=sans, fill=(0x54, 0x4B, 0x3F))
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    im.save(IMG_DIR / "og-preview.png", "PNG", optimize=True)

# --- favicon ------------------------------------------------------------------

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" fill="#F7F2E9"/>
  <circle cx="256" cy="230" r="118" fill="#C2683F"/>
  <text x="256" y="270" font-family="Georgia, serif" font-size="118" fill="#F7F2E9" text-anchor="middle">ND</text>
  <text x="256" y="415" font-family="Georgia, serif" font-size="46" fill="#2A241C" text-anchor="middle">Neel Desai</text>
</svg>
"""

# --- head assembly ------------------------------------------------------------

def build_head(res_map: dict[str, str], preload_fonts: list[str]) -> str:
    lines = [
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>{TITLE}</title>',
        f'<meta name="description" content="{DESCRIPTION}">',
        f'<link rel="canonical" href="{SITE_URL}">',
        '<meta name="theme-color" content="#F7F2E9">',
        '<link rel="icon" type="image/svg+xml" href="./favicon.svg">',
        # Open Graph
        '<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{SITE_URL}">',
        f'<meta property="og:title" content="{TITLE}">',
        f'<meta property="og:description" content="{DESCRIPTION}">',
        f'<meta property="og:image" content="{OG_IMAGE_URL}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        # Twitter
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{TITLE}">',
        f'<meta name="twitter:description" content="{DESCRIPTION}">',
        f'<meta name="twitter:image" content="{OG_IMAGE_URL}">',
    ]
    for f in preload_fonts:
        lines.append(f'<link rel="preload" as="font" type="font/woff2" href="{f}" crossorigin>')
    # window.__resources — MUST come before dc-runtime <script src>
    lines.append('<script>window.__resources=' + json.dumps(res_map, separators=(',', ':')) + ';</script>')
    # Additive mobile shim. Kept here as a python string so a full rebuild
    # from bundle-original.html regenerates the responsive layout too.
    lines.append(MOBILE_SHIM_CSS)
    lines.append('<script src="./assets/js/dc-runtime.js"></script>')
    lines.append('</head>')
    return "\n".join(lines)


# The mobile shim is an ADDITIVE style block — the design template has
# zero @media queries and mutates inline styles at runtime, so we
# override via attribute selectors + section ids and use !important
# throughout. Breakpoints 1024 / 768 / 480. Nothing above 1024.
MOBILE_SHIM_CSS = """<style>
@media (max-width: 1024px) {
  [style*="grid-template-columns: repeat(4, 1fr)"] {
    grid-template-columns: repeat(2, 1fr) !important;
  }
  [style*="padding: 76px 32px 80px"],
  [style*="padding: 60px 32px 56px"],
  [style*="padding: 68px 32px 72px"],
  [style*="padding: 76px 32px 40px"] {
    padding-top: 60px !important; padding-bottom: 60px !important;
    padding-left: 24px !important; padding-right: 24px !important;
  }
}
@media (max-width: 768px) {
  #top, #education, #path, #work, #skills, #contact,
  [id^="proj-"],
  [style*="max-width: 1240px"] {
    max-width: 100% !important;
    padding-left: 20px !important;  padding-right: 20px !important;
    box-sizing: border-box !important;
  }
  [style*="padding: 76px 32px 80px"],
  [style*="padding: 60px 32px 56px"],
  [style*="padding: 68px 32px 72px"],
  [style*="padding: 76px 32px 40px"] { padding: 44px 20px 44px !important; }
  #top {
    grid-template-columns: 1fr !important;
    gap: 28px !important; text-align: center !important;
  }
  #top img[alt="Neel Arvind Desai"] {
    width: min(220px, 55vw) !important; height: auto !important;
    aspect-ratio: 1 / 1 !important; margin: 0 auto !important;
  }
  [style*="grid-template-columns: auto 1fr"],
  [style*="grid-template-columns: 300px 1fr"],
  [style*="grid-template-columns: 1.3fr 1fr"],
  [style*="grid-template-columns: 1fr 1.3fr"],
  [style*="grid-template-columns: 1.15fr 1fr"],
  [style*="grid-template-columns: 1fr 1.15fr"],
  [style*="grid-template-columns: repeat(2, 1fr)"],
  [style*="grid-template-columns: repeat(3, 1fr)"],
  [style*="grid-template-columns: repeat(4, 1fr)"] {
    grid-template-columns: 1fr !important; gap: 24px !important;
  }
  [style*="grid-template-columns: 1.7fr 0.6fr 1.6fr 2.3fr"] {
    grid-template-columns: 1fr 1fr !important; gap: 12px !important;
  }
  [style*="font-size: 60px"] { font-size: clamp(2rem,   8vw,  3.75rem)  !important; line-height: 1.15 !important; }
  [style*="font-size: 44px"] { font-size: clamp(1.6rem, 6vw,  2.75rem)  !important; line-height: 1.2  !important; }
  [style*="font-size: 42px"] { font-size: clamp(1.55rem,5.8vw,2.625rem) !important; line-height: 1.2  !important; }
  [style*="font-size: 36px"] { font-size: clamp(1.4rem, 5vw,  2.25rem)  !important; }
  [style*="font-size: 34px"] { font-size: clamp(1.3rem, 4.6vw,2.125rem) !important; }
  [style*="font-size: 30px"] { font-size: clamp(1.2rem, 4.2vw,1.875rem) !important; }
  [style*="font-size: 28px"] { font-size: clamp(1.15rem,3.9vw,1.75rem)  !important; }
  [style*="font-size: 26px"] { font-size: clamp(1.1rem, 3.7vw,1.625rem) !important; }
  [style*="font-size: 24px"] { font-size: clamp(1.05rem,3.4vw,1.5rem)   !important; }
  [style*="width: 820px"], [style*="width: 700px"], [style*="width: 640px"],
  [style*="width: 561px"], [style*="width: 300px"], [style*="width: 218px"] {
    width: auto !important; max-width: 100% !important;
  }
  [style*="gap: 64px"] { gap: 28px !important; }
  [style*="gap: 44px"] { gap: 22px !important; }
  [style*="gap: 40px"] { gap: 22px !important; }
  [style*="display: flex"]:not([style*="flex-wrap"]) { flex-wrap: wrap !important; }
  [data-screen-label="Nav"] > div {
    padding: 12px 16px !important; gap: 12px !important;
    flex-wrap: nowrap !important;
  }
  [data-screen-label="Nav"] > div > div { gap: 14px !important; font-size: 13px !important; }
  [data-screen-label="Nav"] a[href="#top"] { font-size: 18px !important; white-space: nowrap !important; }
  html, body { overflow-x: hidden !important; }
  img, svg   { max-width: 100% !important; }
}
@media (max-width: 480px) {
  [style*="grid-template-columns: 1.7fr 0.6fr 1.6fr 2.3fr"] {
    grid-template-columns: 1fr !important; gap: 10px !important;
  }
  [data-screen-label="Nav"] a[href="#top"] { display: none !important; }
  [data-screen-label="Nav"] > div { justify-content: flex-end !important; }
  [style*="padding: 76px 32px 80px"],
  [style*="padding: 60px 32px 56px"],
  [style*="padding: 68px 32px 72px"],
  [style*="padding: 76px 32px 40px"] { padding: 36px 16px 36px !important; }
}
</style>"""

# --- template patching --------------------------------------------------------

def patch_template(image_info: dict[str, dict]) -> str:
    text = TEMPLATE_IN.read_text(encoding="utf-8")

    # 1. png/jpg -> webp for extension-changing files
    for old_name, info in image_info.items():
        text = text.replace(f"/assets/img/{old_name}", f"/assets/img/{info['new_basename']}")

    # 2. <html> -> <html lang="en">
    text = text.replace("<html>", '<html lang="en">', 1)

    # 3. Replace the entire <head>...</head>
    res_map = {r["url"]: r["rel"] for r in ASSET_MAP["ext_resources"]}
    preload = [
        "./assets/fonts/instrument-sans.woff2",   # latin subset (~11 KB)
        "./assets/fonts/newsreader-latin.woff2",  # serif headline
    ]
    new_head = build_head(res_map, preload)
    text = re.sub(r"<head>.*?</head>", lambda m: new_head, text, count=1, flags=re.S)

    # 4. Kill Google Fonts preconnects wherever they live (they're inside <helmet>)
    text = re.sub(
        r'\s*<link[^>]*rel="preconnect"[^>]*href="https://fonts\.(googleapis|gstatic)\.com"[^>]*>',
        "",
        text,
    )

    # 5. <img> tags: add width/height/loading/decoding, hero gets fetchpriority
    def img_repl(m: re.Match) -> str:
        tag = m.group(0)
        src_m = re.search(r'src="([^"]+)"', tag)
        if not src_m: return tag
        src = src_m.group(1)
        base = src.rsplit("/", 1)[-1]
        # Look up new intrinsic dims — original name was .png/.jpg, now it's .webp
        old_name = None
        for k, info in image_info.items():
            if info["new_basename"] == base:
                old_name = k; break
        if old_name is None:
            return tag  # unknown img — leave alone
        info = image_info[old_name]
        w, h = info["w"], info["h"]
        # Insert width/height/loading/decoding after src, only if not already there
        attrs = []
        if "width=" not in tag:        attrs.append(f'width="{w}"')
        if "height=" not in tag:       attrs.append(f'height="{h}"')
        if base == "neel-arvind-desai.webp":
            attrs.append('fetchpriority="high"')
            attrs.append('decoding="async"')
        else:
            attrs.append('loading="lazy"')
            attrs.append('decoding="async"')
        # Insert after `<img` and before the rest
        insertion = " " + " ".join(attrs)
        return tag.replace("<img", "<img" + insertion, 1)
    text = re.sub(r"<img\b[^>]*>", img_repl, text)

    return text

# --- main ---------------------------------------------------------------------

def main() -> None:
    print("optimizing images...")
    image_info = optimize_images()

    print("generating og-preview.png...")
    make_og_preview()

    print("writing favicon.svg...")
    (DIST / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")

    print("writing .nojekyll...")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    print("patching template...")
    final = patch_template(image_info)

    # Regenerate the Path timeline from content/timeline-content-final.html.
    # tools/timeline.py owns the parser + renderer + DCLogic patch; build.py
    # calls it so a full rebuild from bundle-original.html carries the
    # timeline through instead of falling back to the bundled Path section.
    print("regenerating timeline from manifest...")
    try:
        from timeline import convert_media, resolve_external_refs, patch_index_html, MANIFEST
        if MANIFEST.exists():
            media_map = convert_media(
                target_dir=DIST / "assets" / "img" / "timeline",
                show_google_g=SHOW_GOOGLE_G,
            )
            # Figures whose data-source starts with EXTERNAL: reuse an asset
            # already in the repo — no copy, no re-encode, path pass-through.
            resolve_external_refs(MANIFEST, media_map)
            final = patch_index_html(final, media_map)
            print(f"  timeline patched; media assets: {len(media_map)}")
        else:
            print(f"  {MANIFEST} missing; skipping timeline regen")
    except ImportError as e:
        print(f"  timeline module unavailable ({e}); skipping")

    (DIST / "index.html").write_text(final, encoding="utf-8")
    print(f"dist/index.html written ({len(final):,} chars)")

    # summary
    total = 0
    for p in DIST.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    print(f"total dist size: {total/1024:,.1f} KB")

if __name__ == "__main__":
    main()
