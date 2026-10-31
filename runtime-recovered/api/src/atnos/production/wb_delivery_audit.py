"""Independent, read-only WB delivery audit. Never renders or approves assets.

Callable by Atnos or scripts/run_wb_delivery_audit.py. Outputs belong in a new
report directory, never in a production order. No cloud calls, no model loads.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

VERSION = "wb-delivery-audit-1"
DOCUMENTS = ("edit.json", "project_manifest.json", "script.json", "captions.json",
             "audio_timestamps.json", "semantic_qa.json", "media_qa.json",
             "sound_mix_plan.json", "sound_mix_notes.json", "finance_evidence.json", "covers.json")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return data


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", str(text).casefold().replace("’", "'")))


def resolve_asset(record: dict, catalog_path: Path, app_root: Path) -> Path:
    raw = Path(str(record.get("file_path") or ""))
    if raw.is_absolute():
        return raw
    candidates = [catalog_path.parent / raw, app_root / "config" / raw]
    return next((p for p in candidates if p.is_file()), candidates[0])


def library_inventory(catalog_path: Path, app_root: Path) -> dict:
    catalog = read_json(catalog_path)
    rows = []
    for item in catalog.get("assets", []):
        path = resolve_asset(item, catalog_path, app_root)
        reasons = []
        role = item.get("production_role") or item.get("layer") or "unspecified"
        if item.get("channel") != "wealth_blueprint":
            reasons.append("wrong_channel")
        if item.get("approved") is not True or item.get("quality_status", "APPROVED") != "APPROVED":
            reasons.append("not_approved")
        if not path.is_file():
            reasons.append("file_missing")
        if "placeholders" in path.parts or item.get("source") in {"placeholder", "fixture", "test"}:
            reasons.append("placeholder_not_production")
        if role == "reference_sheet" or "channel_references" in path.parts:
            reasons.append("reference_only")
        if item.get("license_status") not in {"owned", "licensed"}:
            reasons.append("license_unresolved")
        if item.get("type") == "character" and not (item.get("reviewed_by") and item.get("reviewed_at")):
            reasons.append("character_review_not_recorded")
        rows.append({"id": item.get("id"), "role": role, "pose": item.get("pose"),
                     "character": item.get("character"), "path": str(path),
                     "sha256": digest(path) if path.is_file() else None,
                     "eligible_by_metadata": not reasons, "reasons": reasons,
                     "note": "Metadata audit does not establish visual quality or legal rights."})
    return {"catalog": str(catalog_path), "catalog_sha256": digest(catalog_path), "assets": rows,
            "total": len(rows), "eligible_by_metadata": sum(r["eligible_by_metadata"] for r in rows),
            "reasons": dict(Counter(reason for row in rows for reason in row["reasons"]))}


def probe_final(video: Path, ffmpeg: Path, ffprobe: Path) -> dict:
    """Measure the actual muxed audio, not the pre-mix narrator WAV."""
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    probe = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format",
                            "-of", "json", str(video)], capture_output=True, text=True,
                           timeout=60, check=True, **flags)
    result = json.loads(probe.stdout)
    mix = subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-i", str(video),
                          "-map", "0:a:0", "-vn", "-af",
                          "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=600, check=True, **flags)
    match = re.search(r'\{\s*"input_i"[\s\S]*?\}', mix.stderr)
    if not match:
        raise ValueError("FFmpeg returned no final-mix loudness measurement")
    result["loudness"] = json.loads(match.group())
    return result


def calculated(claim: dict) -> float:
    values = [float(v) for v in claim["operands"]]
    if not 2 <= len(values) <= 8 or not all(math.isfinite(v) and abs(v) <= 1e10 for v in values):
        raise ValueError("invalid operands")
    op = claim["operation"]
    if op == "sum":
        result = sum(values)
    elif op == "product":
        result = math.prod(values)
    elif op in {"ratio", "percent"} and len(values) == 2:
        result = values[0] / values[1] if op == "ratio" else values[0] * values[1] / 100
    elif op in {"compound", "monthly_contribution"} and len(values) == 3:
        amount, rate, years = values
        if amount < 0 or not 0 <= rate <= 1 or not 0 <= years <= 100:
            raise ValueError("invalid rate/horizon")
        if op == "compound":
            result = amount * (1 + rate) ** years
        else:
            months = years * 12
            if months < 1 or not math.isclose(months, round(months), abs_tol=1e-8):
                raise ValueError("invalid monthly horizon")
            result = amount * (months if rate == 0 else math.expm1(months * math.log1p(rate / 12)) / (rate / 12))
    else:
        raise ValueError("unsupported calculation")
    if not math.isfinite(result) or abs(result) > 1e12:
        raise ValueError("invalid result")
    return result


def audit_order(order_dir: Path, *, app_root: Path, catalog_path: Path,
                sound_catalog_path: Path, ffmpeg: Path | None = None,
                ffprobe: Path | None = None, human_review: dict | None = None) -> dict:
    order_dir = order_dir.resolve()
    findings: list[dict] = []
    checks: list[dict] = []

    def check(code: str, ok: bool, detail: str, scene: str | None = None):
        row = {"code": code, "status": "passed" if ok else "blocked", "detail": detail, "scene_id": scene}
        checks.append(row)
        if not ok:
            findings.append(row)

    documents, initial_hashes = {}, {}
    for name in DOCUMENTS:
        path = order_dir / name
        if path.is_file():
            initial_hashes[name] = digest(path)
            try:
                documents[name] = read_json(path)
            except (OSError, ValueError) as exc:
                check("invalid_document", False, f"{name}: {exc}")
    for name in DOCUMENTS[:7]:
        check("required_document", name in documents, name)
    manifest = documents.get("project_manifest.json", {})
    edit = documents.get("edit.json", {})
    script = documents.get("script.json", {})
    voice = documents.get("audio_timestamps.json", {})
    captions = documents.get("captions.json", {})
    semantic = documents.get("semantic_qa.json", {})
    evidence = documents.get("finance_evidence.json", {})
    scenes = manifest.get("scenes") or []
    scene_ids = [s.get("scene_id") for s in scenes]
    check("scene_set", bool(scenes) and all(scene_ids) and len(set(scene_ids)) == len(scene_ids),
          "A nonempty unique set of scenes is required")
    check("channel_language", manifest.get("channel") == "wealth_blueprint" and manifest.get("language_code") == "en",
          "Expected Wealth Blueprint in English")
    check("renderer", edit.get("renderer") == "remotion" and edit.get("fallback_used") is False,
          "Expected explicit Remotion execution without fallback")
    video = Path(str(edit.get("output_file") or order_dir / "__missing_video__"))
    if not video.is_absolute():
        video = order_dir / video
    video_hash = digest(video) if video.is_file() else None
    check("final_video", bool(video_hash), str(video))
    if video_hash:
        check("video_hash", edit.get("sha256") == video_hash, "edit.json must bind the current MP4")
        media = documents.get("media_qa.json", {})
        check("media_qa_binding", media.get("status") == "passed" and media.get("video_sha256") == video_hash,
              "Technical QA must refer to the current MP4")
    manifest_hash = initial_hashes.get("project_manifest.json")
    check("semantic_qa_status", semantic.get("status") == "passed", "Semantic QA must pass, with complete scene evidence")
    for orientation in ("portrait", "landscape"):
        cover = Path(str(documents.get("covers.json", {}).get(orientation) or order_dir / "__missing_cover__"))
        verdict = (semantic.get("reports") or {}).get(f"cover_{orientation}", {})
        check("cover_qa", cover.is_file() and verdict.get("status") == "passed" and
              verdict.get("sha256") == (digest(cover) if cover.is_file() else None), orientation)
    style_path = app_root / "runtime-recovered/api/config/visual_profiles/wealth_blueprint_visual_v1.json"
    style_hash = None
    try:
        style = read_json(style_path)
        style_hash = digest(style_path)
        roles = style.get("palette", {}).get("roles", {})
        check("brand_profile", bool(roles) and all(manifest.get("brand", {}).get(k) == v for k, v in roles.items()),
              "Manifest brand must match the active WB profile; this does not establish pixel-level style")
    except (OSError, ValueError) as exc:
        check("brand_profile", False, str(exc))
    if manifest:
        material = {k: v for k, v in manifest.items() if k != "content_hash"}
        expected = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        check("manifest_hash", manifest.get("content_hash") == expected, "Manifest internal content hash")
    script_text = " ".join(str(s.get("text", "")) for s in script.get("segments", []))
    rendered_text = " ".join(str(s.get("narration", "")) for s in scenes)
    check("narration_preserved", bool(script_text) and normalized(script_text) == normalized(rendered_text),
          "Final scene narration must equal the approved script, including numbers")
    words = [w for group in captions.get("groups", []) for w in group if isinstance(w, dict)]
    voiced_words = voice.get("word_timestamps") or []
    actual_words = [w for scene in scenes for w in scene.get("caption_words", [])]
    signature = lambda ws: [(normalized(w.get("text", "")), round(float(w["start_sec"]), 3),
                              round(float(w["end_sec"]), 3)) for w in ws]
    try:
        check("caption_stage_binding", bool(words) and signature(words) == signature(voiced_words) == signature(actual_words),
              "Renderer must consume all approved word timings exactly once")
    except (ValueError, TypeError, KeyError):
        check("caption_stage_binding", False, "Malformed word timings")

    inventories = []
    for path in (catalog_path, sound_catalog_path):
        try:
            inventories.append(library_inventory(path, app_root))
        except (ValueError, OSError) as exc:
            check("catalog_available", False, str(exc))
    library = {r["id"]: r for inv in inventories for r in inv["assets"]}
    claim_rows = script.get("claims") or []
    claims = {c.get("id"): c for c in claim_rows}
    check("claim_ids_unique", len(claims) == len(claim_rows), "Claim IDs must be unique")
    finance_claims = evidence.get("claims") or {}
    if claims:
        check("finance_script_binding", evidence.get("script_sha256") == initial_hashes.get("script.json"),
              "Finance evidence must bind the exact script")
    for cid, claim in claims.items():
        try:
            value = calculated(claim)
            check("calculation", math.isclose(value, float(claim["result"]), rel_tol=1e-9, abs_tol=.005), str(cid))
        except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
            check("calculation", False, f"{cid}: {exc}")
        proof = finance_claims.get(cid, {})
        kind = proof.get("kind")
        linked_text = " ".join(s.get("text", "") for s in script.get("segments", []) if cid in s.get("claim_ids", []))
        if kind == "hypothetical":
            disclosure = str(proof.get("disclosure_text") or "").strip()
            check("hypothetical_disclosure", bool(disclosure) and normalized(disclosure) in normalized(linked_text)
                  and bool(claim.get("assumptions")) and bool(proof.get("reviewed_by")),
                  f"{cid}: explicit spoken scenario and recorded editorial review")
        elif kind == "external":
            check("claim_fact_links", bool(proof.get("fact_ids")), f"{cid}: external inputs need fact references")
        else:
            check("claim_evidence", False, f"{cid}: classify as a reviewed hypothetical or sourced calculation")
    facts = evidence.get("facts") or {}
    needed_facts = set(fid for s in script.get("segments", []) for fid in s.get("fact_ids", []))
    needed_facts.update(fid for p in finance_claims.values() for fid in p.get("fact_ids", []))
    if needed_facts:
        check("finance_script_binding", evidence.get("script_sha256") == initial_hashes.get("script.json"), "External fact binding")
    for fid in sorted(needed_facts):
        fact = facts.get(fid, {})
        url = urlsplit(str(fact.get("source_url") or ""))
        snapshot = (order_dir / str(fact.get("snapshot_path") or "__missing__")).resolve()
        source_ok = snapshot.is_relative_to(order_dir) and snapshot.is_file()
        check("source_snapshot", source_ok and fact.get("snapshot_sha256") == (digest(snapshot) if source_ok else None),
              f"{fid}: source snapshot must exist within the order and match its hash")
        check("source_metadata", url.scheme == "https" and bool(url.netloc) and all(fact.get(k) for k in
              ("statement", "retrieved_at", "as_of", "jurisdiction", "reviewed_by", "verified_at", "valid_until")),
              f"{fid}: dated source, jurisdiction and verification required; URL alone is not verification")
        try:
            until = datetime.fromisoformat(fact.get("valid_until", "").replace("Z", "+00:00"))
            check("source_freshness", until.tzinfo is not None and until >= datetime.now(timezone.utc), str(fid))
        except (ValueError, TypeError):
            check("source_freshness", False, f"{fid}: invalid validity date")

    previous_end = 0.0
    for scene in scenes:
        sid = scene["scene_id"]
        check("layout_transferred", bool(scene.get("layout_id")), "Scene layout must reach Remotion", sid)
        check("motion_transferred", bool(scene.get("animation_presets")), "Animation selection must reach Remotion", sid)
        timing = scene.get("timing") or {}
        start, end = float(timing.get("start_sec", -1)), float(timing.get("end_sec", -1))
        check("scene_timeline", end > start >= 0 and abs(start - previous_end) <= .05,
              f"{start:.3f}–{end:.3f}", sid)
        previous_end = end
        sw = scene.get("caption_words") or []
        check("caption_content", bool(sw) and normalized(" ".join(w.get("text", "") for w in sw)) == normalized(scene.get("narration", "")),
              "Subtitles must cover the complete narration, not only the headline", sid)
        try:
            cursor = start
            valid = True
            for word in sw:
                a, b = float(word["start_sec"]), float(word["end_sec"])
                valid &= math.isfinite(a) and math.isfinite(b) and a >= cursor - .002 and b > a and b <= end + .05
                cursor = b
            check("caption_intervals", bool(sw) and valid, "Word timing within scene", sid)
        except (ValueError, TypeError, KeyError):
            check("caption_intervals", False, "Invalid word interval", sid)
        report = (semantic.get("reports") or {}).get(sid, {})
        check("scene_qa_binding", report.get("status") == "passed" and bool(video_hash) and
              report.get("video_sha256") == video_hash and report.get("manifest_sha256") == manifest_hash,
              "Scene review must bind the final MP4 and current manifest", sid)
        ids = scene.get("claim_ids") or []
        if scene.get("chart_values") or scene.get("number_value") is not None:
            check("quantitative_claims", bool(ids) and all(cid in claims for cid in ids),
                  "Quantitative visuals need existing verified claims", sid)
            allowed = [float(v) for cid in ids if cid in claims for v in
                       [claims[cid]["result"], *claims[cid]["operands"]]]
            vals = scene.get("chart_values") or []
            check("chart_values_binding", all(any(math.isclose(float(v), x, rel_tol=1e-8, abs_tol=.005) for x in allowed) for v in vals),
                  "Chart values must be explicit calculation values", sid)
            if vals:
                check("chart_labels", len(scene.get("chart_labels", [])) == len(vals) and all(scene.get("chart_labels", [])),
                      "Each value requires a label", sid)
        for asset in scene.get("assets") or []:
            if asset.get("role") != "selected_image":
                continue
            path = Path(str(asset.get("path") or "__missing__"))
            row = library.get(asset.get("asset_id"))
            check("production_asset", bool(row and row["eligible_by_metadata"]),
                  f"{asset.get('asset_id')}: {row['reasons'] if row else 'no catalog record'}", sid)
            check("asset_hash", path.is_file() and asset.get("provenance", {}).get("artifact_hash") == (digest(path) if path.is_file() else None),
                  f"{asset.get('asset_id')}: staged asset hash", sid)
            params = asset.get("provenance", {}).get("generation_parameters", {})
            check("asset_role", params.get("production_role") not in {None, "reference_sheet", "selected_image"}
                  and "channel_references" not in path.parts and "placeholders" not in path.parts,
                  "Explicit production role; reference and test assets forbidden", sid)

    mix = documents.get("sound_mix_notes.json", {})
    check("sound_mix", mix.get("executed") is True, "Production sound mix must have executed")
    for clip in documents.get("sound_mix_plan.json", {}).get("clips", []):
        row = library.get(clip.get("asset_id"))
        check("sound_asset", bool(row and row["eligible_by_metadata"]), str(clip.get("asset_id")))
    measurement = None
    if video_hash and ffmpeg and ffprobe:
        try:
            measurement = probe_final(video, ffmpeg, ffprobe)
            audio = [s for s in measurement.get("streams", []) if s.get("codec_type") == "audio"]
            vs = next((s for s in measurement.get("streams", []) if s.get("codec_type") == "video"), {})
            fps_parts = vs.get("avg_frame_rate", "0/1").split("/")
            fps = float(fps_parts[0]) / float(fps_parts[1])
            check("actual_format", bool(audio) and vs.get("width") == 1080 and vs.get("height") == 1920
                  and vs.get("codec_name") == "h264" and abs(fps - 30) < .01, "Actual final MP4 format")
            seconds = float(measurement.get("format", {}).get("duration", 0))
            check("actual_duration", abs(seconds - previous_end) <= .15, "Final duration matches scene timeline")
            loudness = measurement["loudness"]
            lufs, peak = float(loudness["input_i"]), float(loudness["input_tp"])
            check("final_mix_loudness", math.isfinite(lufs) and -18 <= lufs <= -12, f"Final mix: {lufs} LUFS")
            check("final_mix_peak", math.isfinite(peak) and peak <= -1, f"Final mix true peak: {peak} dBTP")
        except (OSError, ValueError, KeyError, ZeroDivisionError, subprocess.SubprocessError) as exc:
            check("actual_media_measurement", False, str(exc)[:300])
    else:
        check("actual_media_measurement", False, "Final video and explicit FFmpeg/ffprobe paths required")

    for name, before in initial_hashes.items():
        path = order_dir / name
        check("stable_input", path.is_file() and digest(path) == before, name)
    if video_hash:
        check("stable_final", video.is_file() and digest(video) == video_hash, "MP4 unchanged during audit")
    for inv in inventories:
        path = Path(inv["catalog"])
        check("stable_catalog", path.is_file() and digest(path) == inv["catalog_sha256"], str(path))
    if style_hash:
        check("stable_style", style_path.is_file() and digest(style_path) == style_hash, "Style unchanged during audit")
    binding = {"documents": initial_hashes, "video_sha256": video_hash,
               "catalogs": {x["catalog"]: x["catalog_sha256"] for x in inventories}, "style_sha256": style_hash}
    binding_hash = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    review = human_review or {}
    reviewer_ok = (bool(review.get("reviewed_by")) and bool(review.get("reviewed_at"))
                   and review.get("evidence_sha256") == binding_hash and review.get("video_sha256") == video_hash
                   and review.get("full_video_watched") is True and review.get("full_audio_listened") is True
                   and review.get("financial_content_reviewed") is True and review.get("verdict") == "approved"
                   and set(review.get("scenes", {})) == set(scene_ids) and bool(scenes)
                   and all(all(row.get(k) == "passed" for k in ("visual", "content", "captions", "audio"))
                           for row in review.get("scenes", {}).values()))
    status = "blocked" if findings else ("accepted" if reviewer_ok else "awaiting_editorial_review")
    return {"schema_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "order_dir": str(order_dir), "job_id": order_dir.name, "status": status,
            "production_accepted": status == "accepted", "automated_passed": not findings,
            "checks": checks, "blockers": findings, "measurement": measurement, "inventory": inventories,
            "evidence": binding, "evidence_sha256": binding_hash, "video_path": str(video),
            "scenes": [{"scene_id": s.get("scene_id"), "timing": s.get("timing"),
                        "narration": s.get("narration"), "layout_id": s.get("layout_id"),
                        "animation_presets": s.get("animation_presets", [])} for s in scenes],
            "editorial_review": {"status": "recorded" if reviewer_ok else "missing_or_incomplete_or_stale",
                                 "note": "Review records are declarations, not automatic proof of listening or aesthetics."}}


def write_report(report: dict, output: Path) -> None:
    """New E: output directory only; never overwrite past evidence or production files."""
    output = output.resolve()
    if output.drive.casefold() != "e:":
        raise ValueError("WB audit outputs must be on E:")
    if output.exists():
        raise ValueError("Choose a new report directory; existing reports are immutable")
    if report.get("order_dir") and output.is_relative_to(Path(report["order_dir"]).resolve()):
        raise ValueError("Audit outputs must be outside the production order")
    output.mkdir(parents=True)
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    template = {"video_sha256": report["evidence"]["video_sha256"], "evidence_sha256": report["evidence_sha256"],
                "reviewed_by": "", "reviewed_at": "", "full_video_watched": False,
                "full_audio_listened": False, "financial_content_reviewed": False, "verdict": "pending",
                "scenes": {s["scene_id"]: {"visual": "pending", "content": "pending", "captions": "pending",
                                           "audio": "pending", "notes": ""} for s in report["scenes"]}}
    (output / "editorial-review.template.json").write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    esc = lambda value: html.escape(str(value), quote=True)
    blocks = "".join(f"<li><b>{esc(r['code'])}</b> {esc(r.get('scene_id') or '')}: {esc(r['detail'])}</li>" for r in report["blockers"])
    rows = "".join(f"<tr><td>{esc(s['scene_id'])}</td><td>{esc(s['timing'])}</td><td>{esc(s['narration'])}</td>"
                   f"<td>{esc(s['layout_id'])}</td></tr>" for s in report["scenes"])
    video_uri = Path(report["video_path"]).resolve().as_uri()
    page = f"""<!doctype html><html lang="pl"><meta charset="utf-8"><title>Odbiór Wealth Blueprint</title>
