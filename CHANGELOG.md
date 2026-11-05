# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号采用语义化（主.次.修订）。

## [0.5.1] — 2026-11-05 · 选片：入点跟 motion_hint，下载前拒绝人物

### 变更
- **入点不再固定在前 1/3**。Pexels 预览帧做低分辨率帧差：`aerial` / `push` /
  `pan` 选有运动且窗口内没有硬切的一段，`static` 选帧差低的一段
  （`src/shot_window.py`）。比不出差异、或镜头没有这四种 hint 时，仍用前 1/3；
- **人物筛选发生在 mp4 下载之前**。入点附近的一张预览帧交给检测器，有人就
  换下一条候选，不把该片下进缓存，也不等到整条时间轴渲完。近邻去重可以放宽，
  有人的候选不行；
- 检测器是 OpenCV 自带的 HOG 行人检测（权重在 opencv 包内，测试不下载）。
  未安装 opencv 或初始化失败时 **放行**，素材记 `people_check=unavailable`。
  单测注入假检测器，CI 不需要 GPU。

### 修复
- **qtrle 管道在 Python 3.12 上崩溃**：`make_card_mov` / `render_card` 在
  `communicate()` 之前关掉 ffmpeg 的 stdin。CPython 3.12 的 POSIX 实现会再
  flush 这个已关闭的管道，抛 `ValueError: flush of closed file`（3.13 已忽略，
  故只有 Ubuntu 3.12 的 CI 变红）。改为让 `communicate()` 自己关闭 stdin。

## [0.5.0] — 2026-11-03 · 代审闸门：workbuddy 审片入列

### 新增
- **`cli review-workbuddy <id>`**：投稿前的内容维度代审——抽帧（片头卡/
  均布采样/片尾落版）交本机 WorkBuddy/CodeBuddy CLI 审片（人物/动物穿帮、
  主题契合、坏帧过曝、片头片尾完整性），判词多轮累积落盘
  `output/review_workbuddy.json`；fail/unavailable 退出码 1，便于脚本闸门
  （`src/workbuddy_review.py`、`tools/workbuddy_review.sh`）；
- 代审不可用（CLI/网关缺失）时 verdict=unavailable，不阻塞发布，人工兜底；
  正式 `approve` 账本仍要求用户本人签审，代审不代签；
- 实战验证：project-004（主题偏离+坏帧）被判 fail 未发布；project-005 经
  keywords 钉主题 + 镜头排除修复后两轮过审发布
  （BV1LQ4M69EvU《落日海平线》）。

## [0.4.1] — 2026-11-02 · 修复片尾落版隐形

### 修复
- **片尾落版整段隐形**：`apply_overlays` 原用 `enable='between(t,a,b)'` 定点
  叠加，但 enable 关闭期间 overlay 仍按帧序消费卡片输入——窗口在片尾时
  卡片已被"播完"，重复的淡出全透明末帧让落版不可见且无任何报错
  （0.4.0 已发布的动效版成片受影响，片头标题卡因窗口在片头恰好同步而正常）。
  改为 `setpts` 平移卡片时间戳 + `eof_action=pass` 的定点叠加标准写法；
- 新增回归用例 `test_overlay_outro_card_visible_at_tail`：片尾窗口内卡片
  必须真的可见（旧实现下必红）。

## [0.4.0] — 2026-10-28 · Remotion 混合渲染：动效片头/片尾

### 新增
- **动效卡叠加**：`cli motion <id>` 给成片叠 Remotion 动画标题卡（楷体呼吸
  缩放 + 细线展开）与署名落版（逐行淡入），产出 `final_carded.mp4`；时长/
  音乐/QC 不受影响（`src/motion.py`、`remotion/src/`）；
- **降级链**：remotion/ 依赖或系统浏览器缺失时自动退回 ffmpeg drawtext
  静态上字（降级不阻塞）；
- 落版音乐署名自动推导：brief 的 `music_credit` → 曲库 manifest 按文件名
  匹配 → 文件名 `Title [Artist]` 模式（`create --brief` 建项同样补全）；
- 卡片缓存（`title_card.mov`/`outro_card.mov` 存在即命中）与 `--force`
  强制重渲；
- `tests/test_motion.py`：alpha 全链路回归（合成透明卡 → qtrle 回路 →
  overlay 后背景透出/色块叠上/窗口外原样）+ 缓存/降级/署名推导单测；
- 项目级 ZCode skill `.zcode/skills/scenic-motion-card`（用法与陷阱速查）。

### 关键修复（过程中定位）
- Remotion 官方推荐的 vp9+yuva420p webm 透明卡路线在本仓库所用 ffmpeg
  构建上不可用：VP9 alpha 编解码双向失效（带透明度的 PNG 编出的 alpha
  解回恒不透明），首版标题卡变成黑底整帧盖住风景。已改为 Remotion PNG
  序列 → qtrle/argb MOV 中间格式，并用分区 PSNR 字节级验证（片头无字区
  ≈43dB 透底、字带 16dB、中段零影响）。

## [0.3.0] — 2026-10-28 · 阶段一加固：可解释 · 可追溯 · 可盲评

### 新增
- **生成物溯源**：每次 make 落盘 `projects/<id>/provenance.json`——音乐/
  brief/风格画像的 sha1、配置指纹、导演模式、素材清单（含许可）、成片与
  封面哈希（`src/provenance.py`）；
