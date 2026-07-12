#!/usr/bin/env python3
"""Timeline generator — parses content/timeline-content-final.html and emits
the #path section body + a DCLogic patch that adds per-era open state.

The manifest is the source of truth. index.html's #path section is regenerated
from it; nobody hand-edits the timeline markup.

Public API:
    render_path_section(manifest_path, show_google_g=False) -> path_body_html
    convert_media(media_dir, target_dir) -> {media_id: (new_relpath, w, h)}
    patch_dclogic(dclogic_source_js) -> patched_dclogic_source_js
    replace_path_in_index(html, new_body_html) -> patched_html

CLI:
    python tools/timeline.py                # regenerate in place
    python tools/timeline.py --show-google  # rebuild with the Google G flag ON
"""
from __future__ import annotations
import argparse, io, re, sys
from html import escape
from pathlib import Path
from typing import Any
from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "content" / "timeline-content-final.html"
MEDIA_DIR = ROOT / "media"
TARGET_DIR = ROOT / "assets" / "img" / "timeline"
INDEX_HTML = ROOT / "index.html"

# --- Media conversion --------------------------------------------------------
# Rules per manifest figcaption:
#   mack-trucks-logo.png, linde-logo.png  100×100  chip  → keep as PNG, no resize
#   dot-logo.png                          1000×1000 RGBA  era-mark → WebP, keep alpha, 256×256
#   gdsc-coronai-video.jpg                1280×720  video card → WebP, 800×450
#   google-g.png                          1713×1726 palette-mode chip → PNG, 128×128 RGBA
#   praxair-cryo-pipeline.jpg             960×1280  portrait → WebP, 480×640
#   coronai-logo.jpg                      solid black    → OMIT
#   blend-laptop.jpg                                     → do not reference

def convert_media(media_dir: Path = MEDIA_DIR, target_dir: Path = TARGET_DIR,
                  show_google_g: bool = False) -> dict:
    target_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, tuple[str, int, int]] = {}

    def save(id_: str, im: Image.Image, name: str, fmt: str, **kw) -> None:
        dst = target_dir / name
        if fmt == "WEBP":
            im.save(dst, "WEBP", quality=85, method=6, **kw)
        else:
            im.save(dst, "PNG", optimize=True, **kw)
        rel = f"./assets/img/timeline/{name}"
        out[id_] = (rel, im.size[0], im.size[1])

    # 100×100 chips — PNG passthrough (never upscaled; source *is* the display size).
    for src_name, tgt_name, media_id in [
        ("mack-trucks-logo.png", "mack-logo.png",  "mack-logo"),
        ("linde-logo.png",       "linde-logo.png", "linde-logo"),
    ]:
        im = Image.open(media_dir / src_name)
        save(media_id, im, tgt_name, "PNG")

    # dot-logo — era mark, keep alpha, downsample to 256px.
    im = Image.open(media_dir / "dot-logo.png").convert("RGBA")
    im.thumbnail((256, 256), Image.LANCZOS)
    save("dot-logo", im, "dot-logo.webp", "WEBP", lossless=False)

    # gdsc-coronai-video — click-to-play card poster, 16:9 at 800×450.
    im = Image.open(media_dir / "gdsc-coronai-video.jpg").convert("RGB")
    im.thumbnail((800, 800), Image.LANCZOS)  # long-edge cap; source is 16:9 already
    save("gdsc-video-card", im, "gdsc-coronai.webp", "WEBP")

    # google-g — palette mode → RGBA to keep transparency, 128×128 PNG.
    # Only emitted when show_google_g is True; the manifest and the render
    # both skip it by default per Google's brand guidelines.
    if show_google_g:
        im = Image.open(media_dir / "google-g.png").convert("RGBA")
        im.thumbnail((128, 128), Image.LANCZOS)
        save("google-g", im, "google-g.png", "PNG")

    # praxair — portrait, 480×640 keeps 3:4 aspect ratio.
    im = Image.open(media_dir / "praxair-cryo-pipeline.jpg").convert("RGB")
    im.thumbnail((640, 640), Image.LANCZOS)
    save("praxair-photo", im, "praxair.webp", "WEBP")

    # coronai-logo omitted deliberately (solid-black JPEG; nowhere clean to place it).
    # blend-laptop.jpg deliberately unreferenced (it's a photo of a laptop, not the device).

    return out

