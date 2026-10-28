# 风格画像（reference_profile）

本目录存放参考 UP 主的量化剪辑画像，供 `python cli.py create --style ...`
驱动镜头时长区间、最短镜头、转场策略与调色建议。

## 画像怎么来的

```bash
# 样片只放本地（reference/samples/ 已 gitignore），不随仓库分发
python cli.py analyze-reference reference/samples/a.mp4 reference/samples/b.mp4 \
    --channel 频道名 --out docs/reference/xxx.json
```

分析链：ffmpeg 低分辨率解码 → 帧差切镜检测（hard/gradual）→ 镜头时长
分布、切镜密度、色彩与运动统计 → 聚合多支样片 → `derived` 参数建议
（`role_shot_len` / `min_shot_s` / `transition_policy` / `video`）。

## 度量版本（重要）

`metric_version: 2` 起，`density_by_third_cpm` 按**时间**三等分统计每段的
切/分钟。v1 曾把镜头按时长排序后按**个数**三等分，导致三段数值必然接近，
看起来"密度均匀"其实是算法假象。

已入库的 `weimeifengjingxian.json` 生成于 v1（`metric_version` 字段缺失，
`samples[*].density_by_third_cpm` 形如 `[20.45, 19.48, 19.48]` 且后两段完全相同，
即为该假象）。因此该文件里：

- **不可直接采信**：`samples[*].density_by_third_cpm`、
  `derived.density_ratio_climax_to_calm`（由上述三段值算出）。
- **未受影响**：`samples[*].shots_per_minute` / `len_*` / `hard_cut_frac` /
  `color` / `motion`，以及真正驱动剪辑的 `derived.role_shot_len`、
  `min_shot_s`、`transition_policy`、`video`——它们都不读三段密度。

重新生成画像（需要原始样片）即可得到 v2 数值，无需手工改 JSON。

## 验收边界

- 画像验证的是**剪辑参数层面的相似**（切镜密度、镜头长度分布、硬切占比、
  饱和度倾向），不是画面内容相似，也不是"像某位 UP 的风格"这种主观判断。
- 样片仅用于统计规律分析，不复制其画面与音乐。
- 导出验收证据时，`evidence_manifest.json` 的 `acceptance.reference_style`
  恒为 `not_verified_by_export`：导出工具不会替你判定风格是否还原。
