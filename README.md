# scenic-music-studio · 音乐风景号产线

AI 音乐 × 风景视频制作生产线：**先听懂音乐，再决定画面**。输入一首授权音乐
和一个主题，产出切点跟随音乐结构、可局部重做的 1080p 风景混剪成片。
发布永远由人工执行——产线只到"上传物料"为止。

## 它和"找几段视频加音乐自动拼接"的区别

三个数据契约把创作决定固化下来，改哪补哪，不用整条重做：

```
brief.json     你的创作指令（主题/音乐/时长/基调）
   ↓ 音乐理解引擎
music_map.json 音乐在什么时间发生了什么：节拍网格、能量曲线、
               段落边界（opening→build→climax→settle→ending）、切镜候选点
   ↓ AI 导演（规则骨架 + GLM 创意层）
storyboard.json 每个镜头想表达什么：检索词、情绪、运动预期、时长
   ↓ 时间轴规划（切点吸附）
timeline.json   最终用哪个素材、截哪一段、放在时间轴哪里、
                每个切点吸附到哪个音乐候选（kind: section/onset/bar）
```

剪辑原则：段落边界是硬约束；段内切点**接近**（不强卡）节拍/乐句候选，
±0.6s 容差，吸附会让镜头短于 2s 下限时放弃。验收线：≥80% 切点落容差内。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env        # 填 PEXELS_API_KEY（必需）/ ZHIPU_API_KEY（可选）
python cli.py doctor        # 体检，不耗额度

# 放一首授权音乐进 music/（曲库格式与授权红线见 music/README.md）
python cli.py create --theme 治愈系山海 --music music/xxx.mp3 --duration 60
python cli.py make project-001          # 制作，断点续跑
python cli.py show project-001          # 状态 + 时间轴 + 质检概览

python cli.py redo-shot project-001 5 --note "高潮再壮阔一点"   # 局部重做
python cli.py make project-001          # 只有该镜头真正重做，其余走缓存

python cli.py review-template project-001 --out my_review.json   # 六项待填表
python cli.py approve project-001 \
    --reviewer 你的名字 --checklist my_review.json \
    --watched-full --final-sha256 <看过的成片 SHA256>            # 人工放行
python cli.py publish-prepare project-001   # 生成 upload_info.txt（不投稿）
```

放行绑定的是**具体那一版成片**：approve 记录 reviewer、六项清单
（opening/climax/cuts/transitions/color/ending 必须逐项为 true）、
完整观看声明，以及 final.mp4 / timeline.json / qc_report.json 三个 SHA256。
之后任何重渲、redo-shot、改 timeline 都会自动撤销旧审批，
`publish-prepare` 落盘前还会再复核一次。这是本地自声明，不是身份认证；
程序不会替人看片，也不预填清单。

没有 API key 也能跑通全链：`make --offline`（占位素材 + 规则导演）。

## 可解释 · 可追溯 · 可盲评

```bash
python cli.py report project-001        # 剪辑决策报告：逐镜头为什么这样切、
                                        # 用哪段素材、配什么转场（output/decision_report.md）
python cli.py ab-package project-001 project-002 --out ab_experiment/x
                                        # 两版成片匿名随机化为 版本甲/乙 + 盲评表；
                                        # 对应关系封存于 projects/.ab_keys/（评完对答案）
python cli.py prune-cache --max-files 60  # 素材缓存护栏（只保留最新 N 个，库不膨胀）
```

每次 make 落盘 `projects/<id>/provenance.json`：音乐/brief/风格画像的
sha1、配置指纹、导演模式、素材清单（含许可）、成片与封面哈希——
多轮修改后仍可回答"最终成片到底用了什么"。

## 验收证据可搬运、可离线核验

```bash
python cli.py export-evidence project-001 --out docs/acceptance/xxx
                                        # 复制 JSON 契约并把路径改写为证据内相对路径，
                                        # 每个文件记 SHA256；--include-media 才复制
                                        # 音乐/素材/成片（先确认再分发许可）
python cli.py verify-evidence docs/acceptance/xxx
                                        # 离线重算哈希：缺文件、被改动、路径越界都报错
python cli.py verify-evidence docs/acceptance/xxx --require-media
```

证据包不含绝对路径，换机器/换目录也能核验。元数据版可以过完整性
校验，但会被明确标注 `self_contained: false`（成片与素材不在包内）；
不会因为"哈希对得上"就宣称审美验收通过。

## 缓存不是"阶段 done"

`manifest.freshness` 记录每个阶段**实际吃进去的输入**与产出的 SHA256：
音乐文件内容、brief、风格画像、配置、离线开关、素材字节都会参与指纹。
所以"改音乐/brief/配置但不删 projects/"不会再被旧步骤骗过去；
产物缺失或片段被替换也会重建。反过来，只重跑 QC 不会覆盖上一轮渲染
写下的 provenance，失败或制作期间输入变化也不会给旧成片洗出新来源。
redo-shot 仍然只动目标镜头，其余片段的文件名与 mtime 不变。

## 阶段流水线（manifest.json 记账，断点续跑）

```
music_map → storyboard → timeline → materials → render → qc → review(人工)
```

QC 是确定性闸门：规格/时长 ±1.5s/静音/削波/黑帧/封面/时间轴不变量/
切点吸附率；审美起伏由人工审片裁决，`approve` 前不生成上传物料。
本项目自包含：无任何对外部相邻仓库的运行时依赖，可独立克隆运行。

## 设计要点

- 五段情绪模型（opening/build/climax/settle/ending）的镜头时长区间与
  xfade 重叠回补公式是本项目自身调参的结果，全部在 `src/timeline.py`；
- 音乐分析为纯 numpy STFT 实现，不依赖 librosa/numba，后端可整体替换；
- 制作与发布分离：产线只交付成片与上传物料，投稿动作永远由人执行。

## 测试

```bash
python -m pytest -q        # 全离线：合成音乐验证分析器、时间轴不变量、端到端出片
```

## 风格画像（照着某个 UP 的节奏剪）

```bash
# 1 下载参考样片到本地（仅自用分析，不入库）——放到 reference/samples/
# 2 生成画像：量化镜头时长/切镜密度/硬切占比/色彩，输出剪辑参数建议
python cli.py analyze-reference reference/samples/a.mp4 reference/samples/b.mp4 \
    --channel 频道名 --out docs/reference/xxx.json
# 3 按该风格建项目（角色镜头区间/最短镜头/硬切主导转场全部随画像走）
python cli.py create --style docs/reference/xxx.json --theme ... --music ...
```

已内置画像：`docs/reference/weimeifengjingxian.json`（唯美风景线，292.7 万粉；
2 支样片实测 ≈16 切/分、镜头中位 2.6s、硬切 79% → 23 镜/60s、段内 0.13s 快切、
仅段落边界柔和转场）。样片仅用于剪辑规律分析，不复制其画面与音乐。
