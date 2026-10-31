from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path
import subprocess
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT, FPS = 1080, 1920, 30
RENDER_VERSION = "editorial-film-3-data-driven-orders"
BG, PANEL, INK, MUTED, MINT, ORANGE = "#14201E", "#20312B", "#FFF0D8", "#B4B9A7", "#BDF4BC", "#F6B781"


def balance(principal: float, rate: float, years: float) -> float:
    return principal * (1 + rate) ** years


def frame_ranges(timestamps: list[dict[str, Any]], fps: int = FPS) -> list[tuple[int, int]]:
    if not timestamps:
        raise ValueError("No edit timings")
    boundaries = [0]
    for index, item in enumerate(timestamps):
        if index and abs(item["start_sec"] - timestamps[index - 1]["end_sec"]) > 0.001:
            raise ValueError("Timeline gap or overlap")
        end = round(float(item["end_sec"]) * fps)
        if end <= boundaries[-1]:
            raise ValueError("Scene shorter than one complete frame")
        boundaries.append(end)
    return list(zip(boundaries, boundaries[1:]))


def caption_groups(words: list[dict[str, Any]], *, max_words=5, max_characters=32) -> list[list[dict[str, Any]]]:
    """Group words into readable cues; avoid ending on bare function words."""
    from atnos.integrations.caption_align_service import semantic_phrase_cues

    cues = semantic_phrase_cues(words, max_words=max_words, max_characters=max_characters)
    return [list(cue.get("words") or []) for cue in cues if cue.get("words")]


def _ease(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


@lru_cache(maxsize=80)
def _font(size: int, serif: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / ("georgiai.ttf" if serif else "bahnschrift.ttf")), size)


def _text(draw, xy, value, size=60, fill=INK, serif=False, max_width=910):
    font = _font(size, serif)
    while draw.textlength(value, font=font) > max_width and size > 20:
        size -= 2
        font = _font(size, serif)
    draw.text(xy, value, font=font, fill=fill, stroke_width=0)


def _base(index: int, count: int, *, saving_first: bool = False, footer: str | None = None) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    for x in range(-HEIGHT, WIDTH, 170):
        draw.line((x, HEIGHT, x + HEIGHT, 0), fill="#182622", width=1)
    draw.ellipse((72, 89, 89, 106), fill=MINT)
    _text(draw, (106, 78), "WEALTH BLUEPRINT", size=26, fill=MUTED)
    _text(draw, (838, 78), "FIELD NOTES", size=24, fill=MUTED)
    draw.line((74, 140, 1000, 140), fill="#405047", width=2)
    _text(draw, (78, 1778), footer or ("BUILD IT FIRST.  THEN GIVE IT TIME." if saving_first else "ONE DEPOSIT.  A LONGER VIEW."), size=24, fill=MUTED)
    return image


def _pill(draw, xy, text, width, accent=MINT):
    x, y = xy
    draw.rounded_rectangle((x, y, x + width, y + 66), radius=33, fill=PANEL, outline="#4C6254", width=2)
    _text(draw, (x + 24, y + 13), text, size=31, fill=accent, max_width=width - 45)


def _photo(image: Image.Image, frame: Image.Image | None, y=820):
    if frame is None:
        return
    mask = Image.new("L", frame.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, frame.width - 1, frame.height - 1), radius=34, fill=255)
    image.paste(frame, (74, y), mask)


