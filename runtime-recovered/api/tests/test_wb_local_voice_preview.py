from __future__ import annotations

import hashlib
import json
import shutil
import struct
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production.project_profile import content_hash
from atnos.production.order_store import file_hash
from atnos.production.channels.wealth_blueprint import preview
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style


def _put_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _pcm(path: Path, seconds: float = 10.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 8000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(
            struct.pack("<h", 1200 if .1 <= (i / rate) % 2 < .35 or .5 <= (i / rate) % 2 < .8 else 0)
            for i in range(int(rate * seconds))))
    return path


def _fixture(monkeypatch) -> tuple[Path, dict, dict, dict, list[dict]]:
    source = Path(r"E:\Atnos\App\tmp") / f"local-voice-preview-{uuid.uuid4().hex}"
    source.mkdir(parents=True)
    segments = [{"id": f"s{i:02d}", "text": "Move money.", "on_screen": "Move money", "claim_ids": []}
                for i in range(1, 6)]
    script = {"title": "Saving", "segments": segments, "claims": []}
    script_path = _put_json(source / "script.json", script)
    script_hash = content_hash({"segments": segments})
    style = freeze_visual_style("D")
    lane_data = {"job_id": source.name, "digest": "lane-digest-test", "frozen_profile_hash": "profile-hash-test",
                 "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "preset": "ryan-conversational-v2"}
    lane = SimpleNamespace(as_dict=lambda: lane_data, digest=lane_data["digest"])
    _put_json(source / "voice" / "local_voice_lane.json", {"lane": lane_data})
    monkeypatch.setattr("atnos.production.local_voice_lane.load_local_voice_lane", lambda *_, **__: lane)
    request = {"project": "wealth_blueprint", "delivery_mode": "local_only", "visual_style_id": "D",
               "topic": "Saving", "direction": "A measured finance film", "optional_notes": ""}
    profile = {"visual_style": style}

    raw_path = _pcm(source / "audio" / "narration" / "qwen_source.wav")
    raw_sha = file_hash(raw_path)
    final_path = _pcm(source / "audio" / "narration_final.wav")
    final_sha = file_hash(final_path)
    timestamps, words = [], []
    for index, segment in enumerate(segments):
        start, end = float(index * 2), float((index + 1) * 2)
        timestamps.append({"segment_id": segment["id"], "text": segment["text"], "start_sec": start,
                           "end_sec": end, "duration_sec": end-start, "speech_end_sec": end-.2,
                           "timing_source": "local_ctc_reference_alignment", "single_pass": True})
        words.extend([
            {"segment_id": segment["id"], "text": "Move", "start_sec": start+.1, "end_sec": start+.35},
            {"segment_id": segment["id"], "text": "money.", "start_sec": start+.5, "end_sec": start+.8},
        ])
    synthesis = {"output_file": str(raw_path), "sha256": raw_sha, "duration_seconds": 10.0,
                 "lane_digest": lane.digest, "timing_source": "local_ctc_reference_alignment"}
    _put_json(raw_path.with_suffix(".json"), synthesis)
    qa = {"status": "passed", "scope": "full_narration", "expected_prompt_provided": False,
          "recognized_text": "Move money. " * 5, "sequence_ratio": 1.0}
    qa_path = source / "audio" / "narration" / f"local_ryan_recognized_{script_hash[:12]}.srt.json"
    _put_json(qa_path, qa)
    qa_path.with_suffix("").write_text("1\n00:00:00,000 --> 00:00:01,000\nMove money.\n", encoding="utf-8")
    alignment = {"timestamps": timestamps, "word_timestamps": words, "duration_seconds": 10.0,
                 "timing_source": "local_ctc_reference_alignment", "audio_sha256": raw_sha,
                 "script_hash": script_hash, "alignment": {"provenance": {"model": "ctc-test"}}}
    alignment_path = source / "audio" / "narration" / f"local_ctc_alignment_{script_hash[:12]}.json"
    _put_json(alignment_path, alignment)
    voice = {"output_file": str(final_path), "sha256": final_sha, "timestamps": timestamps,
             "word_timestamps": words, "duration_seconds": 10.0, "production_ready": True,
             "timing_source": "local_ctc_reference_alignment", "qa": {"status": "passed", "segments": [qa]},
             "source_recording": {"output_file": str(raw_path), "sha256": raw_sha}}
    voice_path = _put_json(source / "audio_timestamps.json", voice)

    def entry(path: Path) -> dict:
        return {"path": path.relative_to(source).as_posix(), "sha256": file_hash(path)}

    def checkpoint(stage: str, inputs: dict | None, output: dict, files: list[Path]) -> dict:
        return {"stage": stage, "status": "done", "input_hash": content_hash(inputs) if inputs is not None else "script-input",
                "output": output, "files": [entry(path) for path in files]}

    synthesis_inputs = {"lane": lane.as_dict(), "script_hash": script_hash,
                        "text": "\n\n".join(s["text"].strip() for s in segments), "synthesis_contract": 1}
    qa_inputs = {"sha256": raw_sha, "script_hash": script_hash, "validator": 3,
                 "scope": "job_scoped_local_voice"}
    alignment_inputs = {"audio_sha256": raw_sha, "script_hash": script_hash,
                        "aligner": "wav2vec2-asr-base-960h", "contract": 1}
    order = {"artifact_dir": str(source), "request": request, "profile": profile,
             "checkpoints": [
                 checkpoint("script", None, script, [script_path]),
                 checkpoint("voice/local_ryan/narration", synthesis_inputs, synthesis,
                            [raw_path, raw_path.with_suffix(".json")]),
                 checkpoint("voice/local_ryan_qa/narration", qa_inputs, qa,
                            [qa_path, qa_path.with_suffix("")]),
                 checkpoint("voice/local_ryan/alignment", alignment_inputs, alignment, [alignment_path]),
                 checkpoint("voice", None, voice, [voice_path, final_path]),
             ]}
    monkeypatch.setattr(preview, "_source_order", lambda *_: order)
    return source, order, voice, lane_data, segments


def test_local_voice_preview_rebases_ctc_scene_word_and_cue_times(monkeypatch):
    source, _, voice, _, _ = _fixture(monkeypatch)
    try:
        captured = []

        class Planner:
            def __init__(self, settings):
                pass

            def plan(self, inputs):
                captured.append(inputs)
                scenes = []
                for segment in inputs.segments:
                    start, end = float(segment.start), float(segment.end)
                    data = {"scene_id": segment.id, "narration_segment_ids": [segment.id],
                            "start_sec": start, "end_sec": end,
                            "layout_id": "fullscreen_statement", "visual_focus": "statement",
                            "narration_excerpt": segment.text, "on_screen_text": "Move money",
                            "animations": [{"at_sec": start + .1}], "sound_events": [{"at_sec": start + .2}]}
                    scenes.append(SimpleNamespace(scene_id=segment.id, narration_segment_ids=[segment.id],
                        model_dump=lambda mode, data=data: data))
                return SimpleNamespace(scenes=scenes)

        monkeypatch.setattr(preview, "WealthBlueprintScenePlanner", Planner)
        _, parts, plan, source_start = preview.preview_plan(source, ["s03", "s04", "s05"], settings=object())
        assert captured[0].design_system["visual_style_id"] == "D"
        assert parts["_local_single_pass"]["voice"] == voice
        assert source_start == pytest.approx(4.0)
        assert [scene["start_sec"] for scene in plan["scenes"]] == pytest.approx([0, 2, 4])
        assert [scene["animations"][0]["at_sec"] for scene in plan["scenes"]] == pytest.approx([.1, 2.1, 4.1])
        assert [word["text"] for word in voice["word_timestamps"] if word["segment_id"] == "s03"] == ["Move", "money."]
    finally:
        shutil.rmtree(source, ignore_errors=True)


def test_local_voice_preview_crops_contiguous_pcm_and_uses_frozen_style_manifest(monkeypatch):
    source, _, voice, lane_data, _ = _fixture(monkeypatch)
    output = source.parent / f"{source.name}-review"
    try:
        class Planner:
            def __init__(self, settings):
                pass

            def plan(self, inputs):
                return SimpleNamespace(scenes=[
                    SimpleNamespace(scene_id=s.id, narration_segment_ids=[s.id],
                        model_dump=lambda mode, s=s: {"scene_id": s.id, "narration_segment_ids": [s.id],
                            "start_sec": float(s.start), "end_sec": float(s.end),
                            "layout_id": "fullscreen_statement", "visual_focus": "statement",
                            "narration_excerpt": s.text, "on_screen_text": "Move money",
                            "animations": [{"at_sec": float(s.start)+.1}], "sound_events": []})
                    for s in inputs.segments])

        monkeypatch.setattr(preview, "WealthBlueprintScenePlanner", Planner)
        monkeypatch.setattr("atnos.production.channels.wealth_blueprint.visual_decision.storyboard_blocks_render", lambda plan: None)
        captured = {}
        def resolve(**kwargs):
            captured["asset_style"] = kwargs["visual_style"]
            return SimpleNamespace(status="ok", assignments={}, as_dict=lambda: {"status": "ok", "assignments": {}})
        monkeypatch.setattr("atnos.production.channels.wealth_blueprint.scene_production_assets.resolve_scene_production_assets", resolve)
        real_manifest_adapter = preview.scene_plan_to_project_manifest
        def manifest(**kwargs):
            captured["manifest_voice"] = kwargs["voice"]
            captured["manifest_style"] = kwargs["visual_style"]
            return real_manifest_adapter(**kwargs)
        monkeypatch.setattr(preview, "scene_plan_to_project_manifest", manifest)
        def render(_manifest_path, out_path):
            out_path.write_bytes(b"silent preview")
            return {"ok": True, "output_path": str(out_path)}
        monkeypatch.setattr(preview, "render_project_manifest", render)
        def mux(*, raw_video_path, audio_path, output_path, target_lufs):
            output_path.write_bytes(b"review candidate mp4")
            return {"ok": True}
        monkeypatch.setattr(preview, "_mux_remotion_video", mux)

        result = preview.render_review_fragment(source, output, selected_ids=["s03", "s04", "s05"], settings=object())
        with wave.open(str(output / "audio" / "narration_preview.wav"), "rb") as excerpt:
            assert excerpt.getnframes() / excerpt.getframerate() == pytest.approx(6.2)
            assert excerpt.getnframes() == 6.2 * excerpt.getframerate()
            excerpt_frames = excerpt.readframes(excerpt.getnframes())
        with wave.open(str(voice["output_file"]), "rb") as original:
            original.setpos(round(3.8 * original.getframerate()))
            assert excerpt_frames == original.readframes(round(6.2 * original.getframerate()))
        assert captured["asset_style"]["id"] == captured["manifest_style"]["id"] == "D"
        assert captured["manifest_voice"]["timing_source"] == "local_ctc_reference_alignment"
        assert captured["manifest_voice"]["word_timestamps"][0]["start_sec"] == pytest.approx(.3)
        assert result["snapshot_only"] is True
        assert result["user_approved"] is result["publication_allowed"] is False
        assert result["local_voice_lane_digest"] == lane_data["digest"]
        assert result["source_start_seconds"] == pytest.approx(4.0)
        assert result["source_end_seconds"] == pytest.approx(10.0)
        assert result["audio_excerpt_start_seconds"] == pytest.approx(3.8)
        assert result["audio_excerpt_end_seconds"] == pytest.approx(10.0)
        assert (source / "audio_timestamps.json").is_file()
        assert not (source / "WB-review.mp4").exists()
    finally:
        shutil.rmtree(source, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)


def test_local_voice_preview_rejects_changed_asr_receipt_before_writing_preview(monkeypatch):
    source, _, _, _, _ = _fixture(monkeypatch)
    output = source.parent / f"{source.name}-review"
    try:
        script = json.loads((source / "script.json").read_text(encoding="utf-8"))
        script_hash = content_hash({"segments": script["segments"]})
        qa_path = source / "audio" / "narration" / f"local_ryan_recognized_{script_hash[:12]}.srt.json"
        qa_path.write_text('{"status":"passed","recognized_text":"tampered"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="changed voice/local_ryan_qa/narration evidence"):
            preview.render_review_fragment(source, output, selected_ids=["s03", "s04", "s05"], settings=object())
        assert not output.exists()
    finally:
        shutil.rmtree(source, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)


def test_completed_preview_uses_verified_checkpoint_paths_instead_of_guessed_basenames(monkeypatch):
    source, order, _, _, _ = _fixture(monkeypatch)
    try:
        script=json.loads((source/'script.json').read_text(encoding='utf-8'))
        stages={row['stage']:row for row in order['checkpoints']}
        for stage in ('voice/local_ryan_qa/narration','voice/local_ryan/alignment'):
            row=stages[stage]
            entry=next(item for item in row['files'] if str(item['path']).endswith('.json'))
            old=source/entry['path']
            renamed=old.with_name('current-'+old.name)
            old.rename(renamed)
            entry['path']=str(renamed.relative_to(source))
        snapshot=preview._local_voice_snapshot(source,order,script,object())
        assert snapshot['voice']['production_ready'] is True
        assert snapshot['qa_sha256']
        assert snapshot['alignment_sha256']
    finally:
        shutil.rmtree(source,ignore_errors=True)


def test_local_preview_keeps_physical_handoffs_and_claim_chart_under_frozen_style_d(monkeypatch):
    from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type

    source = Path(r"E:\Atnos\App\tmp") / f"local-voice-intent-{uuid.uuid4().hex}"
    source.mkdir(parents=True)
    try:
        segments = [
            {"id": "bill-bag", "text": "A purchase has an opportunity cost.",
             "on_screen": "$10 is not just $10", "visual_intent": "A $10 bill sits beside a small shopping bag.",
             "claim_ids": []},
            {"id": "bill-account", "text": "Compare spending with investing.",
             "on_screen": "Today or future?", "visual_intent": "A $10 bill sits beside an investment account on a phone screen.",
             "claim_ids": []},
            {"id": "growth", "text": "At five percent for two years, one hundred dollars grows to one hundred ten dollars and twenty-five cents.",
             "on_screen": "$100 to $110.25", "visual_intent": "A simple labeled compound growth chart rises from one hundred to one hundred ten dollars.",
             "claim_ids": ["c_compound_demo"]},
            {"id": "close", "text": "Keep your long-term plan in view.",
             "on_screen": "Stay with the plan", "visual_intent": "A simple closed notebook rests beside a pen.",
             "claim_ids": []},
        ]
        claim = {"id": "c_compound_demo", "operation": "compound", "operands": [100.0, 0.05, 2.0],
                 "result": 110.25, "unit": "dollars",
                 "spoken_result": "one hundred ten dollars and twenty five cents",
                 "assumptions": "Hypothetical five percent annual growth for two years only."}
        _put_json(source / "script.json", {"title": "Opportunity cost", "segments": segments, "claims": [claim]})
        style = freeze_visual_style("D")
        order = {"artifact_dir": str(source),
                 "request": {"project": "wealth_blueprint", "topic": "Opportunity cost",
                             "direction": "A measured finance film", "visual_style_id": "D"},
                 "profile": {"visual_style": style}}
        monkeypatch.setattr(preview, "_source_order", lambda *_: order)
        duration_by_id = {"bill-bag": 4.0, "bill-account": 4.0, "growth": 8.0, "close": 4.0}
        cursor = 0.0
        timings = []
        words = []
        for segment in segments:
            start, end = cursor, cursor + duration_by_id[segment["id"]]
            timings.append({"segment_id": segment["id"], "text": segment["text"],
                            "start_sec": start, "end_sec": end, "speech_end_sec": end-.2})
            count = len(segment["text"].split())
            for index, word in enumerate(segment["text"].split()):
                word_start = start + .1 + index * (duration_by_id[segment["id"]] - .3) / count
                words.append({"segment_id": segment["id"], "text": word,
                              "start_sec": word_start, "end_sec": word_start + .05})
            cursor = end
        snapshot = {"mode": "local_single_pass", "style": style,
                    "voice": {"timestamps": timings, "word_timestamps": words}}
        monkeypatch.setattr(preview, "_local_voice_snapshot", lambda *args: snapshot)

        original_planner = preview.WealthBlueprintScenePlanner
        captured = []
        class CapturingPlanner:
            def __init__(self, settings):
                self.delegate = original_planner(settings)

            def plan(self, inputs):
                captured.append(inputs)
                return self.delegate.plan(inputs)

        monkeypatch.setattr(preview, "WealthBlueprintScenePlanner", CapturingPlanner)
        _, _, plan, _ = preview.preview_plan(source, ["bill-bag", "bill-account", "growth"], settings=object())
        assert captured[0].profile["disable_visual_story"] is True
        assert captured[0].design_system["visual_style_id"] == "D"
        assert [(word.word, word.start, word.end) for word in captured[0].words] == [
            (word["text"], word["start_sec"], word["end_sec"]) for word in words
        ]
        scenes = plan["scenes"]
        assert len(scenes) == 3
        assert [scene["narration_segment_ids"] for scene in scenes] == [["bill-bag"], ["bill-account"], ["growth"]]
        assert scenes[0]["source_visual_intents"] == [segments[0]["visual_intent"]]
        assert scenes[1]["source_visual_intents"] == [segments[1]["visual_intent"]]
        assert scenes[0]["meaning_contract"]["illustration_function"] == "bill_and_bag"
        assert scenes[1]["meaning_contract"]["illustration_function"] == "bill_and_account_ui"
        for index, scene in enumerate(scenes[:2]):
            assert layout_to_scene_type(scene["layout_id"], visual_focus=scene["visual_focus"], index=index,
                total=len(scenes), has_number=bool(scene.get("number_value")), has_chart=bool(scene.get("chart"))).value == "ImageScene"
        assert scenes[2]["chart"]["claim_ids"] == ["c_compound_demo"]
        assert layout_to_scene_type(scenes[2]["layout_id"], visual_focus=scenes[2]["visual_focus"], index=2,
            total=len(scenes), has_number=bool(scenes[2].get("number_value")), has_chart=True).value == "ChartScene"
        assert all(scene["visual_story"] is None for scene in scenes)
    finally:
        shutil.rmtree(source, ignore_errors=True)
