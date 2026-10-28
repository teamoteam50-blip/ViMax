"""原创氛围配乐合成（无外部版权素材依赖）：慢起伏和声垫 + 海浪噪声。

在没有音乐生成工具/凭据的环境中保证成片始终有合法原创配乐；
接入正式音乐模型后，把生成文件写到同一路径即可替换本模块输出。
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

# Dm9 — Bbmaj7 — Fmaj9 — A7sus：忧伤而开阔的海边叙事进行
_PROGRESSION_HZ = [
    [146.83, 220.00, 349.23, 523.25],   # D3 A3 F4 C5
    [116.54, 174.61, 293.66, 440.00],   # Bb2 F3 D4 A4
    [87.31, 174.61, 261.63, 392.00],    # F2 F3 C4 G4
    [110.00, 164.81, 293.66, 392.00],   # A2 E3 D4 G4
]


def synthesize_ambient_bed(out_path: str | Path, duration: float, *, sr: int = 44100, sea: bool = True) -> Path:
    n = int(duration * sr)
    t = np.arange(n) / sr
    mix = np.zeros(n, dtype=np.float64)

    chord_len = duration / len(_PROGRESSION_HZ)
    for ci, freqs in enumerate(_PROGRESSION_HZ):
        s0, s1 = int(ci * chord_len * sr), int(min(n, (ci + 1) * chord_len * sr))
        seg_t = t[s0:s1]
        seg = np.zeros(s1 - s0)
        for f in freqs:
            for partial, amp in ((1.0, 0.5), (2.0, 0.18), (3.01, 0.06)):
                seg += amp * np.sin(2 * math.pi * f * partial * seg_t + 0.15 * np.sin(2 * math.pi * 0.07 * seg_t))
        env = np.sin(np.linspace(0, math.pi, s1 - s0)) ** 1.5  # 每和弦呼吸包络
        mix[s0:s1] += seg * env
    # 低音根音
    for ci, freqs in enumerate(_PROGRESSION_HZ):
        s0, s1 = int(ci * chord_len * sr), int(min(n, (ci + 1) * chord_len * sr))
        env = np.sin(np.linspace(0, math.pi, s1 - s0)) ** 1.2
        mix[s0:s1] += 0.35 * np.sin(2 * math.pi * freqs[0] / 2 * t[s0:s1]) * env

    if sea:
        rng = np.random.default_rng(7)
        noise = rng.standard_normal(n)
        kernel = np.ones(2400) / 2400  # 简易低通 → 海浪轰鸣
        swell = np.convolve(noise, kernel, mode="same") * 38.0
        swell *= 0.55 + 0.45 * np.sin(2 * math.pi * 0.09 * t + 1.2)  # ~11s 一浪
        mix += swell * 0.5

    # 全曲淡入/淡出 + 峰值归一到 -6 dBFS（最终响度在混音 loudnorm 统一）
    fade_in = int(min(2.5 * sr, n / 4))
    fade_out = int(min(4.0 * sr, n / 3))
    mix[:fade_in] *= np.linspace(0, 1, fade_in)
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)
    peak = np.max(np.abs(mix)) or 1.0
    mix *= 0.5 / peak

    stereo = np.column_stack([mix, np.roll(mix, 132) * 0.98])  # 轻微声相宽度
    pcm = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out
