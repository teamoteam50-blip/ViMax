"""成片质检门 G3–G6：黑场 / 静音 / 响度 / 字幕 / 规格。只认实测，不认声明。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import subtitles as subs_mod
from .schema import Project
from .timeline import probe_duration

LUFS_MIN, LUFS_MAX, LUFS_TARGET = -18.0, -14.0, -16.0
MAX_BLACK = 1.6
MAX_SILENCE = 2.2


def _ffmpeg_stderr(ffmpeg: str, args: list[str]) -> str:
    proc = subprocess.run([ffmpeg, "-hide_banner", *args], capture_output=True, text=True, timeout=900)
    return proc.stderr


def probe_video(ffmpeg: str, media: Path) -> dict:
    err = _ffmpeg_stderr(ffmpeg, ["-i", str(media)])
    info: dict = {"duration": probe_duration(ffmpeg, media), "has_video": "Video:" in err, "has_audio": "Audio:" in err}
    m = re.search(r"Video:.*?(\d{3,5})x(\d{3,5}).*?([\d.]+) (?:fps|tbr)", err)
    if m:
        info.update(width=int(m.group(1)), height=int(m.group(2)), fps=float(m.group(3)))
    m = re.search(r"Audio:.*?(\d+) Hz", err)
    if m:
        info["sample_rate"] = int(m.group(1))
    return info


def _windows(err: str, kind: str) -> list[tuple[float, float]]:
    out = []
    for m in re.finditer(rf"{kind}_start: ([\d.]+).*?{kind}_end: ([\d.]+)", err, re.S):
        out.append((float(m.group(1)), float(m.group(2))))
    return out


def black_windows(ffmpeg: str, media: Path) -> list[tuple[float, float]]:
    err = _ffmpeg_stderr(ffmpeg, ["-i", str(media), "-vf", "blackdetect=d=1.0:pix_th=0.03", "-an", "-f", "null", "-"])
    return _windows(err, "black")


def silence_windows(ffmpeg: str, media: Path) -> list[tuple[float, float]]:
    err = _ffmpeg_stderr(ffmpeg, ["-i", str(media), "-af", "silencedetect=noise=-45dB:d=1.5", "-vn", "-f", "null", "-"])
    return _windows(err, "silence")


def measure_loudness(ffmpeg: str, media: Path) -> dict:
    err = _ffmpeg_stderr(ffmpeg, ["-i", str(media), "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    m = re.search(r"\{\s*\"input_i\".*?\}", err, re.S)
    if not m:
        raise RuntimeError("loudnorm 测量失败，未得到响度数据")
    data = json.loads(m.group(0))
    return {"input_i_lufs": float(data["input_i"]), "input_tp_dbtp": float(data["input_tp"]),
            "input_lra": float(data["input_lra"])}


def _overlaps(window: tuple[float, float], zones: list[tuple[float, float]], pad: float = 0.35) -> bool:
    return any(window[0] >= z[0] - pad and window[1] <= z[1] + pad for z in zones)


def run_qc(ffmpeg: str, project: Project, final_mp4: Path, cues: list[subs_mod.Cue]) -> tuple[dict, list[dict]]:
    """返回 (quality_gates, defects)。defects 含 gate/severity/message/auto_fix。"""
    gates: dict = {}
    defects: list[dict] = []

    def defect(gate: str, d: dict) -> None:
        defects.append({"gate": gate, "severity": d.get("severity", "major"),
                        "message": d["message"], "auto_fix": d.get("auto_fix", "")})

    if not final_mp4.exists() or final_mp4.stat().st_size == 0:
        defect("G6", {"severity": "critical", "message": f"成片文件不存在或为空: {final_mp4}"})
        gates["G6"] = {"pass": False, "detail": "missing output"}
        return gates, defects

    info = probe_video(ffmpeg, final_mp4)
    total = float(info["duration"])
    card_zones = [(float(e["start"]), float(e["end"])) for e in project.edit_timeline if e.get("source_kind") == "card"]
    tail_zone = [(max(total - 1.2, 0.0), total)]

    # G6 规格
    spec_ok = info.get("has_video") and info.get("has_audio") and info.get("width") == project.width and info.get("height") == project.height
    planned = float(project.duration_seconds or total)
    dur_ok = abs(total - planned) <= 1.0
    gates["G6"] = {"pass": bool(spec_ok and dur_ok), "probe": info, "planned_duration": planned}
    if not info.get("has_audio"):
        defect("G6", {"severity": "critical", "message": "成片无音轨"})
    if not dur_ok:
        defect("G6", {"message": f"成片时长 {total:.2f}s 与计划 {planned:.2f}s 偏差 >1s"})

    # G3 字幕
    sub_defects = subs_mod.validate(cues, total)
    for d in sub_defects:
        defect("G3", d)
    gates["G3"] = {"pass": all(d.get("severity") not in ("major", "critical") for d in sub_defects),
                   "cues": len(cues), "issues": sub_defects}

    # G4 黑场
    blacks = black_windows(ffmpeg, final_mp4)
    bad_black = [(s, e) for (s, e) in blacks if (e - s) > MAX_BLACK and not _overlaps((s, e), card_zones + tail_zone)]
    for s, e in bad_black:
        defect("G4", {"message": f"意外黑场 {s:.1f}–{e:.1f}s（{e - s:.1f}s）"})
    gates["G4"] = {"pass": not bad_black, "black_windows": blacks, "ignored_card_zones": card_zones}

    # G5 静音 + 响度
    silences = silence_windows(ffmpeg, final_mp4)
    bad_sil = [(s, e) for (s, e) in silences if (e - s) > MAX_SILENCE and not _overlaps((s, e), card_zones + tail_zone)]
    for s, e in bad_sil:
        defect("G5", {"message": f"中段意外静音 {s:.1f}–{e:.1f}s（{e - s:.1f}s）"})
    loud = measure_loudness(ffmpeg, final_mp4)
    loud_ok = LUFS_MIN <= loud["input_i_lufs"] <= LUFS_MAX and loud["input_tp_dbtp"] <= -0.5
    if not loud_ok:
        defect("G5", {"message": f"响度 {loud['input_i_lufs']:.1f} LUFS / 峰值 {loud['input_tp_dbtp']:.1f} dBTP 超出 {LUFS_MIN}~{LUFS_MAX} LUFS 目标",
                       "auto_fix": "adjust_master_gain"})
    gates["G5"] = {"pass": not bad_sil and loud_ok, "silence_windows": silences, "loudness": loud}

    gates["overall_pass"] = all(gates[g].get("pass", False) for g in ("G3", "G4", "G5", "G6"))
    return gates, defects