def draw_scene(brief: dict[str, Any], scene: dict[str, Any], progress: float, index: int,
               photo: Image.Image | None = None) -> Image.Image:
    if brief.get("schema_version") == "production-order-1":
        return _draw_order_scene(brief, scene, progress, index, photo)
    image = _base(index, len(brief["scenes"]), saving_first=bool(brief.get("saving_plan")))
    draw = ImageDraw.Draw(image)
    p = _ease(min(1, progress * 3.2))
    principal, rate = brief["premise"]["principal"], brief["premise"]["annual_rate"]
    kind = scene["kind"]
    if kind == "hook":
        _text(draw, (76, 226), "Your $10,000.", size=98, serif=True)
        _text(draw, (70, 365), "Nearly", size=67, fill=MUTED)
        _text(draw, (62, 447), "$150,000?", size=184, fill=MINT)
        if brief.get("saving_plan"):
            _pill(draw, (77, 690), "SAVE FIRST", 296)
            _pill(draw, (397, 690), "THEN 40 YEARS AT 7%", 600, accent=INK)
        else:
            _pill(draw, (77, 690), "IN 40 YEARS", 296)
            _pill(draw, (397, 690), "IF IT GROWS 7%", 459, accent=INK)
        _photo(image, photo)
    elif kind == "save_goal":
        weekly = brief["saving_plan"]["weekly_amount"]
        _pill(draw, (78, 218), "STEP 1 / BUILD YOUR CAPITAL", 803)
        _text(draw, (78, 355), "Make it weekly.", size=96, serif=True)
        _text(draw, (68, 540), f"${weekly:,}", size=220, fill=MINT)
        _text(draw, (80, 805), "A WEEK, NOT ALL AT ONCE", size=38, fill=MUTED)
        draw.rounded_rectangle((78, 970, 1000, 1340), radius=30, fill=PANEL)
        _text(draw, (110, 1022), "Start with what fits", size=64, serif=True)
        _text(draw, (110, 1107), "your budget.", size=82, fill=MINT)
        _text(draw, (110, 1240), "Essentials first. Adjust the amount.", size=31, fill=MUTED)
    elif kind == "save_transfer":
        _text(draw, (78, 227), "Pay yourself", size=106, serif=True)
        _text(draw, (78, 374), "on payday.", size=111, serif=True, fill=MINT)
        for y, label, detail in [(655, "PAYDAY", "Your income arrives"), (1100, "SEPARATE SAVINGS", "Automatic transfer")]:
            draw.rounded_rectangle((78, y, 1000, y + 240), radius=30, fill=PANEL)
            _text(draw, (110, y + 32), label, size=33, fill=MUTED)
            _text(draw, (110, y + 102), detail, size=66)
        draw.line((535, 925, 535, 1050), fill=ORANGE, width=9)
        draw.polygon(((535, 1080), (510, 1040), (560, 1040)), fill=ORANGE)
        dot_y = 934 + 103 * p
        draw.ellipse((521, dot_y - 14, 549, dot_y + 14), fill=MINT)
    elif kind == "save_redirect":
        _text(draw, (78, 228), "Redirect the money.", size=84, serif=True)
        _text(draw, (80, 405), "UNUSED SUBSCRIPTION", size=44, fill=MUTED)
        draw.line((80, 433, 620 * p + 80, 433), fill=ORANGE, width=5)
        _text(draw, (80, 510), "+ EXTRA INCOME", size=62, fill=MINT)
        _pill(draw, (80, 671), "INTO YOUR WEEKLY GOAL", 735)
        _photo(image, photo)
    elif kind == "save_progress":
        plan = brief["saving_plan"]
        _text(draw, (78, 227), "Small deposits.", size=98, serif=True)
        _text(draw, (78, 366), "Real progress.", size=97, serif=True, fill=MINT)
        _text(draw, (80, 550), f"${plan['weekly_amount']:,} / WEEK  x  {plan['weeks']} WEEKS", size=55)
        filled = round(plan["weeks"] * p)
        for n in range(100):
            x, y = 83 + (n % 10) * 92, 689 + (n // 10) * 50
            draw.rounded_rectangle((x, y, x + 76, y + 33), radius=7, fill=MINT if n < filled else PANEL)
        _text(draw, (75, 1227), f"${filled * plan['weekly_amount']:,}", size=142, fill=MINT)
        _text(draw, (80, 1416), "SAVING PHASE / BEFORE INVESTING", size=28, fill=MUTED)
    elif kind == "save_buffer":
        _text(draw, (78, 227), "Two different jobs.", size=84, serif=True)
        for y, label, detail, color in [(566, "EMERGENCY FUND", "Keep it accessible.", ORANGE),
                                        (995, "LONG-TERM MONEY", "Give it time.", MINT)]:
            draw.rounded_rectangle((78, y, 1000, y + 300), radius=30, fill=PANEL, outline=color, width=3)
            _text(draw, (110, y + 45), label, size=38, fill=color)
            _text(draw, (110, y + 140), detail, size=71, serif=True)
    elif kind == "time":
        _text(draw, (78, 226), "The quiet", size=106, serif=True)
        _text(draw, (78, 366), "advantage.", size=110, serif=True)
        _text(draw, (76, 538), "40 YEARS", size=138, fill=MINT)
        _photo(image, photo)
    elif kind == "setup":
        _text(draw, (78, 232), "Now the long-term part." if brief.get("saving_plan") else "Start with one deposit.", size=75, serif=True)
        _text(draw, (66, 422), "$10,000", size=203)
        _pill(draw, (78, 685), "7% PER YEAR", 360)
        _pill(draw, (456, 685), "NO EXTRA DEPOSITS", 550)
        draw.rounded_rectangle((78, 898, 1000, 1325), radius=35, fill=PANEL)
        _text(draw, (110, 943), "YOUR STARTING CAPITAL", size=32, fill=MUTED)
        for n in range(10):
            x = 115 + n * 84
            top = 1170 - int(94 * _ease(p * 1.2 - n * .025))
            draw.rounded_rectangle((x, top, x + 62, 1228), radius=8, fill=MINT)
        _text(draw, (110, 1257), "40 years starts here. Returns vary." if brief.get("saving_plan") else "A constant-rate illustration", size=27, fill=MUTED)
    elif kind == "gain":
        year = scene["year"]
        base = balance(principal, rate, year - 1)
        gain = base * rate
        _text(draw, (78, 231), "The first year." if year == 1 else "The second year.", size=88, serif=True)
        _text(draw, (78, 399), f"${base:,.0f}  x  7%", size=74, fill=MUTED)
        _text(draw, (66, 565), f"+${round(gain * p):,}", size=205, fill=MINT)
        _text(draw, (80, 807), "YOUR GAIN", size=32, fill=MUTED)
        draw.line((80, 920, 1000, 920), fill="#56725B", width=3)
        _text(draw, (78, 988), "New balance", size=45, serif=True)
        _text(draw, (74, 1085), f"${round(base + gain * p):,}", size=137)
        if year == 2:
            _pill(draw, (79, 1322), "+$49 FROM LAST YEAR'S GAIN", 859, accent=ORANGE)
    elif kind == "mechanism":
        _text(draw, (78, 225), "Now the gains", size=85, serif=True)
        _text(draw, (78, 345), "earn gains.", size=105, serif=True, fill=MINT)
        for n, (label, amount, color) in enumerate([("ORIGINAL DEPOSIT", "$10,000", INK), ("FIRST-YEAR GAIN", "+$700", MINT), ("INTEREST ON THAT GAIN", "+$49", ORANGE)]):
            y = 600 + n * 250
            draw.rounded_rectangle((78, y, 1000, y + 210), radius=28, fill=PANEL)
            _text(draw, (108, y + 24), label, size=28, fill=MUTED)
            _text(draw, (108, y + 75), amount, size=94, fill=color)
    elif kind == "curve":
        year = scene["from_year"] + (scene["year"] - scene["from_year"]) * p
        amount = balance(principal, rate, year)
        _text(draw, (78, 225), "Let time work.", size=100, serif=True)
        _text(draw, (73, 404), f"${round(amount):,}", size=182, fill=MINT)
        _pill(draw, (80, 628), f"{round(year)} YEARS", 284)
        # A fixed scale and origin let three adjacent scenes form one continuous chart.
        left, top, right, bottom = 90, 844, 945, 1350
        maximum = balance(principal, rate, 40)
        # Supersample only the plot so moving diagonals do not shimmer at pixel edges.
        plot = Image.new("RGB", (1774, 1076), BG)
        plot_draw = ImageDraw.Draw(plot)
        transform = lambda point: ((point[0] - left + 16) * 2, (point[1] - top + 16) * 2)
        for fraction in (0, .5, 1):
            y = bottom - (bottom - top) * fraction
            plot_draw.line((transform((left, y)), transform((right, y))), fill="#354B3D", width=4)
        all_points = [(left + (right - left) * t / 40, bottom - (bottom - top) * balance(principal, rate, t) / maximum)
                      for t in [i / 10 for i in range(401)]]
        plot_draw.line([transform(point) for point in all_points], fill="#3B5141", width=8)
        points = [point for i, point in enumerate(all_points) if i / 10 <= year]
        if len(points) >= 2:
            plot_draw.line([transform(point) for point in points], fill=MINT, width=20, joint="curve")
            x, y = transform(points[-1])
            plot_draw.ellipse((x - 26, y - 26, x + 26, y + 26), fill=INK)
        image.paste(plot.resize((887, 538), Image.Resampling.LANCZOS), (left - 16, top - 16))
        for t in (0, 10, 20, 30, 40):
            _text(draw, (left + (right - left) * t / 40 - 12, 1390), str(t), size=29, fill=MUTED)
        _text(draw, (82, 731), "7% ANNUALLY / NO EXTRA DEPOSITS", size=28, fill=MUTED)
    elif kind == "comparison":
        early = balance(principal, rate, 10) - principal
        late = balance(principal, rate, 40) - balance(principal, rate, 30)
        _text(draw, (78, 225), "Same ten years.", size=92, serif=True)
        _text(draw, (78, 355), "Very different gains.", size=66, fill=MINT)
        for n, (label, value, color) in enumerate([("FIRST DECADE", early, INK), ("LAST DECADE", late, MINT)]):
            y = 610 + n * 360
            _text(draw, (80, y), label, size=35, fill=MUTED)
            _text(draw, (78, y + 61), f"+${value:,.0f}", size=117, fill=color)
            draw.rounded_rectangle((80, y + 215, 80 + max(14, 900 * value / late * p), y + 258), radius=15, fill=color)
        _pill(draw, (80, 1358), f"{late / early:.1f}x AS MUCH GROWTH", 784, accent=ORANGE)
    elif kind == "patience":
        _text(draw, (78, 230), "Slow at first.", size=103, serif=True)
        _text(draw, (78, 376), "Then it adds up.", size=86, fill=MINT)
        _pill(draw, (80, 622), "THE CURVE TAKES TIME", 671)
        _photo(image, photo)
    elif kind == "context":
        _text(draw, (78, 227), "Know the assumptions.", size=84, serif=True)
        _text(draw, (78, 425), "This is an illustration.", size=62, fill=MINT)
        for n, (first, second) in enumerate([("7% CONSTANT RETURN", "Actual returns vary."), ("FEES & TAXES", "Reduce what you keep."), ("INFLATION", "Reduces buying power.")]):
            y = 650 + n * 242
            draw.line((80, y - 22, 993, y - 22), fill="#47604E", width=2)
            _text(draw, (82, y), first, size=34, fill=MUTED)
            _text(draw, (82, y + 60), second, size=58)
    elif kind == "close":
        _text(draw, (78, 227), "Your next payday." if brief.get("saving_plan") else "Give your", size=102, serif=True)
        _text(draw, (78, 375), "Your first transfer." if brief.get("saving_plan") else "money time.", size=94, serif=True, fill=MINT)
        _pill(draw, (80, 625), "CHOOSE YOUR WEEKLY AMOUNT" if brief.get("saving_plan") else "TRY YOUR OWN NUMBER", 897)
        _photo(image, photo)
    else:
        raise ValueError(f"Unknown scene kind: {kind}")
    return image


def caption_layout(group, *, safe_area=(55, 180, 1025, 1690)):
    left, top, right, bottom = safe_area
    available = right - left - 48
    font = _font(48)
    for size in range(48, 29, -2):
        font = _font(size)
        widths = [font.getlength(w["text"] + " ") for w in group]
        if sum(widths) <= available:
            break
    else:
        raise ValueError("Caption cannot fit the configured safe area at a readable font size")
    x = (left + right - sum(widths)) / 2
    y = bottom - 120
    box = (x - 24, y, x + sum(widths) + 24, y + 88)
    if box[0] < left or box[1] < top or box[2] > right or box[3] > bottom:
        raise ValueError("Caption layout escapes safe area")
    return font, widths, x, y, box


def draw_captions(image: Image.Image, groups, timestamp: float, *, safe_area=(55, 180, 1025, 1690)) -> None:
    group = next((g for g in groups if g[0]["start_sec"] <= timestamp < g[-1]["end_sec"]), None)
    if not group:
        return
    draw = ImageDraw.Draw(image)
    font, widths, x, y, box = caption_layout(group, safe_area=safe_area)
    draw.rounded_rectangle(box, radius=20, fill="#0E1816")
    for word, width in zip(group, widths, strict=True):
        color = MINT if word["start_sec"] <= timestamp <= word["end_sec"] else INK
        draw.text((x, y + 15), word["text"], font=font, fill=color)
        x += width


def photo_reader(ffmpeg: str, source: Path, *, start: float, count: int):
    command = [ffmpeg, "-v", "error", "-ss", str(start), "-i", str(source), "-an", "-vf",
               "fps=30,scale=932:590:force_original_aspect_ratio=increase,crop=932:590",
               "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(count):
            data = process.stdout.read(932 * 590 * 3)
            if len(data) != 932 * 590 * 3:
                raise RuntimeError(f"Source too short or decode failed: {source.name}")
            yield Image.frombytes("RGB", (932, 590), data)
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=15)


def render_scene_clip(*, ffmpeg: str, brief, scene, index, timing, frame_range,
                      groups, output: Path, source: Path | None, previous: Image.Image | None):
    start_frame, end_frame = frame_range
    count = end_frame - start_frame
    reader = photo_reader(ffmpeg, source, start=scene.get("source_start", 0), count=count) if source else None
    output.parent.mkdir(parents=True, exist_ok=True)
    log = output.with_suffix(".log")
    with log.open("wb") as stderr:
        process = subprocess.Popen([ffmpeg, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1080x1920",
                                    "-r", "30", "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                                    "-pix_fmt", "yuv420p", "-threads", "4", "-movflags", "+faststart", str(output)],
                                   stdin=subprocess.PIPE, stderr=stderr)
        try:
            for frame in range(count):
                photo = next(reader) if reader else None
                progress = frame / max(1, count - 1)
                image = draw_scene(brief, scene, progress, index, photo)
                if previous is not None and frame < 6 and scene["kind"] != "curve" and brief.get("schema_version") != "production-order-1":
                    image = Image.blend(previous, image, _ease((frame + 1) / 6))
                if frame == count - 1:
                    last = image.copy()
                draw_captions(image, groups, (start_frame + frame) / FPS,
                              safe_area=brief.get("safe_area", (55, 180, 1025, 1690)))
                process.stdin.write(image.tobytes())
            process.stdin.close()
            if process.wait(timeout=90) != 0:
                raise RuntimeError(f"Encoding failed; inspect {log}")
        except BaseException:
            process.kill()
            process.wait(timeout=15)
            raise
        finally:
            if reader:
                reader.close()
    return last


def _wrapped(draw, xy, text, *, width=924, size=82, max_lines=3, fill=INK, serif=True):
    for chosen in range(size, 19, -2):
        lines, line = [], ""
        font = _font(chosen, serif)
        for word in str(text).split():
            trial = f"{line} {word}".strip()
            if line and draw.textlength(trial, font=font) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        if line:
            lines.append(line)
        if len(lines) <= max_lines and all(draw.textlength(value, font=font) <= width for value in lines):
            for index, value in enumerate(lines):
                draw.text((xy[0], xy[1] + index * chosen * 1.24), value, font=font, fill=fill)
            return xy[1] + len(lines) * chosen * 1.24
    raise ValueError("Editorial text does not fit its safe area")


def _claim_label(claim):
    value = float(claim["result"])
    number = f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"
    if claim["unit"] == "dollars":
        return f"${number}"
    if claim["unit"] == "percent":
        return f"{number}%"
    return number if claim["unit"] == "none" else f"{number} {claim['unit']}"


def _draw_order_scene(brief, scene, progress, index, photo):
    from atnos.production.steps.order_script import NumericClaim, calculate_claim

    image = _base(index, len(brief["scenes"]), footer=brief.get("footer", "WEALTH BLUEPRINT / YOUR NEXT MOVE"))
    draw = ImageDraw.Draw(image)
    p = _ease(min(1, max(0, progress) * 2))
    kind = scene["kind"]
    _pill(draw, (78, 210), f"{index + 1:02} / {len(brief['scenes']):02}", 206)
    _wrapped(draw, (78, 333), scene["headline"], size=91, max_lines=3)
    claims_by_id = {c["id"]: c for c in brief["claims"]}
    claims = [claims_by_id[key] for key in scene["claim_ids"]]
    for claim in claims:
        tolerance = .5 if claim["unit"] in {"dollars", "PLN"} else .005
        if not math.isclose(calculate_claim(NumericClaim.model_validate(claim)), claim["result"], rel_tol=0, abs_tol=tolerance):
            raise ValueError("Renderer rejected an unverified numeric claim")
    if kind == "footage":
        if photo is None:
            raise ValueError("A planned footage scene has no decoded image")
        _photo(image, photo, y=850)
    elif kind in {"steps", "flow"}:
        labels = scene["labels"]
        if not labels:
            raise ValueError("Action graphic has no labels")
        for n, label in enumerate(labels):
            y = 780 + n * 245
            draw.rounded_rectangle((78, y, 1000, y + 183), radius=26, fill=PANEL)
            draw.rounded_rectangle((78, y, 90, y + int(183 * p)), radius=5, fill=MINT)
            _wrapped(draw, (120, y + 36), label, width=825, size=57, max_lines=2, serif=False)
            if kind == "flow" and n + 1 < len(labels):
                draw.line((535, y + 190, 535, y + 222), fill=ORANGE, width=6)
                draw.polygon(((535, y + 237), (520, y + 216), (550, y + 216)), fill=ORANGE)
    elif kind == "number":
        if len(claims) != 1:
            raise ValueError("A number card requires exactly one claim")
        _text(draw, (76, 805), _claim_label(claims[0]), size=174, fill=MINT)
        draw.line((80, 1070, 80 + 915 * p, 1070), fill=ORANGE, width=8)
        _wrapped(draw, (80, 1140), claims[0]["assumptions"], size=37, max_lines=5, fill=MUTED, serif=False)
    elif kind == "comparison":
        if len(claims) != 2 or len(scene["labels"]) != 2:
            raise ValueError("Comparison requires two claims and two labels")
        maximum = max(abs(c["result"]) for c in claims)
        for n, (claim, label) in enumerate(zip(claims, scene["labels"], strict=True)):
            y = 765 + n * 333
            _text(draw, (80, y), label, size=42, fill=MUTED)
            _text(draw, (78, y + 70), _claim_label(claim), size=112, fill=MINT if n else INK)
            draw.rounded_rectangle((80, y + 225, 80 + max(8, 900 * abs(claim["result"]) / max(1, maximum) * p), y + 251), radius=10, fill=MINT if n else INK)
    elif kind == "chart":
        from atnos.production.chart_series import verified_chart_series

        if len(claims) != 1:
            raise ValueError("Chart requires one calculation")
        series = verified_chart_series(claims[0], scene.get("narration", ""))
        _text(draw, (78, 740), _claim_label(claims[0]), size=126, fill=MINT)
        left, right, top, bottom = 95, 968, 955, 1330
        maximum = max(series["values"])
        for fraction in (0, .5, 1):
            y = bottom - (bottom - top) * fraction
            draw.line((left, y, right, y), fill="#405047", width=2)
        points = [(left + (right-left)*n/200, bottom - (bottom-top)*value/maximum) for n, value in enumerate(series["values"])]
        draw.line(points, fill="#405047", width=4)
        visible = points[:max(2, round(201*p))]
        draw.line(visible, fill=MINT, width=9, joint="curve")
        _text(draw, (78, 1380), series["label"], size=33, fill=MUTED)
    else:
        raise ValueError(f"Unknown editorial order scene: {kind}")
    return image


def create_order_covers(directory: Path, *, headline: str, detail: str, visual: Path | None = None, brand: dict | None = None) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    background = brand['background'] if brand else BG
    panel = brand['panel'] if brand else PANEL
    ink = brand['text'] if brand else INK
    accent = brand['accent'] if brand else MINT
    for width, height, name in [(1080, 1920, "cover_portrait.png"), (1280, 720, "thumbnail_landscape.png")]:
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        for x in range(-height, width, 145):
            draw.line((x, height, x + height, 0), fill="#26372D", width=2)
        _text(draw, (68, 68), "WEALTH BLUEPRINT", size=28, fill=accent if brand else MUTED)
        portrait = height > width
        if visual is not None:
            box = (70, 765, 1010, 1425) if portrait else (665, 165, 1210, 585)
            with Image.open(visual) as source:
                photo = ImageOps.contain(source.convert('RGB'), (box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS)
            draw.rounded_rectangle(box, radius=24, fill=panel)
            image.paste(photo, (box[0]+(box[2]-box[0]-photo.width)//2, box[1]+(box[3]-box[1]-photo.height)//2))
            _wrapped(draw, (70, 250 if portrait else 178), headline, width=940 if portrait else 520,
                     size=112 if portrait else 76, max_lines=3 if portrait else 4, fill=ink, serif=bool(brand))
            draw.line((70, 710 if portrait else 578, 350, 710 if portrait else 578), fill=accent, width=9)
            _wrapped(draw, (70, 1510 if portrait else 612), detail, width=width-140,
                     size=52 if portrait else 33, max_lines=4 if portrait else 2, fill=accent, serif=False)
            path = directory / name
            image.save(path)
            paths.append(path)
            continue
        end = _wrapped(draw, (68, 340 if portrait else 160), headline, width=width-140,
                       size=130 if portrait else 78, max_lines=5 if portrait else 3, fill=MINT)
        draw.line((70, end+85, width-70, end+85), fill=ORANGE, width=9)
        _wrapped(draw, (70, 1350 if portrait else 590), detail, width=width-140, size=44 if portrait else 32,
                 max_lines=4 if portrait else 2, fill=INK, serif=False)
        path = directory / name
        image.save(path)
        paths.append(path)
    return tuple(paths)


def create_covers(directory: Path) -> None:
    """Export code-native cover graphics in both delivery aspect ratios."""
    directory.mkdir(parents=True, exist_ok=True)
    for width, height, name in [(1080, 1920, "cover_portrait.png"), (1280, 720, "thumbnail_landscape.png")]:
        image = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(image)
        portrait = height > width
        for x in range(-height, width, 120):
            draw.line((x, height, x + height, 0), fill="#26372D", width=2)
        _text(draw, (65, 57 if not portrait else 112), "WEALTH BLUEPRINT", size=25, fill=MUTED)
        _text(draw, (65, 130 if not portrait else 257), "The 40-year effect.", size=62 if not portrait else 78, serif=True)
        _text(draw, (60, 250 if not portrait else 455), "$10K", size=158 if not portrait else 211)
        if portrait:
            draw.line((165, 755, 165, 934), fill=ORANGE, width=15)
            draw.polygon(((165, 957), (126, 908), (204, 908)), fill=ORANGE)
            _text(draw, (60, 991), "$150K?", size=223, fill=MINT)
            _pill(draw, (69, 1302), "ONE DEPOSIT", 411)
            x0, x1, y0, y1 = 76, 962, 1690, 1475
        else:
            draw.line((482, 344, 634, 344), fill=ORANGE, width=12)
            draw.polygon(((651, 344), (611, 316), (611, 372)), fill=ORANGE)
            _text(draw, (704, 255), "$150K?", size=144, fill=MINT, max_width=513)
            x0, x1, y0, y1 = 83, 1175, 596, 477
        points = [(x0 + (x1 - x0) * i / 100, y0 - (y0 - y1) * (1.07 ** (40 * i / 100) - 1) / (1.07 ** 40 - 1)) for i in range(101)]
        draw.line(points, fill=MINT, width=7, joint="curve")
        _text(draw, (68, height - 65 if not portrait else height - 112), "IF IT GROWS 7% A YEAR. NO EXTRA DEPOSITS.", size=23 if not portrait else 25, fill=MUTED)
        image.save(directory / name)
