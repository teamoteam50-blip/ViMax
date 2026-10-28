"""AUTONOMOUS CINEMATIC MANDARIN VIDEO FACTORY for ViMax.

把成片交付的最后一公里（普通话音频时间轴、简体字幕、原创配乐、剪辑合成、
调色、QC 质检与自动返工）做成确定性管线。镜头素材可来自 ViMax 管线产物
（mp4 片段）或已质检的关键帧（电影运镜合成），项目状态全部落盘 project.json。
"""
from .schema import Project

__all__ = ["Project", "run"]


def run(project_dir, *, keep_work: bool = False):
    from .factory import run as _run
    return _run(project_dir, keep_work=keep_work)