def resolve_external_refs(manifest_path: Path, media_map: dict) -> None:
    """Walk the manifest for `data-source="EXTERNAL:<path>"` figures and add
    them to media_map without copying, converting, or touching the file.

    The prefix means: this asset already lives in the repo at the given path
    and is already optimized. Pass the literal path straight through so the
    renderer emits it as the img src. Width/height are read directly from
    the existing file so the intrinsic dims are honest.
    """
    soup = BeautifulSoup(manifest_path.read_text(encoding="utf-8"), "lxml")
    for fig in soup.select("figure[data-source^='EXTERNAL:']"):
        media_id = fig.get("data-media-id", "")
        rel      = fig["data-source"].split("EXTERNAL:", 1)[1].strip()
        if not media_id or not rel:
            continue
        # Read the actual dims from the file if we can find it on disk.
        # Path is relative to index.html — the ROOT works because the site
        # is served from there.
        disk = ROOT / rel.lstrip("./").lstrip("/")
        if not disk.exists():
            print(f"  WARN: EXTERNAL source not found: {disk}")
            continue
        with Image.open(disk) as im:
            w, h = im.size
        media_map[media_id] = (rel, w, h)

# --- Manifest parse ----------------------------------------------------------

def parse_manifest(path: Path = MANIFEST) -> list[dict]:
    """Return a list of era dicts, in the order they appear (reverse chrono).

    Each era: { key, org, location, start, end, label, thesis, roles: [role...],
                artifacts: [artifact...] }.
    Roles / artifacts nest recursively via sub-.role and sub-.artifact divs.
    """
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    eras: list[dict] = []
    for sec in soup.select("section.era"):
        era = _era_from_section(sec)
        eras.append(era)
    return eras

def _text_of(el) -> str:
    return (el.get_text(" ", strip=True) if el else "").strip()

def _role_from_div(div) -> dict:
    role: dict[str, Any] = {
        "kind":       "role",
        "title":      div.get("data-title") or "",
        "org":        div.get("data-org") or "",
        "start":      div.get("data-start") or "",
        "end":        div.get("data-end") or "",
        "location":   div.get("data-location") or "",
        "role_kind":  div.get("data-kind") or "",
    }
    # header line: first <h3>
    h3 = div.find("h3", recursive=False)
    role["header"] = _text_of(h3) if h3 else ""
    # meta line: first .meta paragraph inside
    meta = div.find("p", class_="meta", recursive=False)
    role["meta"] = _text_of(meta)
    # thesis / detail / summary — data-field="detail" or "summary"
    detail = div.find(attrs={"data-field": "detail"}, recursive=False)
    role["detail"] = _text_of(detail)
    summary = div.find(attrs={"data-field": "summary"}, recursive=False)
    role["summary"] = _text_of(summary)
    # bullets — first ul with data-field="bullets"
    bullets_ul = div.find("ul", attrs={"data-field": "bullets"}, recursive=False)
    role["bullets"] = [_text_of(li) for li in bullets_ul.find_all("li")] if bullets_ul else []
    # links — all ul with data-field="links"
    links: list[dict] = []
    for ul in div.find_all("ul", attrs={"data-field": "links"}, recursive=False):
        for a in ul.find_all("a"):
            links.append({
                "text": _text_of(a),
                "href": a.get("href", ""),
                "rel":  a.get("data-rel", ""),
            })
    role["links"] = links
    # nested roles + artifacts (direct children only)
    role["roles"]     = [_role_from_div(d)     for d in div.find_all("div", class_="role", recursive=False)]
    role["artifacts"] = [_artifact_from_div(d) for d in div.find_all("div", class_="artifact", recursive=False)]
    # figures inside the role
    role["figures"] = [_figure_from_el(f) for f in div.find_all("figure", recursive=False)]
    return role

def _artifact_from_div(div) -> dict:
    art: dict[str, Any] = {
        "kind":     "artifact",
        "id":       div.get("data-artifact") or "",
        "featured": (div.get("data-featured") or "").lower() == "true",
    }
    h3 = div.find("h3", recursive=False)
    art["header"] = _text_of(h3) if h3 else ""
    summary = div.find(attrs={"data-field": "summary"}, recursive=False)
    art["summary"] = _text_of(summary)
    # links
    links: list[dict] = []
    for ul in div.find_all("ul", attrs={"data-field": "links"}, recursive=False):
        for a in ul.find_all("a"):
            links.append({
                "text": _text_of(a), "href": a.get("href", ""), "rel":  a.get("data-rel", ""),
            })
    art["links"] = links
    art["figures"] = [_figure_from_el(f) for f in div.find_all("figure", recursive=False)]
    return art

