"""简体中文字幕：由音频时间轴生成 SRT（交付/平台）与 ASS（烧录），并做 G3 校验。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Cue:
    index: int
    start: float
    end: float
    text: str
    line_id: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def cps(self) -> float:
        """阅读速度：汉字/秒。普通话字幕舒适上限约 9 字/秒。"""
        chars = len(self.text.replace(" ", ""))
        return chars / self.duration if self.duration > 0 else float("inf")


def cues_from_audio_timeline(audio_timeline: list[dict]) -> list[Cue]:
    cues = []
    for i, entry in enumerate(sorted(audio_timeline, key=lambda e: e["start"]), start=1):
        if entry.get("kind") not in (None, "narration", "dialogue", "lyric"):
            continue
        cues.append(Cue(index=i, start=float(entry["start"]), end=float(entry["end"]),
                        text=str(entry["text"]).strip(), line_id=str(entry.get("line_id", ""))))
    for i, cue in enumerate(cues, start=1):
        cue.index = i
    return cues


def _srt_ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 3600_00)
    m, cs = divmod(cs, 60_00)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [f"{c.index}\n{_srt_ts(c.start)} --> {_srt_ts(c.end)}\n{c.text}" for c in cues]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_ass(cues: list[Cue], *, width: int = 1920, height: int = 1080, font_name: str = "Noto Sans CJK SC") -> str:
    header = f"""[Script Info]
Title: Mandarin Factory Subtitles
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},52,&H00FFFFFF,&H000000FF,&HAA000000,&H64000000,0,0,0,0,100,100,0,0,1,2.4,0.8,2,60,60,64,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        text = c.text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")
        lines.append(f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Default,,0,0,0,,{{\\fad(120,180)}}{text}")
    return header + "\n".join(lines) + "\n"


def validate(cues: list[Cue], total_duration: float, *, max_cps: float = 9.5) -> list[dict]:
    """G3 字幕门：返回缺陷列表（dict 含 severity/message）。"""
    defects: list[dict] = []
    if not cues:
        defects.append({"severity": "critical", "message": "无字幕条目：音频时间轴为空或无对白/旁白"})
        return defects
    prev_end = 0.0
    for c in cues:
        if not c.text:
            defects.append({"severity": "major", "message": f"字幕 {c.index} 文本为空"})
        if c.start < prev_end - 1e-6:
            defects.append({"severity": "major", "message": f"字幕 {c.index} 与上一条重叠（start={c.start:.2f} < prev_end={prev_end:.2f}）"})
        if c.end > total_duration + 0.05:
            defects.append({"severity": "critical", "message": f"字幕 {c.index} 结束于 {c.end:.2f}s，超出成片时长 {total_duration:.2f}s", "auto_fix": "extend_final_shot"})
        if c.cps > max_cps:
            defects.append({"severity": "minor", "message": f"字幕 {c.index} 阅读速度 {c.cps:.1f} 字/秒过快"})
        prev_end = max(prev_end, c.end)
    return defects
