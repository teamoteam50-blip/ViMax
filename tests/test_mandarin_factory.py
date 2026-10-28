"""Mandarin factory 纯逻辑测试：状态模式、字幕生成/校验、音频优先时间轴。

不依赖 ffmpeg/TTS（line_durations 注入），保证 CI 快速稳定。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mandarin_factory import subtitles as subs_mod
from mandarin_factory import timeline as tl_mod
from mandarin_factory.schema import Project


def _demo_project() -> Project:
    p = Project(title="测试片")
    p.script = [
        {"id": "L1", "shot_id": "s1", "speaker": "旁白", "kind": "narration", "text": "第一句。", "audio": "audio/l1.mp3"},
        {"id": "L2", "shot_id": "s2", "speaker": "旁白", "kind": "narration", "text": "第二句稍长一点。", "audio": "audio/l2.mp3"},
        {"id": "L3", "shot_id": "s2", "speaker": "旁白", "kind": "narration", "text": "同镜头第三句。", "audio": "audio/l3.mp3"},
    ]
    p.shots = [
        {"id": "card_title", "source": "assets/card.jpg", "source_kind": "card", "duration": 2.0, "motion": "static"},
        {"id": "s1", "source": "assets/a.jpg", "source_kind": "image", "motion": "zoom_in"},
        {"id": "s2", "source": "assets/b.jpg", "source_kind": "image", "motion": "zoom_out"},
    ]
    return p


class SchemaTests(unittest.TestCase):
    def test_roundtrip_and_stage_guard(self):
        p = _demo_project()
        p.log_defect("G1", "测试缺陷", severity="minor")
        with tempfile.TemporaryDirectory() as td:
            p.save(td)
            loaded = Project.load(td)
        self.assertEqual(loaded.title, "测试片")
        self.assertEqual(loaded.defect_log[0]["message"], "测试缺陷")
        self.assertEqual([ln["id"] for ln in loaded.lines_for_shot("s2")], ["L2", "L3"])
        with self.assertRaises(ValueError):
            loaded.set_status("not-a-stage")


class TimelineTests(unittest.TestCase):
    def test_audio_first_math_and_crossfade_offsets(self):
        p = _demo_project()
        durations = {"L1": 3.0, "L2": 4.0, "L3": 2.0}
        edit, audio_tl, total = tl_mod.build_timelines(
            p, Path("."), ffmpeg="ffmpeg", crossfade=0.6, line_durations=durations)

        # shot01: lead .35 + 3.0 + tail .55 = 3.9 → MIN_SHOT 4.0
        self.assertEqual(edit[1]["duration"], 4.0)
        # shot02: .35 + (4 + .35 gap + 2) + .55 = 7.25
        self.assertAlmostEqual(edit[2]["duration"], 7.25, places=3)
        # starts account for crossfade overlap
        self.assertAlmostEqual(edit[1]["start"], 2.0 - 0.6, places=3)
        self.assertAlmostEqual(edit[2]["start"], edit[1]["end"] - 0.6, places=3)
        self.assertAlmostEqual(total, edit[2]["end"], places=3)
        # every narration cue sits inside the film
        for entry in audio_tl:
            self.assertLessEqual(entry["end"], total)

    def test_missing_audio_raises_instead_of_faking(self):
        p = _demo_project()
        with self.assertRaises(FileNotFoundError):
            tl_mod.build_timelines(p, Path("/nonexistent"), ffmpeg="ffmpeg",
                                   line_durations={"L1": 1.0, "L2": 1.0})  # L3 无音频且无注入

    def test_final_shot_pad_extends_total(self):
        p = _demo_project()
        durations = {"L1": 3.0, "L2": 4.0, "L3": 2.0}
        _, _, base = tl_mod.build_timelines(p, Path("."), ffmpeg="ffmpeg", line_durations=durations)
        _, _, padded = tl_mod.build_timelines(p, Path("."), ffmpeg="ffmpeg", line_durations=durations, final_shot_pad=2.5)
        self.assertAlmostEqual(padded - base, 2.5, places=3)


class SubtitleTests(unittest.TestCase):
    def _cues(self):
        tl = [
            {"line_id": "L1", "kind": "narration", "text": "在东海最远的礁石上，有一座灯塔。", "start": 2.75, "end": 6.0},
            {"line_id": "L2", "kind": "narration", "text": "守灯人老周，把灯点亮。", "start": 6.5, "end": 9.0},
        ]
        return subs_mod.cues_from_audio_timeline(tl)

    def test_srt_format(self):
        srt = subs_mod.to_srt(self._cues())
        self.assertIn("1\n00:00:02,750 --> 00:00:06,000\n在东海最远的礁石上，有一座灯塔。", srt)
        self.assertIn("2\n00:00:06,500 --> 00:00:09,000", srt)

    def test_ass_has_style_and_events(self):
        ass = subs_mod.to_ass(self._cues())
        self.assertIn("Noto Sans CJK SC", ass)
        self.assertIn("Dialogue: 0,0:00:02.75,0:00:06.00,Default", ass)

    def test_validate_flags_overflow_with_auto_fix(self):
        cues = self._cues()
        defects = subs_mod.validate(cues, total_duration=8.0)  # L2 ends 9.0 > 8.0
        self.assertTrue(any(d.get("auto_fix") == "extend_final_shot" for d in defects))
        self.assertEqual(subs_mod.validate(cues, total_duration=10.0), [])

    def test_validate_flags_overlap_and_empty(self):
        bad = [subs_mod.Cue(1, 0.0, 3.0, "甲"), subs_mod.Cue(2, 2.0, 4.0, "")]
        msgs = " ".join(d["message"] for d in subs_mod.validate(bad, 10.0))
        self.assertIn("重叠", msgs)
        self.assertIn("文本为空", msgs)


if __name__ == "__main__":
    unittest.main()