def _figure_from_el(fig) -> dict:
    return {
        "media_id":    fig.get("data-media-id", ""),
        "source":      fig.get("data-source", ""),
        "target":      fig.get("data-target", ""),
        "role":        fig.get("data-role", ""),
        "video":       fig.get("data-video", ""),
        "ship":        (fig.get("data-ship") or "").upper(),  # YES / OFF-BY-DEFAULT / ""
        "caption":     _text_of(fig.find("figcaption")),
    }

def _era_from_section(sec) -> dict:
    era: dict[str, Any] = {
        "key":      sec.get("data-era") or "",
        "org":      sec.get("data-org") or "",
        "location": sec.get("data-location") or "",
        "start":    sec.get("data-start") or "",
        "end":      sec.get("data-end") or "",
        "label":    sec.get("data-label") or "",
    }
    org_el = sec.find("h2", attrs={"data-field": "org"}, recursive=False) or sec.find("h2", recursive=False)
    era["org_display"] = _text_of(org_el)
    meta = sec.find("p", class_="meta", recursive=False)
    era["meta"] = _text_of(meta)
    thesis = sec.find("p", attrs={"data-field": "thesis"}, recursive=False)
    era["thesis"] = _text_of(thesis)
    naming = sec.find("p", attrs={"data-field": "naming-note"}, recursive=False)
    era["naming_note"] = _text_of(naming)
    era["figures"] = [_figure_from_el(f) for f in sec.find_all("figure", recursive=False)]
    era["roles"]   = [_role_from_div(d) for d in sec.find_all("div", class_="role", recursive=False)]
    return era

# --- HTML render -------------------------------------------------------------
# All inline styles match the existing site's design tokens; we only introduce
# new attribute-value patterns the mobile shim already handles (or brand-new
# selectors that don't collide with it).

FONT_MONO   = "'Space Mono', monospace"
FONT_SERIF  = "'Newsreader', serif"
COL_MUTED   = "#8A7E6C"
COL_INK     = "#2A241C"
COL_BODY    = "#5C5346"

def _chip(text: str, href: str, external: bool, is_project: bool) -> str:
    """One pill link. Project chips use the accent stroke; outbound use the muted stroke."""
    accent_color = "var(--accent)" if is_project else COL_BODY
    border = "1px solid var(--accent)" if is_project else "1px solid rgba(42,36,28,0.25)"
    tgt = 'target="_blank" rel="noopener"' if external else ""
    return (
        f'<a href="{escape(href)}" {tgt} '
        f'style="font-family: {FONT_MONO}; font-size: 12px; color: {accent_color}; '
        f'border: {border}; border-radius: 16px; padding: 6px 13px; background: #FFFDF8;">'
        f'{escape(text)}</a>'
    )

def _links_row(links: list[dict]) -> str:
    if not links: return ""
    pills = []
    for l in links:
        href = l["href"]
        is_project = href.startswith("#proj-") or l["rel"] == "project"
        external = href.startswith("http")
        pills.append(_chip(l["text"], href, external, is_project))
    return (
        '<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px;">'
        + "".join(pills) + '</div>'
    )

def _bullets(items: list[str]) -> str:
    if not items: return ""
    lis = "".join(
        f'<li style="margin: 6px 0; padding-left: 18px; position: relative;">'
        f'<span style="position: absolute; left: 0; top: 0.65em; width: 6px; height: 6px; '
        f'border-radius: 50%; background: var(--accent);"></span>{escape(b)}</li>'
        for b in items
    )
    return (
        f'<ul style="list-style: none; padding: 0; margin: 10px 0 0; '
        f'font-size: 14.5px; line-height: 1.6; color: {COL_BODY};">{lis}</ul>'
    )

def _chip_logo(rel: str, alt: str, size: int = 48) -> str:
    """48px chip. width/height attrs match the source's intrinsic 100 (never upscaled)."""
    return (
        f'<img src="{rel}" alt="{escape(alt)}" width="100" height="100" '
        f'loading="lazy" decoding="async" '
        f'style="width: {size}px; height: {size}px; object-fit: contain; flex: none;">'
    )

