---
name: scenic-motion-card
description: 为 scenic-music-studio 成片叠加 Remotion 动效片头标题卡与片尾落版（混合渲染）。适用于该项目 make 出 final.mp4 之后要加动效包装的场景；不适用于独立于该仓库的一般视频动效需求。
---

# scenic-motion-card：动效片头/片尾叠加

把已渲好的成片 `projects/<pid>/output/final.mp4` 加上动画标题卡（片头）与
署名落版（片尾），产出 `final_carded.mp4`。一条命令：

```bash
python cli.py motion <project-id> [--force]
```

时长、音乐、QC 全部不受影响（动效只是叠在时间轴两端的透明层）。

## 架构与关键陷阱

混合渲染：Remotion 只渲几秒的卡片 → ffmpeg 叠回成片。

**中间格式必须是 qtrle/argb MOV，绝不能按 Remotion 官方文档用 vp9+yuva420p
webm**——本仓库所用 ffmpeg 构建（Winget 9.0 与 imageio-ffmpeg 均如此）对
VP9 alpha 编解码双双失效：即使输入 PNG 确带透明度，编出的 webm 解回恒为
不透明黑，标题卡会整帧盖住风景。症状识别：final_carded 片头/片尾变黑底。
`src/motion.py` 的 `render_card()` 已走 PNG 序列 → qtrle 管道，勿改回直渲
webm。回归钉子：`tests/test_motion.py::test_overlay_transparent_card_keeps_background`。

## 前置与降级

- `remotion/node_modules`（在 `remotion/` 下 `npm install`）+ 系统 Chrome/Edge。
- 缺任一即自动降级 ffmpeg drawtext 静态上字（降级不阻塞，输出里会提示）。
- 无须下载 Chrome Headless Shell：序列截图用系统 Chrome 即带 alpha。

## 定制

- 卡片样式：`remotion/src/TitleCard.tsx` / `OutroCard.tsx`
  （props：`title/subtitle/accent/durationSec` 与 `lines/accent/durationSec`，
  改样式不需要动 Python）。
- 时长/点缀色：`config.yaml` 的 `motion:` 段（title_s/outro_s/accent）。
- 落版署名自动推导：brief 的 `music_credit` → 曲库 manifest 按文件名匹配
  （`bgm/<mood>/` 旁的 manifest.json）→ 文件名 `Title [Artist]` 模式。

## 缓存

`title_card.mov` / `outro_card.mov` 存在即命中缓存；改了卡片源码或文案后
要删掉对应 .mov 或加 `--force` 重渲。

## 验收

渲完抽查两帧：片头无字角落应与 final.mp4 基本一致（背景透出），文字带
应有明显差异；中段应完全无差异。参考 `tests/test_motion.py` 的分区 PSNR
思路。
