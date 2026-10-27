# 阶段 1 验收记录 · 2026-10-27

对照审查清单（第二轮验收报告第 6 节）的逐项运行证据。审查者可按链接独立复核。

## 1. 自动化测试真实通过（不止配置了 CI）

- CI run [40511357813](https://github.com/zhaohongjun20-creator/scenic-music-studio/actions/runs/40511357813)
  （提交 509f109）：`test (3.12)` [success](https://github.com/zhaohongjun20-creator/scenic-music-studio/actions/runs/40511357813/job/122213810665)、
  `test (3.13)` [success](https://github.com/zhaohongjun20-creator/scenic-music-studio/actions/runs/40511357813/job/122213810946)。
- 本提交起 CI 矩阵扩展为 ubuntu × windows × {3.12, 3.13} 四组。
- **Windows 本机实测**（win32 10.0.26200 / Python 3.14.3）：`python -m pytest -q` 全绿
  （28 项，含真实 ffmpeg 渲染的端到端出片、局部返工缓存、风格差异、真实音乐识别）。

## 2. 独立目录运行

- 克隆到无任何相邻仓库的 `%TEMP%\sms-standalone`，独立 venv + pip install 后：
  25 项测试全绿；`cli.py doctor` 正确报告克隆自身路径；
  从 `$TEMP`（非项目目录）发起 `create → make --offline`，QC 通过、
  切点吸附 7/7、成片落在克隆自己的 `projects/` 内。

## 3. 真实素材 60 秒成片（project-001）

- 音乐：《Awake》johnfrantz17，CC BY 3.0（ccMixter），署名已写入 upload_info。
- 素材：7 段 Pexels 实拍（Pexels License），逐段来源页/作者见 `materials_used.json`。
- 结果：60.00s / 1920x1080 / 30fps / AAC 48kHz；QC 全过（`qc_report.json`）；
  **切点吸附 15/15**（容差 ±0.6s，验收线 80%）。
- 本目录文件：`timeline.json`（逐镜头切点与吸附记录）、`music_map.json`（音乐结构）、
  `contact_sheet.jpg`（16 镜头抽帧拼图，4x4）、`upload_info.txt`、`qc_report.json`。

## 4. 局部返工缓存

- CLI 级（真实项目）：`redo-shot project-001 14` → 仅镜头 14 换素材重渲（排除旧素材
  pexels-27489373 → 新素材 pexels-6815991），重新合成后 QC 仍过。
- 回归级（CI 常驻）：`test_offline_partial_redo_only_touches_target_shot`
  断言重做镜头 5 时其余片段文件名与 mtime 完全不变。

## 5. 已知边界（不宣称覆盖）

- QC 是技术闸门，**不代表审美合格**——人工观看结论记录在 `REVIEW.md`（待填）。
- 真实音乐识别目前仅 1 个样本（`tests/test_real_music.py`，断言来自人工核对），
  后续应扩充曲库并覆盖更多结构类型。
- 参考视频风格分析（学姐圆/风景线 → reference_profile.json）未实现，
  是下一阶段任务。