def _era_mark(rel: str, alt: str, w: int = 256, h: int = 256) -> str:
    """Bigger era mark for era headers (currently IoTmatic and Northeastern).
    Rendered at a fixed 64px so every era mark sits at the same visual size,
    regardless of the source's intrinsic dimensions."""
    return (
        f'<img src="{rel}" alt="{escape(alt)}" width="{w}" height="{h}" '
        f'loading="lazy" decoding="async" '
        f'style="width: 64px; height: 64px; object-fit: contain; flex: none;">'
    )

def _video_card(rel: str, video_url: str, title: str) -> str:
    """Click-to-play card. Anchor to YouTube, poster image, play glyph. NO iframe."""
    return (
        f'<a href="{escape(video_url)}" target="_blank" rel="noopener" '
        f'style="position: relative; display: block; margin-top: 14px; overflow: hidden; '
        f'border-radius: 12px; background: #000; aspect-ratio: 16 / 9; max-width: 480px; '
        f'text-decoration: none;">'
        f'<img src="{rel}" alt="{escape(title)} — poster" width="800" height="450" '
        f'loading="lazy" decoding="async" '
        f'style="width: 100%; height: 100%; object-fit: cover; display: block; opacity: 0.86;">'
        f'<span aria-hidden="true" '
        f'style="position: absolute; inset: 0; display: grid; place-items: center; '
        f'font-size: 64px; color: #fff; text-shadow: 0 2px 20px rgba(0,0,0,0.5); '
        f'pointer-events: none;">▶</span>'
        f'<span style="position: absolute; left: 12px; bottom: 10px; font-family: {FONT_MONO}; '
        f'font-size: 11px; color: rgba(255,255,255,0.9); letter-spacing: 0.1em; '
        f'text-transform: uppercase;">Watch on YouTube ↗</span>'
        f'</a>'
    )

def _portrait_photo(rel: str, alt: str) -> str:
    """Portrait slot — no letterboxing, aspect 3:4."""
    return (
        f'<img src="{rel}" alt="{escape(alt)}" width="480" height="640" '
        f'loading="lazy" decoding="async" '
        f'style="width: 100%; max-width: 300px; aspect-ratio: 3 / 4; object-fit: cover; '
        f'border-radius: 12px; margin-top: 14px; display: block;">'
    )

def _role_html(role: dict, depth: int = 0, media_map: dict = None) -> str:
    media_map = media_map or {}
    left_border = "2px solid rgba(42,36,28,0.14)" if depth == 0 else "1px dashed rgba(42,36,28,0.16)"
    pad_left = "22px" if depth == 0 else "18px"
    parts: list[str] = []

    header = role["header"] or role["title"]
    if header:
        parts.append(
            f'<div style="font-family: {FONT_SERIF}; font-size: {20 - min(depth, 1) * 2}px; '
            f'color: {COL_INK}; margin-bottom: 2px;">{escape(header)}</div>'
        )
    if role["meta"]:
        parts.append(
            f'<div style="font-family: {FONT_MONO}; font-size: 12px; color: {COL_MUTED}; '
            f'letter-spacing: 0.05em;">{escape(role["meta"])}</div>'
        )
    if role["detail"]:
        parts.append(
            f'<p style="font-size: 14.5px; line-height: 1.6; color: {COL_BODY}; margin: 8px 0 0;">'
            f'{escape(role["detail"])}</p>'
        )
    if role["summary"]:
        parts.append(
            f'<p style="font-size: 14.5px; line-height: 1.6; color: {COL_BODY}; margin: 8px 0 0;">'
            f'{escape(role["summary"])}</p>'
        )
    parts.append(_bullets(role["bullets"]))
    parts.append(_links_row(role["links"]))

    # Figures inside a role — currently only IER's implicit "no figure" case,
    # but we render them uniformly if any appear.
    for fig in role["figures"]:
        if fig["media_id"] in media_map:
            rel, _, _ = media_map[fig["media_id"]]
            parts.append(f'<img src="{rel}" alt="{escape(fig["caption"][:80])}" style="max-width:100%; margin-top: 12px;">')

    # Nested roles + artifacts
    for sub in role["roles"]:
        parts.append(_role_html(sub, depth + 1, media_map))
    for art in role["artifacts"]:
        parts.append(_artifact_html(art, depth + 1, media_map))

    return (
        f'<div style="border-left: {left_border}; padding: 0 0 0 {pad_left}; '
        f'margin-top: {14 if depth else 20}px;">'
        + "".join(parts) + '</div>'
    )

