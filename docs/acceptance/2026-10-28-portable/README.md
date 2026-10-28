# 阶段 1 验收证据（可搬运快照）· 2026-10-28

这是 `docs/acceptance/2026-10-27-phase1/` 的可移植版本。历史目录原样保留
（不改写历史记录），但如果把仓库克隆到别的机器，那里的 `timeline.json` /
`music_map.json` 仍会带着生成时的本机绝对路径，无法直接复现。

本目录由 `python cli.py export-evidence` 生成：所有 JSON 契约里的
音乐/素材路径都改写成**证据目录内的相对路径**，每个文件单独记 SHA256。

## 内容

| 文件 | 说明 |
| --- | --- |
| `evidence_manifest.json` | 逐文件 SHA256/字节数、媒体引用指纹、可搬运性检查结果 |
| `timeline.json` | 16 镜头切点与吸附记录（路径已改为相对） |
| `music_map.json` | 音乐结构（节拍网格/段落/切镜候选） |
| `storyboard.json` | 导演计划 |
| `brief.json` | 创作指令 |
| `manifest.json` / `review.json` | 阶段记账与审片状态 |
| `qc_report.json` | 技术质检报告 |
| `cover.jpg` | 封面（本包内已含，不受 .gitignore 影响的是其他媒体） |

媒体本体（1 首音乐、7 段素材、成片）**没有**复制进本包，因此
`self_contained: false`、`final_included: false`。原因有两个：体积，以及
素材许可。`evidence_manifest.json` 里保留了它们实际的 SHA256 与字节数，
拿到同样文件后可以逐个核对。

## 怎么核验

```bash
python cli.py verify-evidence docs/acceptance/2026-10-28-portable
# 输出 integrity_passed: true 才算文件没被改动、也没有绝对路径残留

# 若另外准备了媒体文件，可要求媒体齐备
python cli.py verify-evidence <含 media/ 的目录> --require-media
```

`verify-evidence` 是纯离线重算，不联网、不读仓库外路径；缺文件、被改动、
路径越界（`..`、盘符、符号链接）都会判失败。

## 验收边界（不要过度解读）

- `technical_qc: reported_pass`——数值来自项目自己的 QC 报告，本导出过程
  不做二次测帧；它不等于审美合格。
- `human_review: not_verified_by_export`——导出工具不会替人看片，也不会
  把审片状态改成通过。
- `reference_style: not_verified_by_export`——风格画像（`docs/reference/`）
  验证的是剪辑参数与参考片一致，不是画面内容相似。

## 与历史目录的关系

- `../2026-10-27-phase1/`：原始记录，保持不动，其中音乐路径指向生成时的
  本机曲库 `C:\Users\...\bgm\hopeful\Awake [johnfrantz17].mp3`。
- 本目录：同一份 16 镜头记录的可搬运版本，`timeline.json` 的
  `source_sha256` 与历史版逐字节一致（见 `evidence_manifest.json` 的
  `files.timeline.json.source_sha256`）。
