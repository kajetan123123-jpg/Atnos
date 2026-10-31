"""Deterministic hand-drawn-feeling illustrative stills for Wealth Blueprint explain scenes.

These replace geometric library placeholders (stick figures / orange-circle bars) with
diagrams that encode narration concepts: savings account choice, weekly auto-transfer, etc.
Palette follows wealth_blueprint_visual_v1 (navy / cream / gold).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

NAVY = (20, 38, 51, 255)
PANEL = (28, 51, 68, 255)
CREAM = (240, 229, 210, 255)
GOLD = (213, 173, 108, 255)
BLUE = (66, 101, 121, 255)
TEAL = (75, 121, 119, 255)
ORANGE = (193, 138, 73, 255)
INK = (20, 38, 51, 255)
TRANSPARENT = (0, 0, 0, 0)

W, H = 1080, 1920


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\seguisb.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"E:\Atnos\App\runtime-recovered\api\fonts\segoeui.ttf",
    ):
        path = Path(name)
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _paper_bg(size: tuple[int, int] = (W, H), *, transparent: bool = True) -> Image.Image:
    if transparent:
        return Image.new("RGBA", size, TRANSPARENT)
    img = Image.new("RGBA", size, NAVY)
    draw = ImageDraw.Draw(img)
    for x in range(0, size[0], 48):
        draw.line((x, 0, x, size[1]), fill=(25, 46, 60, 90), width=1)
    for y in range(0, size[1], 48):
        draw.line((0, y, size[0], y), fill=(25, 46, 60, 90), width=1)
    return img


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill, outline, width: int = 3, radius: int = 28) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _label(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], *, size: int = 36, fill=CREAM, anchor: str = "mm") -> None:
    draw.text(xy, text, font=_font(size), fill=fill, anchor=anchor)


def draw_raise_absorption_flow(dest: Path) -> Path:
    """Raise forks into spend vs keep — branching, not a linear box chain."""
    img = _paper_bg(transparent=True)
    draw = ImageDraw.Draw(img)
    _label(draw, "THE RAISE SPLITS", (540, 380), size=28, fill=BLUE)

    _rounded(draw, (290, 480, 790, 660), fill=PANEL, outline=GOLD, width=5, radius=32)
    _label(draw, "THE RAISE", (540, 545), size=36, fill=CREAM)
    _label(draw, "ONE AMOUNT", (540, 610), size=24, fill=GOLD)

    draw.polygon([(480, 680), (600, 680), (540, 740)], fill=GOLD)
    draw.polygon([(300, 760), (360, 860), (300, 860)], fill=ORANGE)
    draw.polygon([(780, 760), (720, 860), (780, 860)], fill=TEAL)

    _rounded(draw, (90, 880, 430, 1120), fill=PANEL, outline=ORANGE, width=5, radius=32)
    _label(draw, "SPEND", (260, 980), size=34, fill=CREAM)
    _label(draw, "THE REST", (260, 1050), size=24, fill=ORANGE)

    _rounded(draw, (650, 880, 990, 1120), fill=PANEL, outline=TEAL, width=5, radius=32)
    _label(draw, "KEEP", (820, 980), size=34, fill=CREAM)
    _label(draw, "SAVE FIRST", (820, 1050), size=24, fill=TEAL)

    _label(draw, "TWO BRANCHES — NOT A SINGLE LINE", (540, 1240), size=26, fill=BLUE)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(dest, format="PNG")
    return dest


def draw_savings_account_choice(dest: Path) -> Path:
    """Wallet (spend) → savings jar (keep) — paper sketch, not nested UI cards."""
    img = _paper_bg(transparent=True)
    draw = ImageDraw.Draw(img)
    _label(draw, "ONE PAYCHECK → TWO POCKETS", (540, 420), size=28, fill=BLUE)

    # Wallet (checking / spend)
    _rounded(draw, (140, 560, 420, 820), fill=(111, 74, 49, 255), outline=INK, width=5, radius=36)
    _rounded(draw, (300, 620, 400, 740), fill=(75, 48, 35, 255), outline=ORANGE, width=4, radius=22)
    draw.ellipse((330, 660, 360, 690), fill=ORANGE)
    _label(draw, "CHECKING · SPEND", (280, 900), size=28, fill=BLUE)

    # Dashed flow
    for x0 in range(440, 560, 28):
        draw.line([(x0, 700), (x0 + 16, 700)], fill=ORANGE, width=6)
    draw.polygon([(560, 680), (600, 700), (560, 720)], fill=ORANGE)

    # Savings jar (organic silhouette)
    jar = [(640, 560), (900, 560), (930, 900), (610, 900)]
    draw.polygon(jar, fill=CREAM, outline=INK, width=5)
    _rounded(draw, (690, 500, 850, 560), fill=(232, 220, 198, 255), outline=INK, width=4, radius=14)
    draw.ellipse((720, 680, 800, 760), fill=GOLD, outline=INK, width=3)
    draw.ellipse((780, 740, 840, 800), fill=ORANGE, outline=INK, width=3)
    draw.ellipse((860, 480, 920, 540), fill=GOLD, outline=INK, width=3)
    draw.line([(875, 510), (888, 523), (908, 495)], fill=INK, width=5)
    _label(draw, "SAVINGS · KEEP", (770, 980), size=28, fill=INK)
    _label(draw, "SEPARATE ACCOUNT = MONEY YOU DO NOT TOUCH", (540, 1100), size=26, fill=BLUE)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(dest, format="PNG")
    return dest


def draw_weekly_auto_transfer(dest: Path) -> Path:
    """Torn calendar leaf → coin path → savings jar (automation, not a date grid)."""
    img = _paper_bg(transparent=True)
    draw = ImageDraw.Draw(img)
    _label(draw, "SAME DAY EACH WEEK", (540, 420), size=28, fill=BLUE)

    # Calendar leaf
    leaf = [(120, 560), (420, 540), (440, 980), (100, 960)]
    draw.polygon(leaf, fill=CREAM, outline=INK, width=5)
    for x in (180, 270, 360):
        draw.line([(x, 540), (x, 500)], fill=INK, width=5)
    _label(draw, "FRI", (270, 640), size=40, fill=INK)
    draw.ellipse((200, 700, 340, 840), fill=GOLD, outline=INK, width=5)
    _label(draw, "7", (270, 770), size=48, fill=INK)
    _label(draw, "AUTO ON", (270, 920), size=26, fill=BLUE)

    # Coin path
    draw.arc((400, 680, 620, 820), start=200, end=340, fill=ORANGE, width=8)
    draw.ellipse((500, 700, 560, 760), fill=GOLD, outline=INK, width=3)
    draw.polygon([(600, 740), (640, 760), (600, 780)], fill=ORANGE)

    # Savings jar
    jar = [(680, 560), (940, 560), (970, 900), (650, 900)]
    draw.polygon(jar, fill=CREAM, outline=INK, width=5)
    _rounded(draw, (730, 500, 890, 560), fill=(232, 220, 198, 255), outline=INK, width=4, radius=14)
    draw.ellipse((760, 680, 840, 760), fill=GOLD, outline=INK, width=3)
    draw.ellipse((820, 740, 880, 800), fill=ORANGE, outline=INK, width=3)
    _label(draw, "INTO SAVINGS", (810, 980), size=28, fill=INK)
    _label(draw, "NO WILLPOWER — THE TRANSFER JUST HAPPENS", (540, 1100), size=26, fill=BLUE)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(dest, format="PNG")
    return dest


def draw_emergency_fund_runway(dest: Path) -> Path:
    """Shield + runway bars = emergency fund."""
    img = _paper_bg(transparent=True)
    draw = ImageDraw.Draw(img)
    shield = [(540, 480), (780, 560), (740, 920), (540, 1040), (340, 920), (300, 560)]
    draw.polygon(shield, fill=PANEL, outline=GOLD, width=6)
    _label(draw, "3 MONTHS", (540, 720), size=40, fill=GOLD)
    for i, w in enumerate((220, 340, 460)):
        y = 1180 + i * 70
        _rounded(draw, (540 - w // 2, y, 540 + w // 2, y + 44), fill=GOLD if i == 2 else BLUE, outline=CREAM, width=3, radius=12)
    _label(draw, "RUNWAY", (540, 1450), size=36, fill=CREAM)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")
    return dest


def draw_notebook_log(dest: Path) -> Path:
    """Open notebook with a visible transfer line — ledger is material, not a habit icon."""
    img = _paper_bg(transparent=True)
    draw = ImageDraw.Draw(img)
    _label(draw, "WRITE IT DOWN", (540, 400), size=28, fill=BLUE)

    _rounded(draw, (220, 480, 860, 1280), fill=CREAM, outline=INK, width=6, radius=28)
    _rounded(draw, (220, 480, 290, 1280), fill=(232, 220, 198, 255), outline=INK, width=4, radius=18)
    for row in range(6):
        y = 620 + row * 90
        draw.line([(320, y), (800, y)], fill=(66, 101, 121, 90), width=3)
    _label(draw, "DATE", (360, 580), size=26, fill=BLUE, anchor="lm")
    _label(draw, "TRANSFER", (520, 580), size=26, fill=BLUE, anchor="lm")
    _rounded(draw, (320, 630, 780, 700), fill=GOLD, outline=INK, width=3, radius=12)
    _label(draw, "FRI · CHECKING → SAVINGS", (550, 665), size=28, fill=INK)
    # Pen
    draw.polygon([(820, 760), (860, 740), (870, 980), (830, 1000)], fill=INK)
    draw.polygon([(830, 1000), (870, 980), (850, 1040)], fill=GOLD)
    _label(draw, "ENTRY COMPLETE — PROGRESS VISIBLE", (540, 1380), size=26, fill=INK)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(dest, format="PNG")
    return dest


def draw_habit_loop(dest: Path) -> Path:
    """Circular habit loop: cue → action → reward."""
    img = _paper_bg(transparent=True)
    draw = ImageDraw.Draw(img)
    cx, cy, r = 540, 860, 280
    draw.arc((cx - r, cy - r, cx + r, cy + r), start=40, end=320, fill=GOLD, width=14)
    draw.polygon([(cx + int(r * 0.72), cy - int(r * 0.55)), (cx + r - 10, cy - 40), (cx + int(r * 0.55), cy - 20)], fill=GOLD)
    nodes = [("CUE", 540, 560), ("ACT", 780, 980), ("KEEP", 300, 980)]
    for label, x, y in nodes:
        draw.ellipse((x - 70, y - 70, x + 70, y + 70), fill=PANEL, outline=CREAM, width=4)
        _label(draw, label, (x, y), size=28, fill=GOLD)
    _label(draw, "HABIT LOOP", (540, 1280), size=36, fill=CREAM)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")
    return dest


ILLUSTRATIVE_SPECS: list[dict] = [
    {
        "id": "illust_raise_absorption_flow_v01",
        "type": "icon",
        "file_path": "illustrative/illust_raise_absorption_flow_v01.png",
        "tags": ["finance", "income", "expenses", "savings", "raise", "budget", "money"],
        "description": "Editorial diagram: raise absorbed by new expenses vs savings keep-slice",
        "production_role": "icon",
        "drawer": draw_raise_absorption_flow,
        "concepts": ["raise_absorption"],
    },
    {
        "id": "illust_savings_account_choice_v01",
        "type": "icon",
        "file_path": "illustrative/illust_savings_account_choice_v01.png",
        "tags": ["finance", "savings", "account", "choose", "banking", "money"],
        "description": "Editorial diagram: choose a separate savings account over checking",
        "production_role": "icon",
        "drawer": draw_savings_account_choice,
        "concepts": ["savings_account", "account_choice"],
    },
    {
        "id": "illust_weekly_auto_transfer_v01",
        "type": "icon",
        "file_path": "illustrative/illust_weekly_auto_transfer_v01.png",
        "tags": ["finance", "transfer", "automatic", "weekly", "savings", "banking", "money"],
        "description": "Editorial diagram: automatic weekly transfer into savings",
        "production_role": "icon",
        "drawer": draw_weekly_auto_transfer,
        "concepts": ["weekly_transfer", "auto_transfer", "money_movement"],
    },
    {
        "id": "illust_emergency_fund_runway_v01",
        "type": "icon",
        "file_path": "illustrative/illust_emergency_fund_runway_v01.png",
        "tags": ["finance", "emergency", "fund", "savings", "money"],
        "description": "Editorial diagram: emergency fund as runway",
        "production_role": "icon",
        "drawer": draw_emergency_fund_runway,
        "concepts": ["emergency_fund"],
    },
    {
        "id": "illust_habit_loop_v01",
        "type": "icon",
        "file_path": "illustrative/illust_habit_loop_v01.png",
        "tags": ["finance", "habit", "budget", "savings", "money"],
        "description": "Editorial diagram: savings habit loop",
        "production_role": "icon",
        "drawer": draw_habit_loop,
        "concepts": ["habit"],
    },
    {
        "id": "illust_notebook_log_v01",
        "type": "icon",
        "file_path": "illustrative/illust_notebook_log_v01.png",
        "tags": ["finance", "notebook", "ledger", "budget", "transfer", "money", "log", "track"],
        "description": "Editorial diagram: notebook records each transfer line",
        "production_role": "icon",
        "drawer": draw_notebook_log,
        "concepts": ["notebook_log"],
    },
]


def ensure_illustrative_stills(library_root: Path) -> list[Path]:
    written: list[Path] = []
    for spec in ILLUSTRATIVE_SPECS:
        dest = library_root / str(spec["file_path"])
        drawer: Callable[[Path], Path] = spec["drawer"]  # type: ignore[assignment]
        drawer(dest)
        written.append(dest)
    return written