def _artifact_html(art: dict, depth: int, media_map: dict) -> str:
    is_ier = art["id"] == "ier-site"
    accent = "var(--accent)" if art["featured"] else COL_BODY
    parts: list[str] = []
    if art["header"]:
        parts.append(
            f'<div style="font-family: {FONT_SERIF}; font-size: 18px; color: {COL_INK}; '
            f'margin-bottom: 2px;">{escape(art["header"])}</div>'
        )
    if art["summary"]:
        parts.append(
            f'<p style="font-size: 14.5px; line-height: 1.6; color: {COL_BODY}; margin: 8px 0 0;">'
            f'{escape(art["summary"])}</p>'
        )
    parts.append(_links_row(art["links"]))
    # Media per figure
    for fig in art["figures"]:
        if fig["ship"] == "OFF-BY-DEFAULT":
            # Google-G — gated by SHOW_GOOGLE_G flag; media_map only contains it when flag is on.
            if fig["media_id"] not in media_map:
                continue
        if fig["media_id"] not in media_map:
            continue
        rel, w, h = media_map[fig["media_id"]]
        if fig["role"] == "video-card":
            parts.append(_video_card(rel, fig["video"], art["header"]))
        elif fig["role"] == "logo-chip":
            parts.append(
                f'<div style="margin-top: 12px;">{_chip_logo(rel, art["header"] + " — brand mark")}</div>'
            )
        elif fig["role"] == "logo":
            # coronai-logo path — omitted at media stage; if a future logo type appears, render inline.
            pass
        elif fig["role"] == "photo":
            parts.append(_portrait_photo(rel, art["header"] + " — photo"))
    return (
        f'<div style="border-left: 2px solid {accent}; padding: 4px 0 4px 16px; '
        f'margin-top: 14px; background: rgba(255,253,248,0.5); border-radius: 0 8px 8px 0;">'
        + "".join(parts) + '</div>'
    )

def _era_html(era: dict, media_map: dict) -> str:
    key = era["key"]
    cap = key[:1].upper() + key[1:]     # Volvo, Northeastern, ...
    era_id = f"era-{key}"

    # Era mark(s): logo chip on the era header row.
    header_media = ""
    for fig in era["figures"]:
        if fig["media_id"] in media_map:
            rel, w, h = media_map[fig["media_id"]]
            if fig["role"] == "logo-chip":
                header_media = _chip_logo(rel, era["org_display"] + " logo")
            elif fig["role"] == "era-mark":
                header_media = _era_mark(rel, era["org_display"] + " mark", w, h)

    # Body: naming note + roles + top-level artifacts
    body_parts: list[str] = []
    if era["naming_note"]:
        body_parts.append(
            f'<p style="font-family: {FONT_MONO}; font-size: 12px; color: {COL_MUTED}; '
            f'margin: 12px 0 0; padding: 10px 12px; background: rgba(194,104,63,0.06); '
            f'border-left: 2px solid var(--accent); border-radius: 0 4px 4px 0;">'
            f'{escape(era["naming_note"])}</p>'
        )
    for role in era["roles"]:
        body_parts.append(_role_html(role, depth=0, media_map=media_map))
    body_html = "".join(body_parts)

    # The era article. Header is a real <button> so Enter/Space toggle it and
    # tab order follows document flow. aria-expanded is bound to the DC state.
    return (
        f'<article id="{era_id}" data-era="{key}" data-reveal="" '
        f'style="border-left: 3px solid var(--accent); background: #FFFDF8; '
        f'border-radius: 0 14px 14px 0; box-shadow: 0 20px 40px -32px rgba(42,36,28,0.35); '
        f'overflow: hidden;">'
        # HEADER BUTTON
        f'<button type="button" '
        f'aria-expanded="{{{{ era{cap}Open }}}}" '
        f'aria-controls="{era_id}-body" '
        f'sc-camel-on-click="{{{{ togEra{cap} }}}}" '
        f'style="display: flex; align-items: center; gap: 18px; width: 100%; '
        f'padding: 20px 22px; background: transparent; border: 0; cursor: pointer; '
        f'font: inherit; color: inherit; text-align: left;">'
        f'<div style="flex: 1; min-width: 0;">'
        f'<div style="font-family: {FONT_MONO}; font-size: 12px; letter-spacing: 0.12em; '
        f'text-transform: uppercase; color: var(--accent);">{escape(era["label"])}</div>'
        f'<div style="font-family: {FONT_SERIF}; font-size: 24px; color: {COL_INK}; '
        f'margin-top: 3px; line-height: 1.2;">{escape(era["org_display"])}</div>'
        f'<div style="font-family: {FONT_MONO}; font-size: 12px; color: {COL_MUTED}; '
        f'margin-top: 4px;">{escape(era["meta"])}</div>'
        + (f'<div style="font-size: 14px; line-height: 1.55; color: {COL_BODY}; '
           f'margin-top: 10px; max-width: 60ch;">{escape(era["thesis"])}</div>' if era["thesis"] else "")
        + '</div>'
        + header_media
        + f'<span aria-hidden="true" style="font-size: 22px; color: {COL_MUTED}; '
        f'transition: transform 0.4s; transform: {{{{ era{cap}Chev }}}}; flex: none;">⌄</span>'
        f'</button>'
        # BODY (grid-rows animated 0fr -> 1fr; opacity too)
        f'<div id="{era_id}-body" role="region" '
        f'style="display: grid; grid-template-rows: {{{{ era{cap}Rows }}}}; '
        f'transition: grid-template-rows 0.55s cubic-bezier(0.3, 0.4, 0.1, 1);">'
        f'<div style="overflow: hidden; min-height: 0;">'
        f'<div style="opacity: {{{{ era{cap}Op }}}}; transition: opacity 0.45s 0.1s; '
        f'padding: 0 22px 24px 22px;">'
        + body_html
        + '</div></div></div>'
        + '</article>'
    )

