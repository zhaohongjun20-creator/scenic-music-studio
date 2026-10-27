# music/

本项目自己的音乐目录（.gitignore，音频文件不入库）。

## 曲库清单（可选，推荐）

建 `music/music_catalog.json`，每首一条：

```json
[
  {
    "id": "calm-lake-01",
    "file": "calm-lake-01.mp3",
    "title": "曲名",
    "artist": "作者",
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "source_url": "https://下载或许可页",
    "mood": ["calm"],
    "duration": 125.0
  }
]
```

在 `config.yaml` 的 `music.catalogs` 里加上 `music/music_catalog.json` 后，
`create` 用到清单内的曲目会自动把署名写进上传物料。

## 授权红线

- **"免费 / royalty-free" 标签本身不是授权证明**，必须落实到具体许可
  条款和来源页，逐首记录在上面的清单里；
- 不使用 B 站/抖音等平台的热门歌曲（版权风险）；
- 推荐来源（入库前逐首核对许可页并留 `source_url`）：
  Pixabay Music、ccMixter、Free Music Archive（按许可筛选）、
  YouTube Audio Library、incompetech 等；
- 许可要求署名的，把署名文案记进清单条目，发布时并入 upload_info。
