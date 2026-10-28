"""工厂编排中枢：资产门 → 时间轴 → 字幕 → 配乐 → 渲染 → 质检 → 自动返工 → 交付。

对应规范 `prompts/mandarin_director.md`。任何阶段失败都落盘到 project.json，
status 置 blocked 并如实报告——绝不把半成品标为 done。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import qc as qc_mod
from . import render as render_mod
from . import subtitles as subs_mod
from . import timeline as tl_mod
from .schema import Project

MAX_REWORK = 2
CROSSFADE = 0.6


class FactoryBlocked(RuntimeError):
    """关键门禁失败且自动返工无法修复：停止并如实报告。"""


def _rel(project_dir: Path, p: Path) -> str:
    try:
        return str(p.relative_to(project_dir))
    except ValueError:
        return str(p)


def _prepare_cards(project: Project, project_dir: Path, font: Path) -> None:
    from .cards import make_card
    for shot in project.shots:
        if shot.get("source_kind") != "card":
            continue
        src = project_dir / str(shot["source"])
        make_card(src, width=project.width, height=project.height, font_path=font,
                  title=str(shot.get("card_title", project.title)),
                  subtitle=str(shot.get("card_subtitle", "")),
                  title_size=int(shot.get("card_title_size", 120)),
                  subtitle_size=int(shot.get("card_subtitle_size", 40)))


def _asset_gates(project: Project, project_dir: Path) -> None:
    missing: list[str] = []
    for shot in project.shots:
        src = project_dir / str(shot.get("source", ""))
        if not src.exists():
            missing.append(f"镜头 {shot.get('id')} 素材缺失: {shot.get('source')}")
    for line in project.script:
        audio = project_dir / str(line.get("audio", ""))
        if not str(line.get("audio", "")) or not audio.exists():
            missing.append(f"台词 {line.get('id')} 配音缺失: {line.get('audio')}")
    project.quality_gates["G1_assets"] = {"pass": not missing, "missing": missing}
    project.quality_gates["G2_audio"] = {"pass": not any("配音缺失" in m for m in missing)}
    for m in missing:
        project.log_defect("G1/G2", m, severity="critical")
    if missing:
        raise FactoryBlocked("资产/配音门未通过：\n- " + "\n- ".join(missing))


def _write_script_doc(project: Project, out: Path) -> None:
    lines = [f"# 《{project.title}》台本", ""]
    by_shot: dict[str, list[dict]] = {}
    for ln in project.script:
        by_shot.setdefault(str(ln.get("shot_id", "")), []).append(ln)
    for shot in project.shots:
        lines.append(f"## 镜头 {shot.get('id')}（{shot.get('source_kind', 'image')}，运镜 {shot.get('motion', '-')}）")
        if shot.get("description"):
            lines.append(f"画面：{shot['description']}")
        for ln in by_shot.get(str(shot.get("id")), []):
            lines.append(f"- {ln.get('speaker', '旁白')}：{ln.get('text', '')}")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def run(project_dir: str | Path, *, keep_work: bool = False) -> dict:
    project_dir = Path(project_dir).resolve()
    project = Project.load(project_dir)
    slug = str((project.render_options or {}).get("slug") or project_dir.name)
    ffmpeg = render_mod.find_ffmpeg()
    font = render_mod.find_font(project)

    export_dir = project_dir / "export"
    sub_dir = project_dir / "subtitles"
    final_mp4 = export_dir / f"{slug}_final.mp4"

    # ---- G1/G2：资产与配音门 ----
    project.set_status("assets")
    _prepare_cards(project, project_dir, font)
    _asset_gates(project, project_dir)
    project.save(project_dir)

    # ---- 时间轴 / 字幕 / 配乐（返工时按 pad 重建）----
    from .music import synthesize_ambient_bed
    final_pad = 0.0

    def build_all(pad: float):
        tl_mod.build_timelines(project, project_dir, ffmpeg, crossfade=CROSSFADE, final_shot_pad=pad)
        cues_local = subs_mod.cues_from_audio_timeline(project.audio_timeline)
        project.subtitles = [{"index": c.index, "start": c.start, "end": c.end, "text": c.text, "line_id": c.line_id} for c in cues_local]
        sub_dir.mkdir(parents=True, exist_ok=True)
        srt_path = sub_dir / f"{slug}.srt"
        ass_path = sub_dir / f"{slug}.ass"
        srt_path.write_text(subs_mod.to_srt(cues_local), encoding="utf-8")
        ass_path.write_text(subs_mod.to_ass(cues_local, width=project.width, height=project.height), encoding="utf-8")
        music_rel = str((project.render_options or {}).get("music_path") or "")
        music_path = project_dir / music_rel if music_rel and (project_dir / music_rel).exists() else project_dir / "audio" / "music_bed.wav"
        if music_path.name == "music_bed.wav":
            synthesize_ambient_bed(music_path, float(project.duration_seconds) + 0.5,
                                   sea=bool((project.render_options or {}).get("music_sea", True)))
        return cues_local, ass_path, srt_path, music_path

    project.set_status("editing")
    cues, ass_path, srt_path, music_path = build_all(final_pad)
    project.save(project_dir)

    # ---- 渲染 + QC + 自动返工 ----
    gates: dict = {}
    defects: list[dict] = []
    for attempt in range(MAX_REWORK + 1):
        render_mod.render_final(ffmpeg, project, project_dir, ass_path=ass_path,
                                music_path=music_path, font_path=font, out_path=final_mp4)
        project.set_status("qc")
        gates, defects = qc_mod.run_qc(ffmpeg, project, final_mp4, cues)
        project.quality_gates.update(gates)
        for d in defects:
            if not any(x["message"] == d["message"] for x in project.defect_log):
                project.log_defect(d["gate"], d["message"], severity=d["severity"], auto_fix=d.get("auto_fix", ""))
        project.save(project_dir)
        if gates.get("overall_pass"):
            break
        if attempt == MAX_REWORK:
            break

        project.set_status("rework")
        fixed = False
        for d in defects:
            if d.get("auto_fix") == "extend_final_shot":
                overflow = max((c.end for c in cues), default=0.0) - float(project.duration_seconds)
                final_pad += max(overflow, 0.0) + 1.0
                cues, ass_path, srt_path, music_path = build_all(final_pad)
                project.log_revision("extend_final_shot", f"末镜延长至 pad={final_pad:.2f}s")
                fixed = True
            elif d.get("auto_fix") == "adjust_master_gain":
                measured = float(gates["G5"]["loudness"]["input_i_lufs"])
                gain = float((project.render_options or {}).get("master_gain_db", 0.0)) + (qc_mod.LUFS_TARGET - measured)
                project.render_options["master_gain_db"] = round(max(-12.0, min(12.0, gain)), 2)
                project.log_revision("adjust_master_gain", f"实测 {measured:.1f} LUFS，母带增益 → {project.render_options['master_gain_db']:+.2f} dB")
                fixed = True
        project.save(project_dir)
        if not fixed:
            break

    # ---- 交付或阻断（如实标注）----
    poster = final_mp4.with_name(final_mp4.stem + "_poster.jpg")
    script_doc = export_dir / f"{slug}_script.md"
    _write_script_doc(project, script_doc)
    export_srt = export_dir / srt_path.name
    shutil.copyfile(srt_path, export_srt)
    qc_report = export_dir / f"{slug}_qc.json"
    qc_report.write_text(json.dumps({"gates": gates, "defects": defects}, ensure_ascii=False, indent=2), encoding="utf-8")

    if gates.get("overall_pass"):
        project.set_status("done")
        project.add_deliverable("final_video", _rel(project_dir, final_mp4), "H.264/AAC，烧录简体字幕")
        project.add_deliverable("subtitles_srt", _rel(project_dir, export_srt))
        project.add_deliverable("poster", _rel(project_dir, poster))
        project.add_deliverable("script", _rel(project_dir, script_doc))
        project.add_deliverable("qc_report", _rel(project_dir, qc_report))
        project.add_deliverable("project_state", "project.json")
    else:
        project.set_status("blocked")
        project.log_revision("blocked", "两轮自动返工后仍未通过全部质检门，保留当前导出供人工复核，不标完成")
        if final_mp4.exists():
            project.add_deliverable("preview_video_unapproved", _rel(project_dir, final_mp4), "未通过全部质检门，仅供复核")
        project.add_deliverable("qc_report", _rel(project_dir, qc_report))

    if not keep_work:
        shutil.rmtree(project_dir / ".render_work", ignore_errors=True)
    project.save(project_dir)

    summary = {"status": project.status, "title": project.title, "duration_seconds": project.duration_seconds,
               "final_video": _rel(project_dir, final_mp4) if final_mp4.exists() else None,
               "overall_pass": bool(gates.get("overall_pass")), "gates": gates, "defects": defects,
               "deliverables": project.deliverables}
    return summary
