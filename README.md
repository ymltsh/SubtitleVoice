<div align="center">

# 🎙️ GPT-SoVITS - SubtitleVoice

### 让番剧数据集制作，从数小时缩短到数分钟。

**一款面向 GPT-SoVITS 等语音模型的数据集制作工具。**

基于 **字幕切分 + AI 声纹检索 + 人工确认** 的工作流，让角色语音整理变得简单、高效。

> ⚠️ 本项目仍处于早期开发阶段，欢迎提出建议与贡献代码。

</div>

---

# 项目简介

训练 GPT-SoVITS、Fish Speech、CosyVoice 等语音模型时，真正耗费时间的并不是训练，而是**整理数据集**。

传统工作流程通常如下：

```
打开视频

↓

听一句

↓

暂停

↓

复制字幕

↓

FFmpeg 裁剪音频

↓

保存

↓

重复几百次……
```

整理一个角色的训练数据，往往需要数小时甚至数天。

**SubtitleVoice** 希望解决的正是这一问题。

新的工作流仅需：

```
导入剧集

↓

自动解析字幕

↓

自动切分对白

↓

选择少量角色参考素材

↓

AI 自动检索相同角色对白

↓

人工确认

↓

一键导出 GPT-SoVITS 数据集
```

SubtitleVoice 并不追求完全自动化。

而是让 AI 帮助完成最耗时的数据整理工作，由人工完成最后一步确认，兼顾效率与数据质量。

---

# 功能特性

## 📂 项目管理

- Workspace 工程管理
- 一个项目对应一个工程
- 支持多剧集管理
- 自动保存项目状态

---

## 🎬 素材管理

支持：

- MKV
- MP4
- ASS 字幕
- SRT 字幕

支持：

- 手动导入
- 自动匹配视频与字幕
- 提取 MKV 内封字幕

---

## 📝 字幕解析

基于 **pysubs2**

支持：

- ASS
- SRT

自动完成：

- 去除 ASS 样式标签
- 文本清洗
- 时间轴解析
- 写入 SQLite 数据库

---

## ✂️ 字幕切片

自动生成对白片段。

支持：

- 视频定位
- 文本浏览
- Keep / Delete 审核
- 连续同角色片段合并建议（目标 3–10 秒）
- 已采用合并段在 Clip 列表中以蓝色边框分组展示，可试听整段或取消采用
- 批量导出

---

## 🤖 Speaker Engine（角色识别）

SubtitleVoice 内置 Speaker Engine。

工作流程：

```
参考素材
      │
      ▼
ECAPA-TDNN
      │
      ▼
角色 Prototype
      │
      ▼
Embedding 检索
      │
      ▼
AI 自动预测角色
```

特点：

- 建立角色
- 添加参考素材
- 自动学习角色声音
- AI 检索全部对白
- 支持人工确认

所有 Embedding 仅计算一次。

后续检索直接读取缓存，速度极快。

---

## 📦 导出

已确认的连续训练片段会在导出时替代其组成的短 Clip；未合并的已保留 Clip 仍会正常导出。片段边界或角色归属发生改变时，对应合并建议会自动失效，避免导出过期音频范围。

支持一键导出：

```
Speaker/

000001.wav

000002.wav

...

train.list
```

可直接用于：

- GPT-SoVITS

未来计划支持：

- Fish Speech
- CosyVoice
- OpenVoice
- IndexTTS

---

# 工作流程

```
创建项目
      │
      ▼
导入视频 + 字幕
      │
      ▼
解析字幕
      │
      ▼
生成 Clip Database
      │
      ▼
人工审核（Keep / Delete）
      │
      ▼
生成 Clip Cache
      │
      ▼
建立角色
      │
      ▼
添加参考素材
      │
      ▼
生成 Prototype
      │
      ▼
AI 检索角色对白
      │
      ▼
人工确认角色
      │
      ▼
导出 GPT-SoVITS 数据集
```

---

# 项目架构

```
SubtitleVoice

├── 项目管理
│
├── 字幕解析
│
├── Clip Database
│
├── Speaker Engine
│      ├── Embedding Builder
│      ├── Prototype Builder
│      ├── Speaker Retrieval
│      └── Prediction
│
└── 导出模块
```

整个项目以 **Clip Database** 为核心。

所有模块均围绕 Clip 工作，而不是直接操作视频文件。

---

# 技术栈

## 后端

- Flask
- SQLite
- FFmpeg
- pysubs2

## 前端

- HTML
- CSS
- JavaScript

## AI

- ECAPA-TDNN
- Cosine Similarity Retrieval

---

# 当前功能

- [x] Workspace 工程管理
- [x] 视频/字幕导入
- [x] 字幕解析
- [x] Clip 数据库
- [x] 视频播放器审核
- [x] Keep / Delete 标记
- [x] GPT-SoVITS 数据导出
- [x] Speaker Engine
- [x] AI 声纹检索
- [x] AI 角色预测
- [x] 角色管理

---

# 开发计划

## V0.1

- [x] 工程管理
- [x] 字幕解析
- [x] Clip 审核
- [x] GPT-SoVITS 导出

---

## V0.2

- [x] Speaker Engine
- [x] 角色管理
- [x] Reference 管理
- [x] AI 检索
- [x] AI 角色预测
- [ ] 批量确认
- [ ] 后台异步检索
- [ ] Embedding 缓存优化

---

## V0.3

计划支持：

- 多种 Speaker Encoder

  - ECAPA
  - CAM++
  - ERes2Net

- 多模型数据集导出

  - GPT-SoVITS
  - Fish Speech
  - CosyVoice
  - OpenVoice
  - IndexTTS

- 自动角色推荐

- 批量项目处理

- 插件化 Speaker Engine

---

# 设计理念

SubtitleVoice 并不是一个「全自动角色识别工具」。

它更希望成为一款 **AI 数据整理工具**。

整个流程遵循：

```
AI 自动整理

↓

人工确认

↓

高质量数据集
```

AI 负责提高效率。

人工负责保证质量。

相比追求百分之百自动识别，更重要的是**大幅降低数据整理成本**。

---

# 为什么开发 SubtitleVoice？

最初只是想制作 GPT-SoVITS 数据集。

后来发现：

真正浪费时间的不是训练，而是：

- 听音频
- 找对白
- 裁剪音频
- 复制字幕
- 整理角色

这些工作几乎完全依赖人工。

于是便有了 SubtitleVoice。

希望把原本需要数小时甚至数天完成的数据整理工作，缩短到几十分钟。

---

# 项目截图



---

# 开源协议

MIT License

欢迎 Issue、Pull Request 与各种建议。

---

<div align="center">

### ⭐ 如果这个项目对你有所帮助，欢迎点一个 Star！

**让番剧数据集制作，从数小时缩短到数分钟。**

Made with ❤️ for the Voice AI Community.

</div>