- **剪辑决策报告**：`cli report <id>` 输出逐镜头决策 markdown——切点依据
  （段落边界/吸附候选类型与偏移）、素材来源与取材点、转场策略、修改指引
  （`src/report.py`）；
- **A/B 盲评实验包**：`cli ab-package` 把两版成片匿名随机化为「版本甲/乙」
  并生成四维盲评表（音乐匹配/画面节奏/风格一致性/整体观感），对应关系封存
  于 `projects/.ab_keys/`（gitignore），评完对答案（`src/ab_experiment.py`）；
- **素材缓存护栏**：`cli prune-cache --max-files N` 按时间保留最新 N 个缓存
  素材；`doctor` 报告缓存体积，超 2GB 提示清理；
- QC 新增**相邻镜头重复素材同区间**检查（带切点时间戳定位）。

### 变更
- 真实音乐识别测试**不再静默跳过**：样本缺失按 FAIL 处理；CI 显式执行该
  步骤（含 pipefail）作为独立验收项——绿灯必须包含真实音乐测试；
- 首支成片投稿 B 站（project-002，唯美风景线风格，23 镜/60s，QC 通过，
  切点吸附 22/22；发布前 redo-shot 替换了一支人像穿帮镜头）。

### 协同改进（同日合入）
- **审片绑定成片版本**：审片结论与具体成片哈希绑定，改片后旧审片自动失效；
- **缓存输入指纹**：片段缓存键纳入素材/参数指纹，杜绝"改了配置却命中旧缓存"；
- **证据包可搬运核验**（`src/evidence.py`）：验收证据打包后可在另一台机器
  校验一致性；新增可搬运验收包 `docs/acceptance/2026-10-28-portable/`；
- **风格画像度量版本标注**：画像生成器标注度量参数版本，验收边界写明
  （`docs/reference/README.md`）。

## [0.2.0] — 2026-10-27 · 风格画像：照着参考频道的节奏剪

### 新增
- **参考视频风格分析器**（`src/style_analyzer.py`）：RGB 三通道帧差 +
  直方图的镜头边界检测（区分硬切/渐变，合成已知切点视频验证 ±0.35s）、
  镜头时长分布、切镜密度曲线、色彩/运动统计、BGM 节奏，聚合多支样片
  生成剪辑参数建议（`derived`）；
- `cli analyze-reference` 生成画像；`cli create --style` 与
  `config.style.profile` 接入；
- **画像驱动剪辑**：角色镜头区间、最短镜头下限（绝对下限 1.0s）、转场
  策略（`cut_dominant`：段内 0.13s 快切≈硬切，仅音乐段落边界保留柔和
  转场）全部由画像覆盖默认基线；
- 内置**唯美风景线**画像（292.7 万粉参考频道，2 支样片实测 ≈16.4 切/分、
  镜头中位 2.6s、硬切 79%）；实拍验证：同一音乐/主题下 23 镜快切版
  （project-002）与 16 镜柔和版（project-001）节奏对照鲜明。

### 修复
- 镜头边界检测对互补色剪切漏检（纯灰度差分不可见色相变化）。

## [0.1.0] — 2026-10-27 · 核心产线 MVP：先听懂音乐，再决定画面

### 新增
- **音乐理解引擎**（纯 numpy STFT，无 librosa/numba 依赖）：节拍网格、
  能量曲线、起振强度、实测段落边界（opening→build→climax→settle→ending）、
  三类切镜候选（段落边界>强起振>小节线）、播放窗口选择；
- **AI 导演**：规则骨架（段落→镜头数/时长分配）+ GLM 创意层（检索词与
  情绪标签，结构校验，失败降级不阻塞）；
- **节拍吸附时间轴**：段落边界硬约束，段内切点 ±0.6s 容差吸附最近候选
  （接近而非硬卡节拍），最短镜头约束、xfade 重叠守恒；
- **素材链路**：Pexels 检索/断点续传下载/完整性校验/结构化建档，相邻镜头
  去重，素材溯源索引（含许可与使用记录）；
- **渲染与质检**：逐镜规范化渲染（片段级缓存，redo-shot 只重做目标镜头）、
  xfade 链合成、音乐响度归一与淡入淡出、封面抽取；QC 闸门（规格/时长/
  静音/削波/黑帧/封面/时间轴不变量/切点吸附率 ≥80%）；
- **制作/发布分离**：人工审片放行（approve/reject）、局部重做（redo-shot）、
  上传物料生成（标题/简介/署名块/标签），不自动投稿；
- 断点续跑的阶段状态机（manifest.json）；
- 独立性：无外部相邻仓库依赖，可独立克隆运行；GitHub Actions 四组矩阵
  （ubuntu/windows × Python 3.12/3.13）全离线测试；
- 真实音乐识别回归（CC BY 样本入仓，断言来自人工核对运行）。

### 修复
- 播放窗口边缘碎片段落导致时间轴铺不满（碎段并入邻居，段落 ≥2s）；
- 崩溃遗留半截素材被当完整缓存（下载原子落盘 + 字节数校验）；
- 素材溯源索引漏 append 恒为空；
- GLM 网络故障静默降级（记录失败原因 + 3 次重试）。

[0.3.0]: https://github.com/zhaohongjun20-creator/scenic-music-studio/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/zhaohongjun20-creator/scenic-music-studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zhaohongjun20-creator/scenic-music-studio/releases/tag/v0.1.0