def render_path_section(manifest_path: Path = MANIFEST, media_map: dict | None = None) -> str:
    """Return the innerHTML of the #path <section> — the wrapper stays, its body is regen'd."""
    eras = parse_manifest(manifest_path)
    parts: list[str] = []
    parts.append(
        f'<div style="max-width: 1240px; margin: 0 auto; padding: 68px 32px 72px;">'
        f'<div data-reveal="" style="display: flex; justify-content: space-between; '
        f'align-items: baseline; gap: 20px; flex-wrap: wrap; margin-bottom: 26px;">'
        f'<div style="font-family: {FONT_MONO}; font-size: 13px; letter-spacing: 0.15em; '
        f'text-transform: uppercase; color: var(--accent);">The path so far</div>'
        f'<div style="font-family: {FONT_MONO}; font-size: 12px; color: {COL_MUTED};">'
        f'now → the beginning · tap an era to expand</div>'
        f'</div>'
        f'<div style="display: flex; flex-direction: column; gap: 14px;">'
    )
    for era in eras:
        parts.append(_era_html(era, media_map or {}))
    parts.append('</div></div>')

    # Focus-ring stylesheet — scoped to timeline buttons only. Injected once,
    # here, so nothing else in the page picks up the outline.
    parts.append(
        '<style>'
        '#path article button:focus-visible {'
        '  outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 4px;'
        '}'
        '#path article button:hover {'
        '  background: rgba(255,253,248,0.6) !important;'
        '}'
        '</style>'
    )
    return "".join(parts)

# --- DCLogic patch -----------------------------------------------------------

DCLOGIC_ERAS_CONST = "  ERAS = ['volvo', 'northeastern', 'iotmatic', 'srm'];\n"
DCLOGIC_ERAS_STATE = (
    "  toggleEra(key) {\n"
    "    this.setState(s => ({ eras: { ...s.eras, [key]: !s.eras[key] } }));\n"
    "  }\n\n"
)
DCLOGIC_ERAS_RENDER = (
    "    for (const era of this.ERAS) {\n"
    "      const cap = era.charAt(0).toUpperCase() + era.slice(1);\n"
    "      const isOpen = !!(this.state.eras && this.state.eras[era]);\n"
    "      vals['era' + cap + 'Rows'] = isOpen ? '1fr' : '0fr';\n"
    "      vals['era' + cap + 'Op']   = isOpen ? 1 : 0;\n"
    "      vals['era' + cap + 'Chev'] = isOpen ? 'rotate(180deg)' : 'none';\n"
    "      vals['era' + cap + 'Open'] = isOpen ? 'true' : 'false';\n"
    "      vals['togEra' + cap]       = () => this.toggleEra(era);\n"
    "    }\n"
)