<style>body{{font:16px system-ui;background:#142633;color:#f0e5d2;max-width:1100px;margin:32px auto;padding:20px}}
video{{max-height:660px;max-width:100%;background:#000}}td,th{{padding:12px;border-bottom:1px solid #426579;text-align:left}}li{{margin:12px 0}}a{{color:#d5ad6c}}</style>
<h1>Odbiór Wealth Blueprint</h1><p>{esc(report['job_id'])} · <b>{esc(report['status'])}</b></p>
<p>Odtwórz cały film z dźwiękiem. Raport automatyczny nie potwierdza obejrzenia ani odsłuchu.
Jeśli przeglądarka blokuje plik lokalny, otwórz MP4 w odtwarzaczu systemowym.</p>
<video controls preload="metadata" src="{esc(video_uri)}"></video><p><a href="{esc(video_uri)}">Otwórz końcowy film</a></p>
<h2>Blokery ({len(report['blockers'])})</h2><ul>{blocks}</ul>
<h2>Sceny do odbioru</h2><table><tr><th>Scena</th><th>Czas</th><th>Narracja</th><th>Układ</th></tr>{rows}</table>
<p>Wypełnij kopię editorial-review.template.json po rzeczywistym odbiorze. Każda zmiana dowodów unieważnia zapisany odbiór.</p></html>"""
    (output / "review.html").write_text(page, encoding="utf-8")
