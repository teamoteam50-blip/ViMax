# mandarin_factory — 全自动电影级普通话视频交付管线

把 ViMax 的规划产物（剧本/关键帧/镜头片段）收口为**最终可交付成片**：
普通话音频优先时间轴 → 简体中文字幕 → 原创配乐 → 剪辑合成 → 调色 → QC 质检 → 自动返工（≤2 轮）→ 导出。
行为规范见仓库根目录 `prompts/mandarin_director.md`；样板项目《守灯人》在 `projects/shoudengren/`。

## 运行

```bash
uv sync                                          # 仓库依赖（含 pillow、moviepy→imageio-ffmpeg）
# 或在独立环境： pip install imageio-ffmpeg pillow numpy
python -m mandarin_factory run projects/shoudengren
```

退出码 0 = 全部质检门通过、成片在 `projects/shoudengren/export/`；退出码 2 = blocked（不得当作成片交付，原因写在 `export/<slug>_qc.json` 与 `project.json` 的 defect_log）。

## 项目目录约定

```
<project>/
  project.json        # 唯一状态源（结构见 schema.py）
  assets/             # 角色参考图、关键帧、（视频镜头片段亦可）
  audio/              # 逐句普通话配音（路径与 script[].audio 一致）
  subtitles/          # 工厂生成的 .srt / .ass（自动）
  export/             # 最终成片 + SRT + 海报 + 台本 + QC 报告（自动）
```

`shots[].source_kind`：`image`（关键帧电影运镜合成）/ `video`（直接取材 ViMax 等产线生成的 mp4，循环/裁切到镜头时长）/ `card`（ PIL 字卡，字段 `card_title`/`card_subtitle`）。

## 中文字体（烧录字幕必需）

工厂按顺序查找：`project.json: render_options.font_path` → `mandarin_factory/fonts/*` → 系统字体目录中的 CJK 字体。
在仅放行 GitHub 的沙盒里可通过 GitHub API 拉取 Noto Sans CJK SC（约 16MB，已被 .gitignore 排除，不要提交）：

```bash
mkdir -p mandarin_factory/fonts
SHA=$(gh api "repos/googlefonts/noto-cjk/contents/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf" --jq .sha)
gh api "repos/googlefonts/noto-cjk/git/blobs/$SHA" --jq .content | base64 -d > mandarin_factory/fonts/NotoSansCJKsc-Regular.otf
```

## 与 ViMax 的衔接与诚实边界

- **镜头来源**：有视频供应商凭据时，用 ViMax `idea2video/script2video` 管线或 Agent 的 `vimax_render_video` 生成片段并在 `shots[]` 里以 `source_kind=video` 引用；没有凭据时退化为关键帧运镜，`assumptions` 与交付说明必须如实标注。
- **普通话配音/演唱与口型**：本管线消费已生成的逐句音频（`script[].audio`），合成本身由外部 TTS 完成。默认 `voice_mode=narration`（画外音免口型）；近景对白片需先锁定本管线的 `audio_timeline` 再做口型同步。
- **配乐**：`music.py` 为无外部服务时的原创合成垫底（可被正式音乐模型文件替换：`render_options.music_path`）。
- **质检门**：G1 资产存在、G2 配音存在（运行时复查）+ G3 字幕（重叠/超时→自动延长末镜）、G4 黑场、G5 静音与响度（−18~−14 LUFS→自动母带增益）、G6 规格（见 `qc.py`）。两轮返工不过即 `status=blocked`。

## 测试

```bash
python -m pytest tests/test_mandarin_factory.py
```
