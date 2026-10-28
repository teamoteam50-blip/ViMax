"""FFmpeg 渲染：关键帧电影运镜 / ViMax 视频片段 → 转场 → 调色 → 字幕烧录 → 混音导出。

诚实降级说明：当环境没有可用的视频生成凭据时，镜头画面由已质检的关键帧
做缓慢推/拉/移（Ken Burns 电影运镜）合成；当 shots 的 source 为真实视频
片段（如 ViMax 管线产物）时直接取材剪辑。两种来源共用同一时间轴与交付链。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .schema import Project


def find_ffmpeg() -> str:
    env = os.environ.get("FFMPEG_PATH")
    if env and Path(env).exists():
        return env
    which = shutil.which("ffmpeg")
    if which:
        return which
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - 环境问题
        raise RuntimeError("找不到 ffmpeg：请安装系统 ffmpeg，或 pip install imageio-ffmpeg") from exc


def find_font(project: Project) -> Path:
    opts = project.render_options or {}
    candidates: list[Path] = []
    if opts.get("font_path"):
        candidates.append(Path(str(opts["font_path"])))
    fonts_dir = Path(__file__).parent / "fonts"
    candidates += sorted(fonts_dir.glob("*.otf")) + sorted(fonts_dir.glob("*.ttf")) + sorted(fonts_dir.glob("*.ttc"))
    for base in (Path("/usr/share/fonts"), Path.home() / ".local/share/fonts"):
        if base.exists():
            candidates += [p for p in base.rglob("*") if p.suffix.lower() in (".otf", ".ttf", ".ttc")
                           and any(k in p.name.lower() for k in ("cjk", "notosc", "noto-sans-sc", "wqy", "han"))]
    for c in candidates:
        if c.exists() and c.stat().st_size > 100_000:
            return c
    raise FileNotFoundError(
        "找不到可用的简体中文字体。请将 Noto Sans CJK SC（.otf）放到 mandarin_factory/fonts/，"
        "或在 project.json 的 render_options.font_path 指定。下载方法见 mandarin_factory/README.md。")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败（exit {proc.returncode}）:\n{' '.join(cmd[:6])} ...\n{proc.stderr[-1800:]}")


def _escape_filter_path(p: Path) -> str:
    s = str(p.resolve())
    return s.replace("\\", "/").replace(":", r"\:").replace("'", r"\'").replace("[", r"\[").replace("]", r"\]")


def _motion_filter(motion: str, frames: int, w: int, h: int, fps: int) -> str:
    big = f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2}"
    span = 0.14
    rate = span / max(frames, 1)
    if motion == "zoom_out":
        z = f"max(1.14-{rate:.6f}*on,1.0)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == "pan_right":
        z = "1.14"
        x, y = f"(iw-iw/zoom)*on/{max(frames,1)}", "ih/2-(ih/zoom/2)"
    elif motion == "pan_left":
        z = "1.14"
        x, y = f"(iw-iw/zoom)*(1-on/{max(frames,1)})", "ih/2-(ih/zoom/2)"
    elif motion == "static":
        z = "1.02"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    else:  # zoom_in（默认）
        z = f"min(1.0+{rate:.6f}*on,1.14)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    return f"{big},zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},setsar=1"


def render_segment(ffmpeg: str, *, source: Path, source_kind: str, motion: str,
                     duration: float, w: int, h: int, fps: int, out: Path) -> None:
    frames = max(1, round(duration * fps))
    out.parent.mkdir(parents=True, exist_ok=True)
    if source_kind == "video":
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1,"
              f"trim=duration={duration:.3f},setpts=PTS-STARTPTS")
        cmd = [ffmpeg, "-y", "-hide_banner", "-stream_loop", "-1", "-i", str(source), "-vf", vf,
               "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", str(out)]
    else:  # image / card：单帧输入经 zoompan 展开为整段（含运动）
        vf = _motion_filter(motion or "zoom_in", frames, w, h, fps)
        cmd = [ffmpeg, "-y", "-hide_banner", "-i", str(source), "-vf", vf,
               "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", str(out)]
    _run(cmd)


def render_final(ffmpeg: str, project: Project, project_dir: Path, *,
                 ass_path: Path, music_path: Path, font_path: Path, out_path: Path,
                 work_dir: Path | None = None) -> Path:
    w, h, fps = project.width, project.height, project.fps
    edit = project.edit_timeline
    if not edit:
        raise RuntimeError("edit_timeline 为空：请先 build_timelines")
    work = work_dir or (project_dir / ".render_work")
    work.mkdir(parents=True, exist_ok=True)
    opts = project.render_options or {}

    seg_paths: list[Path] = []
    for e in edit:
        seg = work / f"seg_{int(e['index']):02d}.mp4"
        render_segment(ffmpeg, source=project_dir / str(e["source"]), source_kind=str(e["source_kind"]),
                       motion=str(e.get("motion") or "zoom_in"), duration=float(e["duration"]),
                       w=w, h=h, fps=fps, out=seg)
        seg_paths.append(seg)

    # ---- 画面链：xfade 转场 → 调色 → 烧字幕 → 首尾淡黑 ----
    inputs: list[str] = []
    for seg in seg_paths:
        inputs += ["-i", str(seg)]
    narration = project.audio_timeline
    for entry in narration:
        inputs += ["-i", str(project_dir / str(entry["audio"]))]
    music_idx = len(seg_paths) + len(narration)
    inputs += ["-i", str(music_path)]

    fc: list[str] = []
    for i in range(len(seg_paths)):
        fc.append(f"[{i}:v]format=yuv420p,settb=AVTB,setsar=1,fps={fps}[v{i}]")
    cur = "v0"
    for i in range(1, len(seg_paths)):
        prev = edit[i - 1]
        trans = str(prev.get("transition_out") or "fade")
        offset = float(edit[i]["start"])
        nxt = f"x{i}"
        fc.append(f"[{cur}][v{i}]xfade=transition={trans}:duration={float(prev['crossfade']):.3f}:offset={offset:.3f}[{nxt}]")
        cur = nxt
    total = float(project.duration_seconds)
    grade = "eq=contrast=1.05:saturation=1.08:brightness=0.0,unsharp=5:5:0.32:5:5:0.0"
    fades = f"fade=t=in:st=0:d=0.5,fade=t=out:st={max(total - 0.9, 0):.3f}:d=0.9"
    fc.append(f"[{cur}]{grade},ass='{_escape_filter_path(ass_path)}':fontsdir='{_escape_filter_path(font_path.parent)}',{fades},format=yuv420p[vout]")

    # ---- 声音链：逐句 adelay → 人声总线 → 与配乐混音 → loudnorm ----
    voice_labels: list[str] = []
    for j, entry in enumerate(narration):
        idx = len(seg_paths) + j
        delay_ms = int(round(float(entry["start"]) * 1000))
        lab = f"a{j}"
        fc.append(f"[{idx}:a]aresample=48000,adelay={delay_ms}|{delay_ms}[{lab}]")
        voice_labels.append(f"[{lab}]")
    if voice_labels:
        fc.append(f"{''.join(voice_labels)}amix=inputs={len(voice_labels)}:duration=longest:normalize=0[voice]")
    else:  # 无人声项目：静音人声总线兜底
        fc.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={total:.3f}[voice]")
    music_vol = float(opts.get("music_volume", 0.16))
    fc.append(f"[{music_idx}:a]aresample=48000,volume={music_vol},atrim=duration={total:.3f},"
              f"afade=t=out:st={max(total - 3.5, 0):.3f}:d=3.5[music]")
    master_gain = float(opts.get("master_gain_db", 0.0))
    gain_stage = f",volume={master_gain:.2f}dB" if abs(master_gain) > 0.05 else ""
    fc.append(f"[voice][music]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11{gain_stage},aresample=48000[aout]")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", *inputs, "-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
           "-metadata", f"title={project.title}", str(out_path)]
    _run(cmd)

    poster = out_path.with_name(out_path.stem + "_poster.jpg")
    grab_at = min(max(total * 0.55, 1.0), max(total - 1.0, 1.0))
    _run([ffmpeg, "-y", "-hide_banner", "-ss", f"{grab_at:.2f}", "-i", str(out_path),
          "-frames:v", "1", "-q:v", "3", str(poster)])
    return out_path
