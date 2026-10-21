# AstrBot-Wusound

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0-blue)](https://github.com/AstrBotDevs/AstrBot)
[![version](https://img.shields.io/badge/version-1.1.0-green)](https://github.com/unmia1/AstrBot-Wusound/releases)
[![license](https://img.shields.io/badge/license-MIT-orange)](LICENSE)

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件：调用 [悟声 Wusound](https://www.wusound.cn) 的 TTS 接口，
把文字合成**拟真人声**并以 QQ 语音消息发出。

既支持 `/语音 你好` 手动合成，也可以打开自动模式，让 AI 的回复**按设定概率**自动变成语音。

---

## 特性

- **拟真人声**：直接使用悟声的角色音色，不是传统机械 TTS
- **覆盖悟声 simple-generate 全部官方参数**：`preset`（音质档位）、`language`（语言）、`speechRate`（语速）、`model`（算法版本 v3.0/v4.0）、`srt`（字幕）、`stream`（流式）
- **手动 / 自动两种模式**：命令按需合成，或让 AI 回复自动转语音
- **可配置概率**：`auto_tts_probability` 控制「每条回复发语音的概率」，10~30 就能得到「偶尔蹦一句语音」的效果
- **本地缓存**：相同文字 + 相同音色 + 相同参数只调一次接口，固定话术不重复扣点数
- **自动纠错**：`voice_id` 填了名字而不是 ID 也能用，插件会从角色列表里自动映射
- **优雅降级**：没有 ffmpeg 时自动改走原生 base64 发送，不会整个报错

## 安装

### 方式一：WebUI 从仓库安装（推荐）

在 AstrBot WebUI 的「插件」页面选择从仓库安装，填入：

```
https://github.com/unmia1/AstrBot-Wusound
```

### 方式二：手动放置

```bash
cd AstrBot/data/plugins
git clone https://github.com/unmia1/AstrBot-Wusound.git
```

然后重启 AstrBot，或在 WebUI 插件页点「重载」。

### 装完必须做的两步

1. 到 https://www.wusound.cn/apiKey 创建一个 API Key（**只在创建时显示一次**，记得存好）
2. 在 WebUI 插件配置里填 `api_key`，再用 `/语音角色` 拿到 `voice_id` 填进去

## 命令

| 命令 | 说明 |
|---|---|
| `/语音 <文字>` | 把文字合成语音发出来（别名 `/tts`、`/念`、`/说话`） |
| `/语音角色` | 列出账号下可用的声音及 ID |
| `/语音概率 [0-100]` | 查看或设置自动语音概率，不带数字即查询（别名 `/自动语音概率`） |
| `/语音清理` | 清空本地语音缓存 |

## 配置项

配置文件：`AstrBot/data/config/<插件目录名>_config.json`（在 WebUI 里改更方便）

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `api_key` | string | 空 | 悟声 API Key，必填 |
| `voice_id` | string | 空 | 默认音色 ID，必填 |
| `preset` | string | balance | 音质/速度档位（见下方「官方参数」） |
| `language` | string | auto | 语言，auto 自动识别，可填 zh/en/ja 等 |
| `speech_rate` | float | 1.0 | 语速，0.5 ~ 2.0（接口 speechRate） |
| `model` | string | 空 | 算法版本，留空自动；可填 v3.0 / v4.0 |
| `srt` | bool | false | 是否生成字幕（接口 srt 参数） |
| `stream` | bool | false | 是否流式合成（接口 stream 参数） |
| `max_chars` | int | 200 | 单次合成最大字数，超长自动跳过 |
| `timeout` | int | 30 | 接口超时（秒） |
| `cache_enabled` | bool | true | 本地缓存开关 |
| `auto_tts` | bool | false | AI 回复自动转语音（总开关） |
| `auto_tts_probability` | int | 100 | 自动语音概率 0~100 |
| `auto_tts_groups` | string | 空 | 生效群号，逗号分隔，空 = 全部 |
| `auto_tts_keep_text` | bool | true | 自动语音时是否同时保留文字 |
| `send_mode` | string | component | 语音发送方式，发不出去时改 `raw` |

### 悟声 simple-generate 官方参数说明

`preset` 可选值（对应悟声官方的音质/速度档位）：

| 值 | 说明 |
|---|---|
| `fast_standard` | 快速 · 标准质量 |
| `fast_premium` | 快速 · 高级质量 |
| `fast_ultra` | 快速 · 极致质量 |
| `balance` | 均衡档（默认） |
| `stable` | 稳定档（牺牲表现力换稳定） |
| `expressive` | 表现力档 |

`model` 算法版本：`v3.0`（默认，稳定）、`v4.0`（更新，需账号/角色支持，支持多提示词）。留空由官方自动选择。

> 说明：悟声的情感/音高/音量控制**不是**通过 `emotion`/`pitch`/`volume` 参数实现（这些字段当前接口会静默忽略），
> 而是通过角色的「提示词（prompts）」实现。不同角色可能带不同的风格提示词，用 `/语音角色` 可以查看每个声音附带的风格。

### 关于概率

`auto_tts_probability` 是**每条回复独立掷骰子**：

- `100` 每条都发语音
- `30` 约三成发语音，其余保持纯文字 —— 人格「会说话但不啰嗦」的甜点区
- `0` 等价于关闭

短时间内的比例会有波动，消息量大时才收敛到设定值。除了改配置，也可以直接在群里发 `/语音概率 30` 在线调整，会写回配置文件，重启后仍生效。

## 实测可用的音色

| 名称 | voice_id | 特点 |
|---|---|---|
| Ruo Li | `12be752a-3c9a-4c59-9012-ceeaa0f460ba` | 慵懒微沙女声，适合傲娇 / 摆烂语气 |
| Airi | `c9767f39-49a9-4397-9452-d37f1bba6eda` | 活泼少女，日系腔 |
| 山田凉 | `eaf4727e-70e6-4973-9525-0d6c9f5e801b` | 淡定少女 |

音色会随账号权限和悟声更新变化，以 `/语音角色` 的实时返回为准。

## 工作原理

```
用户消息 ──► AstrBot ──► LLM 生成回复
                            │
                            ├─ auto_tts 关闭 ──► 纯文字发送
                            │
                            └─ auto_tts 开启
                                  ├─ 已是语音消息？跳过
                                  ├─ 超长 / 群不在白名单？跳过
                                  ├─ 概率未命中？保持文字
                                  └─ 命中 ──► 悟声 API ──► ffmpeg 转码 ──► 语音消息
```

接口调用：

- 合成：`POST https://v1.wusound.cn/api/tts/simple-generate`，body `{"voiceId": "...", "text": "...", "preset": "...", "language": "...", "speechRate": 1.0, "model": "...", "srt": false, "stream": false}`
- 角色列表：`GET https://v1.wusound.cn/api/voice`

## 依赖

- AstrBot >= 4.0
- Python 包：`httpx`（AstrBot 本体已自带，`requirements.txt` 里只是显式声明）
- **ffmpeg**（可选）：`send_mode = component` 时 AstrBot 会把音频转成 wav 再下发，这一步需要 `ffmpeg` 在 PATH 中。
  没有 ffmpeg 时插件自动降级为原生 base64 直接发 mp3，并打一条 warning 日志

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `401` / `AUTH_UNAUTHORIZED` | `api_key` 错误或已被删除 |
| `VOICE_NOT_FOUND` | `voice_id` 填错，用 `/语音角色` 复制准确 ID |
| 文字有了但没声音 | 当前平台不支持语音消息（需 OneBot v11 / NapCat 一类），或把 `send_mode` 改成 `raw` |
| 日志提示 ffmpeg not found | 装 ffmpeg 并加入 PATH，或忽略（已自动降级） |
| 自动模式下有时有语音有时没有 | `auto_tts_probability` 不是 100，属正常行为 |

## 计费说明

悟声按字符扣点数，免费账号每日签到可领额度。本插件已对「相同文字 + 相同音色 + 相同参数」做本地缓存，
重复内容不会再请求接口。自动模式在群聊里会持续消耗额度，建议配合 `auto_tts_groups` 限定范围，或把概率调低。

## 免责声明

本项目仅封装悟声的公开接口，语音内容由使用者自行负责。请遵守悟声的服务条款以及所在平台（QQ / 腾讯）的相关规定，
不要用于骚扰、冒充他人或任何违法用途。

## License

[MIT](LICENSE) © unmia1
