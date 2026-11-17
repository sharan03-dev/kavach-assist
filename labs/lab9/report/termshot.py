"""
Images for the report, drawn with Pillow (no browser, no screenshots by hand):

  render_console(text, out_prefix, title)  captured console output -> terminal-style PNG(s)
  bar_chart(...)                           simple grouped bar chart -> PNG

The console text is exactly what the lab printed (saved by the lab9 menu to
results/console_*.txt); it is only re-drawn as an image so the report shows
the output the way it looked in the terminal.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
             "/Library/Fonts", "/System/Library/Fonts/Supplemental"]


def find_font(names):
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return str(p)
    return None


MONO = find_font(["DejaVuSansMono.ttf", "Menlo.ttc", "Courier New.ttf"])
MONO_BOLD = find_font(["DejaVuSansMono-Bold.ttf"]) or MONO
SANS = find_font(["DejaVuSans.ttf", "Arial.ttf"])
SANS_BOLD = find_font(["DejaVuSans-Bold.ttf", "Arial Bold.ttf"]) or SANS


def _font(path, size):
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                      # Pillow < 10.1
        return ImageFont.load_default()


# terminal colours (dark theme)
BG, FG, BAR = (30, 30, 30), (212, 212, 212), (58, 58, 58)
GREEN, CYAN, YELLOW, RED, GREY = (106, 200, 120), (78, 201, 176), (220, 220, 170), (244, 135, 113), (140, 140, 140)

_BAD = re.compile(r"WTIMEOUT|NO_PRIMARY|NOT_PRIMARY|NETWORK_ERROR|STEPPED_DOWN|NO_SERVER|"
                  r"INTERRUPTED|UNREACHABLE|ROLLED BACK|not reachable|Traceback|Error")
_EVENT = re.compile(r"\] (PARTITION_START|PARTITION_END|SCENARIO_|CAUGHT_UP|PRIMARY_AVAILABLE|"
                    r"PRIORITY_TAKEOVER|FORMER_PRIMARY|MONGOD_LOG)")


def _colour(line: str):
    s = line.strip()
    if s.startswith("root@") or s.startswith("$ "):
        return GREEN
    if set(s) <= set("=-") and len(s) > 10:
        return GREY
    if _EVENT.search(line) or "***" in line:
        return YELLOW
    if _BAD.search(line):
        return RED
    if s.startswith(("Lab 9", "Scenario", "Step ", "RESULTS", "WHY THE", "ANALYSIS", "Timeline")):
        return CYAN
    return FG


def _wrap(lines, cols):
    out = []
    for ln in lines:
        ln = ln.rstrip("\n").expandtabs(4)
        if len(ln) <= cols:
            out.append(ln)
            continue
        indent = len(ln) - len(ln.lstrip())
        if ln.lstrip().startswith("* "):
            indent += 2
        parts = textwrap.wrap(ln, width=cols, subsequent_indent=" " * min(indent, 20),
                              break_long_words=True, drop_whitespace=False)
        out.extend(p.rstrip() for p in parts) if parts else out.append("")
    return out


def render_console(text: str, out_prefix: Path, title: str, cols: int = 120,
                   lines_per_image: int = 58, font_px: int = 26) -> list[Path]:
    """Draw console text as one or more terminal-window PNGs. Returns the paths."""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)            # strip ANSI codes
    raw = [ln for ln in text.splitlines()]
    while raw and not raw[-1].strip():
        raw.pop()
    lines = _wrap(raw, cols)
    chunks = [lines[i:i + lines_per_image] for i in range(0, len(lines), lines_per_image)] or [[""]]
    f = _font(MONO, font_px)
    fb = _font(MONO_BOLD, font_px)
    ft = _font(SANS, int(font_px * 0.85))
    cw = f.getlength("M")
    lh = int(font_px * 1.32)
    pad, bar_h = int(font_px * 0.9), int(font_px * 1.6)
    width = int(pad * 2 + cw * cols)
    paths = []
    for k, chunk in enumerate(chunks, 1):
        h = bar_h + pad * 2 + lh * len(chunk)
        img = Image.new("RGB", (width, h), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, width, bar_h], fill=BAR)
        r = int(bar_h * 0.22)
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            cx = pad + i * r * 3 + r
            d.ellipse([cx - r, bar_h // 2 - r, cx + r, bar_h // 2 + r], fill=c)
        t = title + (f"   ({k}/{len(chunks)})" if len(chunks) > 1 else "")
        tw = d.textlength(t, font=ft)
        d.text(((width - tw) / 2, (bar_h - font_px * 0.85) / 2 - 2), t, font=ft, fill=(200, 200, 200))
        y = bar_h + pad
        for ln in chunk:
            col = _colour(ln)
            d.text((pad, y), ln, font=fb if col in (GREEN, CYAN) else f, fill=col)
            y += lh
        p = Path(f"{out_prefix}_{k}.png")
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p, optimize=True)
        paths.append(p)
    return paths


def bar_chart(out: Path, title: str, categories: list[str], series: dict[str, list[float]],
              ylabel: str = "ms", colours=None, value_fmt="{:.2f}") -> Path:
    """Grouped bar chart. series = {legend: [value per category]}."""
    colours = colours or [(27, 32, 51), (86, 130, 200), (230, 145, 56), (120, 170, 90)]
    W, H = 1800, 900
    ml, mr, mt, mb = 150, 60, 130, 170
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    ft, fl, fs = _font(SANS_BOLD, 40), _font(SANS, 30), _font(SANS, 26)
    d.text((ml, 30), title, font=ft, fill=(27, 32, 51))
    vmax = max(v for vals in series.values() for v in vals) or 1
    top = vmax * 1.18
    ph = H - mt - mb
    # grid + y labels
    for i in range(6):
        v = top * i / 5
        y = H - mb - ph * i / 5
        d.line([ml, y, W - mr, y], fill=(225, 225, 225), width=2)
        lab = f"{v:.1f}"
        d.text((ml - 15 - d.textlength(lab, font=fs), y - 15), lab, font=fs, fill=(90, 90, 90))
    d.text((20, mt - 60), ylabel, font=fs, fill=(90, 90, 90))
    n_cat, n_ser = len(categories), len(series)
    gw = (W - ml - mr) / n_cat
    bw = gw * 0.7 / n_ser
    for ci, cat in enumerate(categories):
        gx = ml + gw * ci + gw * 0.15
        for si, (name, vals) in enumerate(series.items()):
            v = vals[ci]
            x0 = gx + si * bw
            y0 = H - mb - ph * v / top
            d.rectangle([x0 + 4, y0, x0 + bw - 4, H - mb], fill=colours[si % len(colours)])
            lab = value_fmt.format(v)
            d.text((x0 + bw / 2 - d.textlength(lab, font=fs) / 2, y0 - 34), lab, font=fs,
                   fill=(40, 40, 40))
        cw_ = d.textlength(cat, font=fl)
        d.text((ml + gw * ci + gw / 2 - cw_ / 2, H - mb + 18), cat, font=fl, fill=(40, 40, 40))
    d.line([ml, H - mb, W - mr, H - mb], fill=(60, 60, 60), width=3)
    if n_ser > 1:
        x = ml
        for si, name in enumerate(series):
            d.rectangle([x, H - 70, x + 34, H - 40], fill=colours[si % len(colours)])
            d.text((x + 46, H - 74), name, font=fl, fill=(40, 40, 40))
            x += 70 + d.textlength(name, font=fl) + 40
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out
