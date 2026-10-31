import unittest
from pathlib import Path
import asyncio
import math
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psutil

from atnos.services.local_ctc_alignment_text import build_alignment_targets, build_word_timestamps
from atnos.services.local_ctc_alignment_service import align, LocalCTCAlignmentError
from atnos.services.local_ctc_alignment_service import _OwnedProcessTree, _cleanup_owned_worker, _kill_owned_process_tree, _record_owned_process_tree, PYTHON, WORKER


LABELS = tuple("|'ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class LocalCtcAlignmentTextTests(unittest.TestCase):
    def test_word_ranges_skip_boundary_tokens_and_keep_hyphenated_words_together(self):
        text = "alpha one-time ten-dollar\nONE HUNDRED ONE DOLLARS"
        chars, ranges = build_alignment_targets(text, LABELS)
        self.assertEqual(chars, list("ALPHA|ONETIME|TENDOLLAR|ONE|HUNDRED|ONE|DOLLARS"))
        self.assertEqual([w for w, _, _ in ranges], text.split())
        spans = [(i * 0.1, i * 0.1 + 0.05, 0.9) for i in range(len(chars))]
        words = build_word_timestamps(text, spans, 100)
        one_time = words[1]
        ten_dollar = words[2]
        self.assertEqual(one_time["text"], "one-time")
        self.assertAlmostEqual(one_time["start_sec"], ranges[1][1] * 0.1)
        self.assertAlmostEqual(one_time["end_sec"], (ranges[1][2] - 1) * 0.1 + 0.05)
        self.assertAlmostEqual(ten_dollar["start_sec"], ranges[2][1] * 0.1)
        self.assertAlmostEqual(ten_dollar["end_sec"], (ranges[2][2] - 1) * 0.1 + 0.05)

    def test_newlines_and_whitespace_runs_collapse_to_single_boundary(self):
        flat, flat_ranges = build_alignment_targets("ONE HUNDRED ONE DOLLARS", LABELS)
        multiline, multiline_ranges = build_alignment_targets("ONE\n\tHUNDRED   ONE\nDOLLARS", LABELS)
        self.assertEqual(multiline, flat)
        self.assertEqual([(w, a, b) for w, a, b in flat_ranges], [(w, a, b) for w, a, b in multiline_ranges])

    def test_curly_apostrophe_maps_to_supported_ascii_target_and_preserves_word(self):
        chars, ranges = build_alignment_targets("don’t", LABELS)
        self.assertEqual(chars, list("DON'T"))
        self.assertEqual(ranges[0][0], "don’t")

    def test_rejects_character_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, "token count mismatch"):
            build_word_timestamps("one two", [(0, 0.1, 0.9)], 1)

    def test_rejects_confidence_below_gate(self):
        chars, _ = build_alignment_targets("one", LABELS)
        with self.assertRaisesRegex(ValueError, "Low CTC confidence"):
            build_word_timestamps("one", [(0, 0.1, 0.01), (0.1, 0.2, 0.01), (0.2, 0.3, 0.01)], 1)

    def test_rejects_overlapping_word_spans(self):
        chars, ranges = build_alignment_targets("one two", LABELS)
        spans = [(i * 0.1, i * 0.1 + 0.05, 0.9) for i in range(len(chars))]
        boundary = ranges[1][1]
        spans[boundary] = (spans[boundary - 2][0], spans[boundary - 2][1] + 0.1, 0.9)
        with self.assertRaisesRegex(ValueError, "Overlapping or non-monotonic"):
            build_word_timestamps("one two", spans, 10)

    def test_rejects_nan_and_infinite_timestamps_confidences_and_duration(self):
        valid = [(0.0, 0.1, 0.9), (0.1, 0.2, 0.9), (0.2, 0.3, 0.9)]
        variants = [
            ([(math.nan, 0.1, 0.9), *valid[1:]], 1),
            ([valid[0], (0.1, math.inf, 0.9), valid[2]], 1),
            ([valid[0], (0.1, 0.2, math.nan), valid[2]], 1),
            (valid, math.inf),
        ]
        for spans, duration in variants:
            with self.subTest(spans=spans, duration=duration):
                with self.assertRaises(ValueError):
                    build_word_timestamps("one", spans, duration)

    def test_rejects_confidence_greater_than_one(self):
        spans = [(0, .1, .9), (.1, .2, 1.01), (.2, .3, .9)]
        with self.assertRaisesRegex(ValueError, r"outside \[0, 1\]"):
            build_word_timestamps("one", spans, 1)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree identity test")
    def test_cleanup_kills_only_the_worker_root_and_its_recorded_descendant(self):
        code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
        root = subprocess.Popen([str(PYTHON), "-c", code, str(WORKER)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state = _OwnedProcessTree(root.pid)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not state.children:
                _record_owned_process_tree(state)
                time.sleep(.1)
            self.assertTrue(state.children, "expected one owned child process to be recorded")
            child_pids = list(state.children)
            self.assertTrue(_kill_owned_process_tree(state))
            root.wait(timeout=5)
            self.assertFalse(psutil.pid_exists(root.pid))
            self.assertTrue(all(not psutil.pid_exists(pid) for pid in child_pids))
        finally:
            if root.poll() is None:
                root.kill()
                root.wait(timeout=5)

    def test_unverified_cleanup_fails_without_unbounded_wait_or_kill(self):
        proc = SimpleNamespace(pid=424242, wait=AsyncMock())
        state = _OwnedProcessTree(proc.pid)
        audit_path = Path(r"E:\Atnos\App\tmp\caption-align") / f"cleanup-test-{os.getpid()}.jsonl"
        with patch("atnos.services.local_ctc_alignment_service.CLEANUP_AUDIT_PATH", audit_path), \
             patch("atnos.services.local_ctc_alignment_service._record_owned_process_tree"), \
             patch("atnos.services.local_ctc_alignment_service._kill_owned_process_tree", return_value=False):
            with self.assertRaisesRegex(LocalCTCAlignmentError, "PID 424242 was left untouched"):
                asyncio.run(_cleanup_owned_worker(proc, state))
        proc.wait.assert_not_awaited()
        event = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(event["worker_pid"], 424242)
        self.assertEqual(event["reason"], "identity_unverified")
        audit_path.unlink()

    def test_rejects_missing_audio_before_starting_worker(self):
        missing = Path(r"E:\Atnos\App\tmp\caption-align\missing-audio-test.wav")
        output = Path(r"E:\Atnos\App\reports\wb-10-dollar-pilot-20261005\local-voice\alignment\negative-check.json")
        with self.assertRaisesRegex(LocalCTCAlignmentError, "Audio and a non-empty"):
            asyncio.run(align(missing, "one two", output))


if __name__ == "__main__":
    unittest.main()
