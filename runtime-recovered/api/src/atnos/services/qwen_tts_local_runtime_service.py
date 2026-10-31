"""Isolated adapter for the locally pinned Qwen3-TTS model.

This service deliberately does not consult or mutate Atnos Settings, channel
profiles, or default voices. Inference runs in its dedicated E: Python 3.12
CPU environment with network access to model hubs disabled.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import wave


RUNTIME_ROOT = Path(r"E:\Atnos\Tools\Qwen3-TTS-runtime")
RUNTIME_PYTHON = RUNTIME_ROOT / "venv" / "Scripts" / "python.exe"
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
MODEL_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
EXPECTED_MODEL_FILE_COUNT = 12
MODEL_DIR = RUNTIME_ROOT / "models" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"
PROVENANCE_PATH = MODEL_DIR / "model-provenance.json"
PREVIEW_ROOT = Path(r"E:\Atnos\App\reports\wb-10-dollar-pilot-20261005\local-voice\qwen")
EXPECTED_TEXT = (
    "Every dollar you spend has two prices: what it costs today, and what it could become. "
    "Imagine a one-time ten-dollar deposit growing at an assumed fixed eight percent a year, "
    "compounded annually for thirty years. With no additional deposits, it could become about "
    "one hundred one dollars before fees, taxes, and inflation. That is an illustration, not a promise. "
    "The point is to see the trade."
)
RYAN_MEASURED_PROFESSIONAL_V1 = (
    "Speak in a calm, measured, competent professional narrator voice. Keep the delivery "
    "natural and conversational, with restrained emphasis and steady pacing. Avoid theatrical, "
    "promotional, radio-announcer, or grandiose delivery."
)
RYAN_CONVERSATIONAL_V2 = (
    "Speak in a clear, relaxed, conversational way, as if explaining an idea to one person. "
    "Keep the delivery smooth and natural, with warm, understated confidence. Give gentle emphasis "
    "to the important numbers—ten dollars, eight percent, thirty years, and one hundred one dollars—"
    "without sounding formal, ceremonial, promotional, or like a radio announcer."
)
ALLOWED_PRESETS = {
    "ryan-measured-professional-v1": RYAN_MEASURED_PROFESSIONAL_V1,
    "ryan-conversational-v2": RYAN_CONVERSATIONAL_V2,
}
EXPECTED_INSTRUCTION = RYAN_MEASURED_PROFESSIONAL_V1
COMPARISON_ROOT = Path(r"E:\Atnos\App\reports\wb-batch-20261013\voice-comparison")
COMPARISON_HOLD_PATH = COMPARISON_ROOT.parent / "quality-hold.json"
COMPARISON_SCRIPT_ROOT = Path(r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint")
COMPARISON_SOURCE_JOB_ID = "prod-ef3550d6b7e6"
AUDITION_REPORT_ROOT = Path(r"E:\Atnos\App\reports")
AUDITION_TIMEOUT_SECONDS = 600
# The same file is also an isolated stdlib entry point with no repository on
# PYTHONPATH. Keep this frozen literal here; the binding regression compares it
# with the ordinary production lane before authorizing synthesis.
COMPARISON_CLEAR_INSTRUCTION = (
    "Clear, alert, conversational English. Crisp articulation, a normal steady pace, and natural "
    "sentence stress. Explain the idea to one person."
)
COMPARISON_SAMPLES = {
    "ryan_neutral": ("Ryan", "ryan-conversational-v2-neutral"),
    "ryan_clear": ("Ryan", "clear_conversation_v1"),
    "aiden_clear": ("Aiden", "clear_conversation_v1"),
}


class QwenTTSLocalRuntimeError(RuntimeError):
    """Raised when the preview-only Qwen local runtime cannot complete safely."""


class QwenTTSLocalRuntimeService:
    """Run explicitly scoped local voice work in the pinned child process."""

    def __init__(self, *, timeout_seconds: int = 1800, max_threads: int = 4) -> None:
        if not 60 <= timeout_seconds <= 3600:
            raise ValueError("Qwen preview timeout must be between 60 and 3600 seconds.")
        if not 1 <= max_threads <= 4:
            raise ValueError("Qwen preview CPU threads are limited to 1-4.")
        self.timeout_seconds = timeout_seconds
        self.max_threads = max_threads

    async def synthesize_preview(
        self,
        *,
        text: str,
        output_path: Path,
        speaker: str = "Ryan",
        language: str = "English",
        instruction: str | None = None,
        preset: str = "ryan-measured-professional-v1",
        preview: bool = False,
    ) -> dict[str, Any]:
        """Generate only the exact requested comparison sample; never production audio."""
        if preview is not True:
            raise QwenTTSLocalRuntimeError("Qwen synthesis is gated to an explicit comparison preview.")
        allowed_instruction = ALLOWED_PRESETS.get(preset)
        if (allowed_instruction is None
                or (instruction is not None and instruction != allowed_instruction)):
            raise QwenTTSLocalRuntimeError("Only the named, whitelisted Ryan preview presets are allowed.")
        instruction = allowed_instruction
        if text != EXPECTED_TEXT or speaker != "Ryan" or language != "English":
            raise QwenTTSLocalRuntimeError("This local service only permits the pinned WB voice-comparison preview.")
        if not RUNTIME_PYTHON.is_file() or not MODEL_DIR.is_dir() or not PROVENANCE_PATH.is_file():
            raise QwenTTSLocalRuntimeError("The pinned local Qwen runtime or provenance record is missing.")
        try:
            provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QwenTTSLocalRuntimeError("The local model provenance record is unreadable.") from exc
        if (provenance.get("model_id") != MODEL_ID
                or provenance.get("revision") != MODEL_REVISION
                or provenance.get("license") != "apache-2.0"
                or provenance.get("verified_files") is not True):
            raise QwenTTSLocalRuntimeError("The local model does not match its verified pin and provenance.")

        output = output_path.resolve()
        if output.drive.casefold() != "e:":
            raise QwenTTSLocalRuntimeError("All local Qwen outputs must remain on E:.")
        try:
            output.relative_to(PREVIEW_ROOT.resolve())
        except ValueError as exc:
            raise QwenTTSLocalRuntimeError("Preview output must stay under the E: local-voice/qwen report folder.") from exc
        if output.suffix.casefold() != ".wav" or output.exists():
            raise QwenTTSLocalRuntimeError("Preview output must be a new WAV path; existing files are never overwritten.")
        output.parent.mkdir(parents=True, exist_ok=True)

        environment = _isolated_environment(self.max_threads)
        request = {
            "model_dir": str(MODEL_DIR),
            "output_path": str(output),
            "text": text,
            "speaker": speaker,
            "language": language,
            "instruction": instruction,
            "max_threads": self.max_threads,
        }
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [str(RUNTIME_PYTHON), str(Path(__file__).resolve()), "--worker"],
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
                check=False,
                env=environment,
                cwd=str(RUNTIME_ROOT),
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise QwenTTSLocalRuntimeError("Local Qwen preview exceeded its configured timeout.") from exc
        except OSError as exc:
            raise QwenTTSLocalRuntimeError(f"Could not start the isolated Qwen runtime: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "worker returned no diagnostic").strip()[-1200:]
            raise QwenTTSLocalRuntimeError(f"Isolated Qwen preview failed (exit {result.returncode}): {detail}")
        payload = None
        for line in reversed(result.stdout.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("model_id") == MODEL_ID:
                payload = candidate
                break
        if payload is None:
            raise QwenTTSLocalRuntimeError("Qwen worker returned an invalid result record.")
        if payload.get("device") != "cpu" or payload.get("output_path") != str(output):
            raise QwenTTSLocalRuntimeError("Qwen worker result does not confirm the required CPU preview output.")
        return payload

    async def synthesize_comparison(
        self, *, request_path: Path, request_sha256: str, sample_id: str, output_path: Path,
        settings=None,
    ) -> dict[str, Any]:
        """Record one unapproved voice candidate from a pinned, on-disk batch request."""
        from atnos.production.local_voice_lane import NEUTRAL_INSTRUCTION, NEUTRAL_PRESET

        request_file = Path(request_path).resolve()
        comparison_root = COMPARISON_ROOT.resolve()
        output = Path(output_path).resolve()
        if (request_file.name != "request.json" or request_file.drive.casefold() != "e:"
                or output.drive.casefold() != "e:"):
            raise QwenTTSLocalRuntimeError("Comparison request and output must stay on E:.")
        if not isinstance(sample_id, str) or sample_id not in COMPARISON_SAMPLES:
            raise QwenTTSLocalRuntimeError("Unsupported comparison sample ID.")
        if output != request_file.parent / "samples" / f"{sample_id}.wav":
            raise QwenTTSLocalRuntimeError("Comparison WAV must use its new, sample-scoped report path.")
        if not isinstance(request_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", request_sha256) is None:
            raise QwenTTSLocalRuntimeError("Comparison request SHA-256 is invalid.")

        request_bytes = _comparison_file_bytes(request_file)
        if hashlib.sha256(request_bytes).hexdigest() != request_sha256:
            raise QwenTTSLocalRuntimeError("Persisted comparison request SHA-256 changed.")
        request = _comparison_json(request_bytes, "request")
        version = request.get("schema_version")
        if (type(version) is not int or version not in (1, 2)
                or request.get("status") != "candidate_unapproved"
                or request.get("user_approved") is not False
                or request.get("production_enabled") is not False
                or type(request.get("cost_usd")) is not int or request["cost_usd"] != 0
                or request.get("model_id") != MODEL_ID or request.get("model_revision") != MODEL_REVISION
                or request.get("language") != "English" or "instruction" in request):
            raise QwenTTSLocalRuntimeError("Comparison request is not an unapproved, pinned local candidate.")

        job_id = request.get("source_job_id")
        if version == 1:
            if request_file != comparison_root / "request.json":
                raise QwenTTSLocalRuntimeError("Legacy comparison must stay in the fixed E: batch report folder.")
            hold_file = COMPARISON_HOLD_PATH.resolve()
            hold_bytes = _comparison_file_bytes(hold_file)
            if (request.get("quality_hold_path") != str(hold_file)
                    or request.get("quality_hold_sha256") != hashlib.sha256(hold_bytes).hexdigest()):
                raise QwenTTSLocalRuntimeError("Comparison request does not match the current quality hold.")
            hold = _comparison_json(hold_bytes, "quality hold")
            if (hold.get("batch_id") != "wb-batch-20261013"
                    or hold.get("status") != "USER_REJECTED_APPEARANCE_AND_VOICE"
                    or hold.get("hold_new_dispatch_and_delivery") is not True
                    or request.get("user_feedback") != hold.get("user_feedback")
                    or not isinstance(hold.get("user_feedback"), str)
                    or not hold["user_feedback"].strip()):
                raise QwenTTSLocalRuntimeError("Comparison requires the unchanged user quality hold and feedback.")
            if (job_id != COMPARISON_SOURCE_JOB_ID
                    or not isinstance(hold.get("affected_delivered_jobs"), list)
                    or job_id not in hold["affected_delivered_jobs"]):
                raise QwenTTSLocalRuntimeError("Comparison source must be the held Atnos job2 script.")
            script_file = (COMPARISON_SCRIPT_ROOT / job_id / "script.json").resolve()
            authority = {"quality_hold_sha256": request["quality_hold_sha256"]}
        else:
            _validate_order_audition(request, request_file, settings)
            if self.timeout_seconds != request["timeout_seconds"] or self.max_threads != request["max_cpu_threads"]:
                raise QwenTTSLocalRuntimeError("Order audition runtime differs from its bounded execution scope.")
            script_file = Path(request["source_script_path"])
            authority = {"schema_version": 2, "scope": "voice_audition_only",
                         "source_snapshot": request["source_snapshot"],
                         "execution_authorization": request["execution_authorization"],
                         "preserve_current_mastering": True}
        script_bytes = _comparison_file_bytes(script_file)
        if (script_file.drive.casefold() != "e:"
                or request.get("source_script_path") != str(script_file)
                or request.get("source_script_sha256") != hashlib.sha256(script_bytes).hexdigest()):
            raise QwenTTSLocalRuntimeError("Comparison source script path or SHA-256 changed.")
        script = _comparison_json(script_bytes, "source script")
        segments = script.get("segments")
        selected_ids = request.get("segment_ids")
        if (not isinstance(segments, list) or not isinstance(selected_ids, list)
                or not selected_ids or len(selected_ids) != len(set(map(str, selected_ids)))
                or any(not isinstance(item, str) or not item for item in selected_ids)):
            raise QwenTTSLocalRuntimeError("Comparison requires unique source segment IDs.")
        selected = [item for item in segments if isinstance(item, dict) and item.get("id") in selected_ids]
        if ([item.get("id") for item in selected] != selected_ids
                or any(not isinstance(item.get("text"), str) or not item["text"].strip() for item in selected)):
            raise QwenTTSLocalRuntimeError("Comparison text must come from ordered Atnos script segments.")
        text = ("\n\n" if version == 2 else " ").join(item["text"].strip() for item in selected)
        if (request.get("text") != text or len(text) > 700 or len(text.split()) > 80
                or any(ord(char) < 32 and not (version == 2 and char == "\n") for char in text)):
            raise QwenTTSLocalRuntimeError("Comparison text must exactly match at most 80 words and 700 script characters.")

        samples = request.get("samples")
        if (not isinstance(samples, list) or not 1 <= len(samples) <= 3
                or any(not isinstance(item, dict) or not isinstance(item.get("sample_id"), str)
                       for item in samples)
                or len({item["sample_id"] for item in samples}) != len(samples)):
            raise QwenTTSLocalRuntimeError("Comparison request must contain one to three unique candidate records.")
        for item in samples:
            identity = item.get("sample_id")
            if (identity not in COMPARISON_SAMPLES
                    or (item.get("speaker"), item.get("preset")) != COMPARISON_SAMPLES[identity]
                    or "instruction" in item):
                raise QwenTTSLocalRuntimeError("Comparison candidate speaker or preset is not allowlisted.")
        candidate = next((item for item in samples if item["sample_id"] == sample_id), None)
        if candidate is None:
            raise QwenTTSLocalRuntimeError("Sample ID is absent from the persisted comparison request.")
        if NEUTRAL_PRESET != COMPARISON_SAMPLES["ryan_neutral"][1]:
            raise QwenTTSLocalRuntimeError("The existing neutral Ryan preset has changed.")
        instruction = NEUTRAL_INSTRUCTION if sample_id == "ryan_neutral" else COMPARISON_CLEAR_INSTRUCTION

        if not RUNTIME_PYTHON.is_file() or not MODEL_DIR.is_dir() or not _verified_model_files():
            raise QwenTTSLocalRuntimeError("The pinned local Qwen runtime or model provenance is unavailable.")
        provenance_sha256 = _comparison_sha256(PROVENANCE_PATH)
        model_config_path = MODEL_DIR / "config.json"
        config_bytes = _comparison_file_bytes(model_config_path)
        config = _comparison_json(config_bytes, "model config")
        talker = config.get("talker_config") or {}
        if not isinstance(talker, dict):
            raise QwenTTSLocalRuntimeError("Pinned Qwen speaker config is invalid.")
        speakers = talker.get("spk_id") or {}
        dialects = talker.get("spk_is_dialect") or {}
        if (not isinstance(speakers, dict) or not isinstance(dialects, dict)
                or speakers.get("ryan") != 3061 or speakers.get("aiden") != 2861
                or dialects.get("ryan") is not False or dialects.get("aiden") is not False):
            raise QwenTTSLocalRuntimeError("The pinned Qwen config does not support both neutral English speakers.")

        receipt_path = output.with_suffix(".json")
        if output.exists() or receipt_path.exists():
            raise QwenTTSLocalRuntimeError("Comparison sample already exists; WAV and receipt are never replaced.")
        output.parent.mkdir(parents=True, exist_ok=True)
        reservation = {"status": "running", "request_sha256": request_sha256, "sample_id": sample_id,
                       "output_path": str(output), "candidate_status": "candidate_unapproved"}
        try:
            with receipt_path.open("x", encoding="utf-8") as receipt:
                json.dump(reservation, receipt, ensure_ascii=False, indent=2)
                receipt.write("\n")
        except FileExistsError as exc:
            raise QwenTTSLocalRuntimeError("Comparison sample receipt already exists.") from exc
        try:
            if output.exists():
                raise QwenTTSLocalRuntimeError("Comparison WAV appeared after reservation; refusing to replace it.")
            if _comparison_sha256(request_file) != request_sha256:
                raise QwenTTSLocalRuntimeError("Persisted comparison request changed before dispatch.")
            if version == 2:
                _validate_order_audition(request, request_file, settings)
            elif _comparison_sha256(hold_file) != request["quality_hold_sha256"]:
                raise QwenTTSLocalRuntimeError("Comparison evidence changed before dispatch.")
            if _comparison_sha256(script_file) != request["source_script_sha256"]:
                raise QwenTTSLocalRuntimeError("Comparison source script changed before dispatch.")
            worker_request = {"model_dir": str(MODEL_DIR), "output_path": str(output), "text": text,
                              "speaker": candidate["speaker"], "language": "English", "instruction": instruction,
                              "max_threads": self.max_threads}
            result = await asyncio.to_thread(
                _run_qwen_child, [str(RUNTIME_PYTHON), str(Path(__file__).resolve()), "--worker"],
                json.dumps(worker_request, ensure_ascii=False), _isolated_environment(self.max_threads),
                str(RUNTIME_ROOT), self.timeout_seconds,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "worker returned no diagnostic").strip()[-1200:]
                raise QwenTTSLocalRuntimeError(f"Isolated Qwen comparison failed (exit {result.returncode}): {detail}")
            payload = next((item for line in reversed(result.stdout.splitlines())
                            if isinstance((item := _json_object(line)), dict)), None)
            if (payload is None or payload.get("model_id") != MODEL_ID
                    or payload.get("revision") != MODEL_REVISION or payload.get("device") != "cpu"
                    or payload.get("output_path") != str(output)
                    or payload.get("speaker") != candidate["speaker"]
                    or payload.get("language") != "English" or payload.get("instruction") != instruction
                    or payload.get("torch_cuda_available") is not False):
                raise QwenTTSLocalRuntimeError("Qwen worker did not confirm the pinned CPU comparison output.")
            try:
                with wave.open(str(output), "rb") as wav:
                    if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                            or wav.getframerate() <= 0 or wav.getnframes() <= 0):
                        raise QwenTTSLocalRuntimeError("Comparison WAV has invalid PCM audio metadata.")
                    duration = wav.getnframes() / wav.getframerate()
                    if (type(payload.get("sample_rate_hz")) is not int
                            or payload["sample_rate_hz"] != wav.getframerate()
                            or type(payload.get("channels")) is not int
                            or payload["channels"] != wav.getnchannels()
                            or type(payload.get("duration_seconds")) not in (int, float)
                            or abs(payload["duration_seconds"] - duration) > 0.01):
                        raise QwenTTSLocalRuntimeError("Comparison WAV does not match worker audio metadata.")
            except (OSError, EOFError, wave.Error) as exc:
                raise QwenTTSLocalRuntimeError("Qwen worker did not produce a readable PCM WAV.") from exc
            if version == 2:
                _validate_order_audition(request, request_file, settings)
            elif _comparison_sha256(hold_file) != request["quality_hold_sha256"]:
                raise QwenTTSLocalRuntimeError("Comparison evidence changed during synthesis.")
            if (_comparison_sha256(request_file) != request_sha256
                    or _comparison_sha256(script_file) != request["source_script_sha256"]
                    or _comparison_sha256(PROVENANCE_PATH) != provenance_sha256
                    or _comparison_sha256(model_config_path) != hashlib.sha256(config_bytes).hexdigest()):
                raise QwenTTSLocalRuntimeError("Comparison evidence changed during synthesis.")
            receipt = {"status": "candidate_unapproved", "user_approved": False, "production_enabled": False,
                       "provider": "qwen_local", "cost_usd": 0, "provider_calls": 1,
                       "request_path": str(request_file), "request_sha256": request_sha256,
                       **authority,
                       "source_job_id": job_id, "source_script_sha256": request["source_script_sha256"],
                       "segment_ids": selected_ids, "text": text,
                       "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                       "sample_id": sample_id, "speaker": candidate["speaker"], "preset": candidate["preset"],
                       "instruction": instruction,
                       "instruction_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                       "model_id": MODEL_ID, "revision": MODEL_REVISION, "device": "cpu",
                       "model_provenance_path": str(PROVENANCE_PATH.resolve()),
                       "model_provenance_sha256": provenance_sha256,
                       "model_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                       "output_path": str(output), "output_sha256": _comparison_sha256(output),
                       "sample_rate_hz": payload.get("sample_rate_hz"),
                       "channels": payload.get("channels"), "duration_seconds": payload.get("duration_seconds"),
                       "worker": payload}
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return receipt
        except Exception as exc:
            failure = {**reservation, "status": "failed", "error": str(exc)}
            receipt_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise

    async def synthesize_production(
        self, *, text: str, output_path: Path, lane: dict[str, Any], settings=None,
    ) -> dict[str, Any]:
        """Generate one approved job's complete narration in a new local WAV."""
        from atnos.production.local_voice_lane import instruction_for_preset, MODEL_ID as LANE_MODEL_ID, MODEL_REVISION as LANE_REVISION
        from atnos.production.project_profile import content_hash
        try:
            instruction = instruction_for_preset(str(lane.get("preset") or ""))
        except RuntimeError as exc:
            raise QwenTTSLocalRuntimeError("Production synthesis requires a valid job-scoped Ryan approval lane with a supported preset.") from exc

        body = {key: lane.get(key) for key in (
            "job_id", "request_hash", "frozen_profile_hash", "sample_sha256", "model_id",
            "model_revision", "preset", "instruction_sha256", "approval_sha256", "public_authorized", "local_only",
        )}
        if (not lane.get("public_authorized") or lane.get("local_only") is not True
                or lane.get("model_id") != LANE_MODEL_ID or lane.get("model_revision") != LANE_REVISION
                or lane.get("instruction_sha256") != hashlib.sha256(instruction.encode("utf-8")).hexdigest()
                or lane.get("digest") != content_hash(body)):
            raise QwenTTSLocalRuntimeError("Production synthesis requires a valid job-scoped Ryan approval lane.")
        if settings is None:
            raise QwenTTSLocalRuntimeError("Production synthesis must revalidate its persisted job registration.")
        from atnos.production.local_voice_lane import load_local_voice_lane
        registered = load_local_voice_lane(settings, str(lane["job_id"]))
        if registered is None or registered.as_dict() != lane:
            raise QwenTTSLocalRuntimeError("Production synthesis lane is not the current immutable job registration.")
        if not isinstance(text, str) or not text.strip() or len(text) > 30000:
            raise QwenTTSLocalRuntimeError("Production narration text is empty or exceeds the local lane limit.")
        output = output_path.resolve()
        if output.drive.casefold() != "e:":
            raise QwenTTSLocalRuntimeError("All local Qwen outputs must remain on E:.")
        from atnos.production.order_store import ProductionOrderStore
        order_root = Path(ProductionOrderStore(settings).get(str(lane["job_id"]))["artifact_dir"]).resolve()
        if order_root.drive.casefold() != "e:":
            raise QwenTTSLocalRuntimeError("Authorized job artifact root must remain on E:.")
        try:
            relative = output.relative_to(order_root)
        except ValueError as exc:
            raise QwenTTSLocalRuntimeError("Production WAV must remain inside the authorized job artifact root.") from exc
        if not relative.parts:
            raise QwenTTSLocalRuntimeError("Production WAV must be a file inside the authorized job artifact root.")
        if output.suffix.casefold() != ".wav" or output.exists():
            raise QwenTTSLocalRuntimeError("Production synthesis requires a new WAV path.")
        if not _verified_model_files():
            raise QwenTTSLocalRuntimeError("The pinned Qwen model file hashes do not match provenance.")
        if not RUNTIME_PYTHON.is_file():
            raise QwenTTSLocalRuntimeError("The pinned local Qwen Python runtime is unavailable.")
        output.parent.mkdir(parents=True, exist_ok=True)
        request = {"model_dir": str(MODEL_DIR), "output_path": str(output), "text": text,
                   "speaker": "Ryan", "language": "English", "instruction": instruction,
                   "max_threads": self.max_threads}
        try:
            result = await asyncio.to_thread(
                _run_qwen_child, [str(RUNTIME_PYTHON), str(Path(__file__).resolve()), "--worker"],
                json.dumps(request, ensure_ascii=False), _isolated_environment(self.max_threads),
                str(RUNTIME_ROOT), self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            # The owned child lifecycle also checks create_time and descendants
            # before terminating anything beyond the direct Popen handle.
            raise QwenTTSLocalRuntimeError("Local Qwen narration exceeded its configured timeout.") from exc
        except OSError as exc:
            raise QwenTTSLocalRuntimeError(f"Could not start the isolated Qwen runtime: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "worker returned no diagnostic").strip()[-1200:]
            raise QwenTTSLocalRuntimeError(f"Isolated Qwen narration failed (exit {result.returncode}): {detail}")
        payload = next((candidate for line in reversed(result.stdout.splitlines())
                        if isinstance((candidate := _json_object(line)), dict)
                        and candidate.get("model_id") == MODEL_ID), None)
        if payload is None or payload.get("device") != "cpu" or payload.get("output_path") != str(output):
            raise QwenTTSLocalRuntimeError("Qwen worker did not confirm the authorized CPU WAV output.")
        payload.update({"provider": "qwen_local", "preset": lane["preset"], "lane_digest": lane["digest"],
                        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "cost_usd": 0, "provider_calls": 1})
        return payload


def _run_qwen_child(command: list[str], input_text: str, environment: dict[str, str], cwd: str,
                    timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    """Run the direct pinned Python child and clean only its identity-checked process family."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env=environment, cwd=cwd, shell=False,
                               creationflags=flags)
    created = None
    try:
        import psutil
        created = psutil.Process(process.pid).create_time()
    except Exception:
        pass
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if created is not None:
            try:
                from atnos.production.steps.voice_synthesis import _terminate_process_tree
                _terminate_process_tree(process.pid, expected_created=created)
            except Exception:
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            stdout, stderr = exc.output or "", exc.stderr or ""
        raise subprocess.TimeoutExpired(command, timeout_seconds, output=stdout, stderr=stderr) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _isolated_environment(max_threads: int) -> dict[str, str]:
    """Pass only required Windows/runtime variables; never forward API credentials."""
    keep = ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE")
    environment = {key: os.environ[key] for key in keep if os.environ.get(key)}
    environment.update({
        "PATH": str(RUNTIME_PYTHON.parent) + os.pathsep + os.environ.get("PATH", ""),
        "TEMP": r"E:\Atnos\Temp\qwen3",
        "TMP": r"E:\Atnos\Temp\qwen3",
        "HF_HOME": r"E:\Atnos\Storage\ToolCaches\huggingface-qwen3",
        "HF_HUB_CACHE": r"E:\Atnos\Storage\ToolCaches\huggingface-qwen3",
        "HUGGINGFACE_HUB_CACHE": r"E:\Atnos\Storage\ToolCaches\huggingface-qwen3",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": r"E:\Atnos\Temp\pycache-qwen3",
        "CUDA_VISIBLE_DEVICES": "",
        "OMP_NUM_THREADS": str(max_threads),
        "MKL_NUM_THREADS": str(max_threads),
        "OPENBLAS_NUM_THREADS": str(max_threads),
        "NUMEXPR_NUM_THREADS": str(max_threads),
    })
    return environment


def _json_object(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _comparison_file_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise QwenTTSLocalRuntimeError(f"Comparison evidence is unavailable: {path.name}") from exc


def _comparison_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise QwenTTSLocalRuntimeError(f"Comparison evidence is unavailable: {path.name}") from exc
    return digest.hexdigest()


def _comparison_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QwenTTSLocalRuntimeError(f"Comparison {label} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise QwenTTSLocalRuntimeError(f"Comparison {label} must be a JSON object.")
    return value


def audition_scope_sha256(request: dict[str, Any]) -> str:
    """Bind execution consent to this exact draft, separately from voice approval."""
    body = {key: value for key, value in request.items() if key != "execution_authorization"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def order_audition_source(settings, job_id: str, feedback_ids: list[str]) -> dict[str, Any]:
    """Read existing order/feedback/voice-lane evidence; never mutate production."""
    from atnos.production.local_voice_lane import load_local_voice_lane
    from atnos.production.order_feedback import ProductionFeedbackStore
    from atnos.production.order_store import ProductionOrderStore

    if (settings is None or not isinstance(job_id, str)
            or re.fullmatch(r"prod-[a-zA-Z0-9_-]+", job_id) is None
            or not isinstance(feedback_ids, list) or not feedback_ids
            or any(not isinstance(item, str) or not item for item in feedback_ids)
            or len(set(feedback_ids)) != len(feedback_ids)):
        raise QwenTTSLocalRuntimeError("Order audition requires settings, a job and unique feedback IDs.")
    order = ProductionOrderStore(settings).get(job_id)
    root = Path(order["artifact_dir"]).resolve()
    if (root.drive.casefold() != "e:"
            or not root.is_relative_to((settings.data_dir / "production").resolve())
            or order.get("request", {}).get("project") != "wealth_blueprint"
            or order.get("request", {}).get("delivery_mode") != "local_only"
            or order.get("worker_live") is not False
            or order.get("status") not in {"ready", "script_ready", "blocked"}):
        raise QwenTTSLocalRuntimeError("Order audition requires an idle, existing local-only WB order on E:.")
    by_id = {item["id"]: item for item in ProductionFeedbackStore(settings).list(job_id)}
    records = [by_id.get(key) for key in feedback_ids]
    if any(not isinstance(item, dict) or item.get("active") is not True
           or item.get("category") != "voice" or item.get("source_job_id") != job_id for item in records):
        raise QwenTTSLocalRuntimeError("Order audition requires current voice feedback for this source job.")
    lane = load_local_voice_lane(settings, job_id, read_only_snapshot=True)
    if (lane is None or lane.model_id != MODEL_ID or lane.model_revision != MODEL_REVISION
            or lane.local_only is not True):
        raise QwenTTSLocalRuntimeError("Order audition requires the current registered local Ryan lane.")
    script_file = root / "script.json"
    return {"source_job_id": job_id, "source_script_path": str(script_file),
            "source_script_sha256": _comparison_sha256(script_file),
            "source_snapshot": {
                "profile_hash": order["profile_hash"], "status": order["status"],
                "current_step": order["current_step"], "provider_calls": order["provider_calls"],
                "reserved_cost_units": order["reserved_cost_units"],
                "voice_lane_digest": lane.digest, "baseline_preset": lane.preset,
                "feedback": records}}


def _validate_order_audition(request: dict[str, Any], request_file: Path, settings) -> None:
    if (not request_file.is_relative_to(AUDITION_REPORT_ROOT.resolve())
            or request.get("request_path") != str(request_file)
            or request_file.parent.name != request.get("source_job_id")
            or request_file.parent.parent.name != "voice-auditions"
            or request.get("scope") != "voice_audition_only"
            or request.get("preserve_current_mastering") is not True
            or type(request.get("max_new_synthesis_requests")) is not int
            or request["max_new_synthesis_requests"] != 1
            or type(request.get("max_cpu_threads")) is not int or request["max_cpu_threads"] != 4
            or type(request.get("timeout_seconds")) is not int
            or request["timeout_seconds"] != AUDITION_TIMEOUT_SECONDS
            or request.get("samples") != [{"sample_id": "ryan_neutral", "speaker": "Ryan",
                                           "preset": COMPARISON_SAMPLES["ryan_neutral"][1]}]):
        raise QwenTTSLocalRuntimeError("Order audition is limited to one neutral Ryan candidate in its report folder.")
    authorization = request.get("execution_authorization")
    if (not isinstance(authorization, dict) or authorization.get("status") != "authorized"
            or authorization.get("scope_sha256") != audition_scope_sha256(request)
            or not isinstance(authorization.get("user_response"), str)
            or not authorization["user_response"].strip()
            or type(authorization.get("max_new_synthesis_requests")) is not int
            or authorization["max_new_synthesis_requests"] != 1):
        raise QwenTTSLocalRuntimeError("Order audition needs explicit execution authorization bound to the exact draft.")
    current = order_audition_source(settings, request.get("source_job_id"), request.get("feedback_ids"))
    if any(request.get(key) != value for key, value in current.items()):
        raise QwenTTSLocalRuntimeError("Order audition source snapshot changed; prepare and review a new draft.")


def _verified_model_files() -> bool:
    try:
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        if (provenance.get("model_id") != MODEL_ID or provenance.get("revision") != MODEL_REVISION
                or provenance.get("license") != "apache-2.0" or provenance.get("verified_files") is not True):
            return False
        files = provenance.get("files", [])
        if (type(provenance.get("files_expected_and_verified")) is not int
                or provenance["files_expected_and_verified"] != EXPECTED_MODEL_FILE_COUNT
                or len(files) != EXPECTED_MODEL_FILE_COUNT):
            return False
        for item in files:
            relative = Path(item["path"])
            path = (MODEL_DIR / relative).resolve()
            path.relative_to(MODEL_DIR.resolve())
            if not path.is_file() or path.stat().st_size != item["bytes"]:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                return False
        return bool(provenance.get("files"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def runtime_availability() -> dict[str, Any]:
    """Fast preflight check; model integrity is fully verified before inference."""
    try:
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        model_pin_ok = (provenance.get("model_id") == MODEL_ID and provenance.get("revision") == MODEL_REVISION
                        and provenance.get("license") == "apache-2.0" and provenance.get("verified_files") is True)
        files = provenance.get("files") or []
        model_files_present = (type(provenance.get("files_expected_and_verified")) is int
            and provenance["files_expected_and_verified"] == EXPECTED_MODEL_FILE_COUNT
            and len(files) == EXPECTED_MODEL_FILE_COUNT) and all(
            (MODEL_DIR / Path(item["path"])).is_file()
            and (MODEL_DIR / Path(item["path"])).stat().st_size == item["bytes"] for item in files)
    except (OSError, json.JSONDecodeError):
        model_pin_ok, model_files_present = False, False
    available = RUNTIME_PYTHON.is_file() and MODEL_DIR.is_dir() and model_pin_ok and model_files_present
    return {"available": available, "provider": "qwen_local", "model_id": MODEL_ID,
            "revision": MODEL_REVISION, "device": "cpu", "max_threads": 4,
            "model_files_present": model_files_present,
            "integrity_check": "full_sha256_before_inference"}


def _worker_main() -> int:
    """Child-only worker. No service, settings, API, or profile imports occur here."""
    try:
        request = json.loads(sys.stdin.read())
        import soundfile as sf
        import torch
        from qwen_tts import Qwen3TTSModel

        threads = int(request["max_threads"])
        if not 1 <= threads <= 4:
            raise ValueError("CPU thread limit must be 1-4.")
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        if torch.cuda.is_available():
            raise RuntimeError("Qwen preview requires the CPU-only runtime; CUDA unexpectedly became available.")
        model = Qwen3TTSModel.from_pretrained(
            request["model_dir"],
            device_map="cpu",
            dtype=torch.float32,
            attn_implementation="sdpa",
            local_files_only=True,
        )
        wavs, sample_rate = model.generate_custom_voice(
            text=request["text"],
            language=request["language"],
            speaker=request["speaker"],
            instruct=request["instruction"],
        )
        if len(wavs) != 1:
            raise RuntimeError("Expected exactly one preview waveform.")
        output = Path(request["output_path"])
        sf.write(str(output), wavs[0], int(sample_rate), format="WAV", subtype="PCM_16")
        import numpy as np
        samples, sr = sf.read(str(output), dtype="float32", always_2d=True)
        duration = len(samples) / sr
        if not np.isfinite(samples).all() or duration <= 0 or sr != int(sample_rate):
            raise RuntimeError("Generated preview WAV failed basic technical validation.")
        print(json.dumps({
            "device": "cpu",
            "torch_version": torch.__version__,
            "torch_cuda_available": torch.cuda.is_available(),
            "threads": threads,
            "model_id": MODEL_ID,
            "revision": MODEL_REVISION,
            "speaker": request["speaker"],
            "language": request["language"],
            "instruction": request["instruction"],
            "output_path": str(output),
            "sample_rate_hz": int(sr),
            "channels": int(samples.shape[1]),
            "duration_seconds": round(duration, 3),
            "peak_abs": float(np.max(np.abs(samples))),
            "rms": float(np.sqrt(np.mean(np.square(samples)))),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    raise SystemExit(_worker_main())
