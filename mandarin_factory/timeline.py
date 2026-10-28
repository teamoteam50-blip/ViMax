"""音频优先的时间轴构建（工厂强约束：先锁定音频，再排画面）。

流程：实测每句台词音频时长 → 推导每个镜头时长 → 计入转场重叠 →
得到画面 edit_timeline 与逐句 audio_timeline（字幕与混音共用同一真源）。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .schema import Project

LINE_GAP = 0.35        # 同镜头内句间留白（秒）
SHOT_LEAD = 0.35       # 镜头开始后人声进入前的留白
SHOT_TAIL = 0.55       # 末句结束后镜头保留
MIN_SHOT = 4.0
CARD_DURATION = 2.4


def probe_duration(ffmpeg: str, media: str | Path) -> float:
    """用 ffmpeg -i 解析时长（环境中未必有 ffprobe）。失败即抛错，绝不编造。"""
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(media)],
                          capture_output=True, text=True, timeout=60)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    if not m:
        raise RuntimeError(f"无法测定媒体时长: {media}\n{proc.stderr[-500:]}")
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def build_timelines(project: Project, project_dir: Path, ffmpeg: str,
                    *, crossfade: float = 0.6, line_durations: dict[str, float] | None = None,
                    final_shot_pad: float = 0.0) -> tuple[list[dict], list[dict], float]:
    """返回 (edit_timeline, audio_timeline, total_duration) 并写回 project。

    line_durations 可注入（测试用）；否则对每句音频实测。
    """
    durations: dict[str, float] = dict(line_durations or {})
    for line in project.script:
        lid = str(line["id"])
        if lid not in durations:
            audio_rel = line.get("audio")
            if not audio_rel:
                raise FileNotFoundError(f"台词 {lid} 未绑定音频文件（配音阶段未完成）")
            durations[lid] = probe_duration(ffmpeg, project_dir / str(audio_rel))
            line["duration_seconds"] = round(durations[lid], 3)

    edit: list[dict] = []
    audio_tl: list[dict] = []
    cursor = 0.0  # 已计入转场重叠的画面时间游标
    last_idx = len(project.shots) - 1
    for idx, shot in enumerate(project.shots):
        lines = project.lines_for_shot(str(shot["id"]))
        if shot.get("source_kind") == "card":
            shot_duration = float(shot.get("duration", CARD_DURATION))
        else:
            spoken = sum(durations[str(l["id"])] for l in lines)
            spoken += LINE_GAP * max(0, len(lines) - 1)
            shot_duration = max(MIN_SHOT, SHOT_LEAD + spoken + SHOT_TAIL + (final_shot_pad if idx == last_idx else 0.0))
        start = cursor
        end = start + shot_duration
        edit.append({
            "shot_id": shot["id"], "index": idx, "start": round(start, 3), "end": round(end, 3),
            "duration": round(shot_duration, 3), "source": shot.get("source", ""),
            "source_kind": shot.get("source_kind", "image"), "motion": shot.get("motion", ""),
            "transition_out": shot.get("transition_out", "fade") if idx < last_idx else None,
            "crossfade": crossfade if idx < last_idx else 0.0,
        })
        t = start + SHOT_LEAD
        for line in lines:
            d = durations[str(line["id"])]
            audio_tl.append({
                "line_id": line["id"], "shot_id": shot["id"], "kind": line.get("kind", project.voice_mode),
                "speaker": line.get("speaker", "旁白"), "text": line.get("text", ""),
                "audio": line.get("audio", ""), "start": round(t, 3), "end": round(t + d, 3),
                "duration": round(d, 3),
            })
            t += d + LINE_GAP
        cursor = end - (crossfade if idx < last_idx else 0.0)

    total = edit[-1]["end"] if edit else 0.0
    project.edit_timeline = edit
    project.audio_timeline = audio_tl
    project.duration_seconds = round(total, 3)
    return edit, audio_tl, total
