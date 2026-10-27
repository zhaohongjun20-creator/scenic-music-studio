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

python cli.py approve project-001       # 人工看片后放行
python cli.py publish-prepare project-001   # 生成 upload_info.txt（不投稿）
```

没有 API key 也能跑通全链：`make --offline`（占位素材 + 规则导演）。

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