def patch_dclogic(js: str) -> str:
    """Add ERAS constant + state.eras + toggleEra() + per-era render bindings.
    Idempotent — reruns leave a patched source unchanged."""
    if "ERAS =" in js:
        return js
    # 1. Add ERAS constant after KEYS =
    js = re.sub(
        r"(KEYS = \[[^\]]*\];\n)",
        r"\1" + DCLOGIC_ERAS_CONST,
        js, count=1,
    )
    # 2. Extend the existing state initializer with an `eras` key — first
    #    era open, others collapsed. Single class field, no duplication.
    js = re.sub(
        r"(state = \{[^}]*?, hover: null)( \};\n)",
        r"\1, eras: { volvo: true, northeastern: false, iotmatic: false, srm: false }\2",
        js, count=1,
    )
    # 3. Add toggleEra method above scrollToId
    js = re.sub(
        r"(  scrollToId\(id, delay\))",
        DCLOGIC_ERAS_STATE + r"\1",
        js, count=1,
    )
    # 4. Insert render bindings before the final `return vals;` in renderVals
    js = re.sub(
        r"(    return vals;\n  \}\n\})",
        DCLOGIC_ERAS_RENDER + r"\1",
        js, count=1,
    )
    return js

# --- Splice into index.html --------------------------------------------------

_DIV_ANY_OPEN = re.compile(r'<div\b[^>]*>', re.I)
_DIV_CLOSE    = re.compile(r'</div\s*>', re.I)

def _find_matching_div_close(html: str, opening_end: int) -> int:
    """Given the position just after a `<div ...>` opening tag, return the
    position of the matching `</div>` closing tag by counting nesting."""
    depth = 1
    pos   = opening_end
    while pos < len(html):
        m_open  = _DIV_ANY_OPEN.search(html, pos)
        m_close = _DIV_CLOSE.search(html, pos)
        if not m_close:
            raise SystemExit("no closing </div> found for #path section")
        if m_open and m_open.start() < m_close.start():
            depth += 1
            pos = m_open.end()
        else:
            depth -= 1
            pos = m_close.end()
            if depth == 0:
                return m_close.start()
    raise SystemExit("no closing </div> found for #path section")

def replace_path_in_index(html: str, body_html: str) -> str:
    """Replace the inside of `<div id="path" ...>...</div>` with body_html."""
    open_tag = re.search(r'<div\b[^>]*id="path"[^>]*>', html)
    if not open_tag:
        raise SystemExit("could not locate #path section in index.html")
    close_start = _find_matching_div_close(html, open_tag.end())
    return html[: open_tag.end()] + body_html + html[close_start:]

def patch_index_html(html: str, media_map: dict | None) -> str:
    body = render_path_section(MANIFEST, media_map or {})
    html = replace_path_in_index(html, body)
    # Patch the DCLogic text/x-dc block in-place.
    def _repl(m: re.Match) -> str:
        return m.group(1) + patch_dclogic(m.group(2)) + m.group(3)
    html = re.sub(
        r'(<script[^>]*type="text/x-dc"[^>]*>)(.*?)(</script>)',
        _repl, html, count=1, flags=re.S,
    )
    return html

# --- CLI ---------------------------------------------------------------------

def main() -> None:
    # Windows PowerShell (cp1252) chokes on em-dashes and arrows in stdout;
    # force utf-8 so the arrow separator and other niceties survive.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-google", action="store_true",
                    help="include google-g.png (default: OFF per manifest)")
    args = ap.parse_args()

    print("converting media...")
    media_map = convert_media(show_google_g=args.show_google)
    resolve_external_refs(MANIFEST, media_map)
    for mid, (rel, w, h) in media_map.items():
        print(f"  {mid:<18s} -> {rel}  ({w}x{h})")

    print("patching index.html...")
    html = INDEX_HTML.read_text(encoding="utf-8")
    html = patch_index_html(html, media_map)
    INDEX_HTML.write_text(html, encoding="utf-8")
    print("done.")

if __name__ == "__main__":
    main()
