"""项目状态模式（AUTONOMOUS CINEMATIC MANDARIN VIDEO FACTORY §4）。

`project.json` 是单个工厂项目的唯一状态源：策划、资产、时间轴、质检、返工
与交付记录全部落盘，保证任何阶段中断后可从磁盘恢复，而不是靠对话记忆。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STAGES = ("planning", "assets", "audio", "editing", "qc", "rework", "done", "blocked")


@dataclass(slots=True)
class Project:
    title: str = ""
    status: str = "planning"
    user_brief: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    target_platform: str = "generic"
    duration_seconds: float = 0.0
    aspect_ratio: str = "16:9"
    width: int = 1920
    height: int = 1080
    fps: int = 24
    language: str = "zh-CN"
    voice_mode: str = "narration"  # narration（画外音，免口型）| speaking | singing
    creative_direction: dict[str, Any] = field(default_factory=dict)
    story: dict[str, Any] = field(default_factory=dict)
    characters: list[dict[str, Any]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    visual_bible: dict[str, Any] = field(default_factory=dict)
    audio_bible: dict[str, Any] = field(default_factory=dict)
    # script: [{id, shot_id, speaker, text, audio(相对路径), kind}]
    script: list[dict[str, Any]] = field(default_factory=list)
    lyrics: list[dict[str, Any]] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    storyboard: list[dict[str, Any]] = field(default_factory=list)
    # shots: [{id, source(相对路径), source_kind(image|video|card), motion, line_ids, card_text...}]
    shots: list[dict[str, Any]] = field(default_factory=list)
    audio_timeline: list[dict[str, Any]] = field(default_factory=list)
    edit_timeline: list[dict[str, Any]] = field(default_factory=list)
    subtitles: list[dict[str, Any]] = field(default_factory=list)
    quality_gates: dict[str, Any] = field(default_factory=dict)
    defect_log: list[dict[str, Any]] = field(default_factory=list)
    revision_log: list[dict[str, Any]] = field(default_factory=list)
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    render_options: dict[str, Any] = field(default_factory=dict)

    # ---------- 持久化 ----------
    @classmethod
    def load(cls, project_dir: str | Path) -> "Project":
        path = Path(project_dir) / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, project_dir: str | Path) -> Path:
        path = Path(project_dir) / "project.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ---------- 状态与日志 ----------
    def set_status(self, status: str) -> None:
        if status not in STAGES:
            raise ValueError(f"unknown stage: {status} (allowed: {STAGES})")
        self.status = status

    def log_defect(self, gate: str, message: str, severity: str = "major", auto_fix: str = "") -> dict[str, Any]:
        entry = {"ts": _now(), "gate": gate, "severity": severity, "message": message, "auto_fix": auto_fix}
        self.defect_log.append(entry)
        return entry

    def log_revision(self, action: str, detail: str = "") -> None:
        self.revision_log.append({"ts": _now(), "action": action, "detail": detail})

    def add_deliverable(self, kind: str, rel_path: str, note: str = "") -> None:
        self.deliverables = [d for d in self.deliverables if d.get("path") != rel_path]
        self.deliverables.append({"kind": kind, "path": rel_path, "note": note})

    # ---------- 查询 ----------
    def line_by_id(self, line_id: str) -> dict[str, Any] | None:
        return next((ln for ln in self.script if ln.get("id") == line_id), None)

    def lines_for_shot(self, shot_id: str) -> list[dict[str, Any]]:
        return [ln for ln in self.script if ln.get("shot_id") == shot_id]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
