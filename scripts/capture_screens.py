"""Capture dashboard screenshots (and assemble the demo GIF) for the README.

Requires:  pip install playwright pillow && playwright install chromium
The dashboard must already be running (streamlit run app/dashboard.py).

Usage:
  python scripts/capture_screens.py shot --name dashboard_reject
  python scripts/capture_screens.py shot --name delta_explorer --scroll-to "Delta Explorer"
  python scripts/capture_screens.py shot --name queue_approved --click Approve --scroll-to "Human review queue"
  python scripts/capture_screens.py gif --frames a.png b.png c.png --out demo.gif
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "images"
DEFAULT_URL = "http://localhost:8511"


def shot(url: str, name: str, scroll_to: str | None, click: str | None) -> Path:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url)
        page.wait_for_load_state("networkidle")
        time.sleep(4)                    # streamlit renders after the websocket settles
        if click:
            page.get_by_role("button", name=click).first.click()
            time.sleep(3)                # rerun after the widget event
        if scroll_to:
            page.evaluate(
                """(text) => {
                    const els = [...document.querySelectorAll('h1,h2,h3,summary,p,span')];
                    const el = els.find(e => e.textContent.trim().startsWith(text));
                    if (el) el.scrollIntoView({block: 'start'});
                }""",
                scroll_to,
            )
            time.sleep(1.5)
        page.screenshot(path=str(path))
        browser.close()
    print(f"saved {path}")
    return path


def gif(frames: list[str], out: str, seconds_per_frame: float) -> Path:
    from PIL import Image

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / out
    images = []
    for frame in frames:
        img = Image.open(OUT_DIR / frame).convert("RGB")
        ratio = 1100 / img.width
        img = img.resize((1100, int(img.height * ratio)))
        images.append(img.convert("P", palette=Image.ADAPTIVE, colors=128))
    images[0].save(
        path, save_all=True, append_images=images[1:],
        duration=int(seconds_per_frame * 1000), loop=0, optimize=True,
    )
    print(f"saved {path} ({path.stat().st_size / 1e6:.1f} MB, "
          f"{len(images)} frames x {seconds_per_frame:.0f}s)")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shot")
    s.add_argument("--url", default=DEFAULT_URL)
    s.add_argument("--name", required=True)
    s.add_argument("--scroll-to", default=None)
    s.add_argument("--click", default=None)
    g = sub.add_parser("gif")
    g.add_argument("--frames", nargs="+", required=True)
    g.add_argument("--out", default="demo.gif")
    g.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    if args.cmd == "shot":
        shot(args.url, args.name, args.scroll_to, args.click)
    else:
        gif(args.frames, args.out, args.seconds)


if __name__ == "__main__":
    main()
