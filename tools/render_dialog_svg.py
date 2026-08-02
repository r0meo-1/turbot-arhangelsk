"""Render the captured dialog as an SVG for the README.

A screenshot would be a PNG nobody can diff and everybody suspects of being
touched up. This reads demo_capture.json — produced by driving the real bot
through a real webhook, with real Tutu prices — and lays it out as text, so
the image in the README is regenerable and reviewable like any other file.

    python3 demo_capture.py            # capture a real run
    python3 tools/render_dialog_svg.py # turn it into docs/dialog.svg
"""

import html
import json
import os
import re
import sys

W = 720
PAD = 18
BUBBLE_MAX = 470
LINE = 19
FS = 13.2
GAP = 9

# Only the interesting stretch: the steps a reviewer has not seen in every
# other bot, plus the payoff. /start and the consent screen add height and
# say nothing.
SKIP_UNTIL = "🛫"


def strip_tags(text):
    text = re.sub(r"<a [^>]*>(.*?)</a>", r"\1", text, flags=re.S)
    text = re.sub(r"</?[a-z][^>]*>", "", text)
    return html.unescape(text)


def wrap(text, width_chars):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            probe = f"{line} {word}".strip()
            if len(probe) <= width_chars:
                line = probe
            else:
                if line:
                    out.append(line)
                line = word
        if line:
            out.append(line)
    return out


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "demo_capture.json"), encoding="utf-8") as fh:
        events = json.load(fh)

    started = False
    items = []
    for ev in events:
        text = strip_tags(ev["text"]).strip()
        if not started:
            if text.startswith(SKIP_UNTIL):
                started = True
            else:
                continue
        if not text:
            continue
        items.append((ev["to"], wrap(text, 52), text))

    # Cut one contiguous stretch — budget, contact choice, phone — because it
    # looks like every other bot and only adds height. Cutting a range keeps
    # the user's own replies to those steps out too, which a per-message
    # filter missed. The cut is marked: a picture that quietly drops steps
    # misrepresents the product.
    def index_of(prefix):
        return next((i for i, e in enumerate(items) if e[2].startswith(prefix)), None)

    cut_from, cut_to = index_of("💰 Бюджет"), index_of("✅ Заявка принята")
    if cut_from is not None and cut_to is not None and cut_to > cut_from:
        skipped = cut_to - cut_from
        marker = ("gap", ["⋯ бюджет, способ связи и телефон — ещё три шага ⋯"], "⋯")
        items = items[:cut_from] + [marker] + items[cut_to:]
    items = [(w, l) for w, l, _ in items]

    body = []
    y = 58
    for who, lines in items:
        h = len(lines) * LINE + 16
        wide = max((len(l) for l in lines), default=0)
        bw = min(BUBBLE_MAX, max(120, int(wide * 7.1) + 26))
        if who == "gap":
            body.append(
                f'<text x="{W // 2}" y="{y + 16}" font-size="11.5" fill="#6b7783" '
                f'text-anchor="middle">{html.escape(lines[0])}</text>'
            )
            y += 30
            continue
        if who in ("user", "user_tap"):
            x = W - PAD - bw
            fill, stroke, anchor = "#e7f6d5", "#cfe7b4", x + 13
        else:
            x = PAD
            fill, stroke, anchor = "#ffffff", "#e0e5ea", x + 13
        body.append(
            f'<rect x="{x}" y="{y}" width="{bw}" height="{h}" rx="11" '
            f'fill="{fill}" stroke="{stroke}"/>'
        )
        ty = y + 20
        for line in lines:
            body.append(
                f'<text x="{anchor}" y="{ty}" font-size="{FS}" fill="#16181d">'
                f"{html.escape(line)}</text>"
            )
            ty += LINE
        y += h + GAP

    total = y + PAD
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{total}"
     viewBox="0 0 {W} {total}" font-family="-apple-system,Segoe UI,Roboto,Helvetica,sans-serif">
  <rect width="{W}" height="{total}" fill="#d6e0ea"/>
  <rect x="0" y="0" width="{W}" height="42" fill="#ffffff"/>
  <circle cx="26" cy="21" r="13" fill="#2b7fd4"/>
  <text x="21" y="26" font-size="14" fill="#ffffff">🌴</text>
  <text x="48" y="18" font-size="13.5" font-weight="600" fill="#16181d">АПРЕЛЬ тур</text>
  <text x="48" y="33" font-size="11" fill="#7b8794">живой прогон · цены из Tutu.ru по MCP</text>
{chr(10).join("  " + b for b in body)}
</svg>
"""
    out = os.path.join(here, "docs", "dialog.svg")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"written {out} ({len(items)} bubbles, {total}px tall)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
