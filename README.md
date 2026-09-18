# GenAI2OpenAI

把上海科技大学 GenAI 对话平台（Web Chat）反向代理成 OpenAI 兼容 API 的服务，可在 opencode / Chatbox 等任意 OpenAI 客户端中使用。

## 项目渊源

本项目 fork 自 [ShanghaitechGeekPie/GenAI2OpenAI](https://github.com/ShanghaitechGeekPie/GenAI2OpenAI)（作者邮箱见文末）。**仅供本校师生个人学习使用**，请勿将服务暴露给他人。

### 原作者贡献

核心架构与协议逆向均由原作者完成：

- Flask 反向代理骨架：OpenAI 兼容的 `/v1/chat/completions`、`/v1/responses`、`/v1/models` 端点
- GenAI 上游 SSE 协议适配与模型别名映射机制
- 统一身份认证（CAS）自动登录（IDS 页面提取 salt、AES 密码加密、重定向链拿 token）
- 图片上传链路、工具调用的提示词兼容层雏形
- benchmark 与上下文长度测试工具

### 本分支改进（2026-09 新版平台适配）

原仓库更新止于 2026-05，平台 9 月升级后旧模型全部下线、协议字段变更。本分支完成适配与增强：

- **协议适配**：思维链字段更名兼容（`reasoning_content`）；上游错误包（`{"code":500,"errMsg":...}`）显式透出，不再静默吞掉；`data: [DONE]` 处理；修正 `chatInfo`（本轮提问）与 `messages`（历史）的拆分——旧版会把最后一条用户消息发两遍
- **模型表更新**：kimi-k3 / deepseek-v4.1 / glm-5.3-flash / qwen-3.8 / gpt-6-astra / gpt-5.6-sol/terra/luna 全量实测（benchmark 8/8 通过），详见[模型列表](docs/模型列表.md)
- **新能力**：联网搜索（`-search`）与深度思考（`-thinking` / `-nothink`）开关映射；`--chat-group-id` 会话归组，避免 API 请求刷爆网页版会话列表
- **工具调用增强**：Kimi 原生工具标记（`call tool=...` 特殊 token）解析，JSON / XML / 原生三种格式合并去重；opencode 多轮工具循环实测通过
- **工程化**：标准 src 布局重构（单文件 1551 行 → `src/genai2openai/` 模块包）；`Settings` 配置对象；`.env` 凭据文件支持；日志文件双写；真实 token usage（接入上游 `totalTokens`）
- **调试工具链**：`tools/probe_upstream.py`（上游原始报文探测，平台再升级时先跑它）、`tools/smoke_test.py`（冒烟回归）、`tools/test_tool_call.py`（工具调用单测）

## 写在前面

> 本节为原作者 README 原文，保留作纪念。

写这个项目的时候, GenAI平台还是比较好的, 当时我也没申请API. 虽然GenAI平台实际上烂完了,但胜在免费,自己用用还是可以的. 后来申请了API, 只能说难兄难弟,没比公开的好用多少. 现在我已经不缺token了,所以维护这个repo的动力很低很低. 我建议大家玩一玩genai就够了, 最多是一些高通量的任务用一下, 除此之外别折腾了. 嫌贵可以去买那些中转服务, 一个平台可以用所有模型的那种,其实是挺好用的. 最近我看到大家对于这个项目还是挺热情的,所以参考其他人的工作补全了很多很多的功能. 当然距离一个“能用”的API还有距离, 当然这个距离我也无能为力了. 一想当GenAI刚出的时候我还是非常有信心的, Yu老师亲口说这个平台很吊, 但是我只能说烂完了(除了免费). 希望大家能利用AI改善自己的生活. 最后如果不出意外这个项目基本不会再更新了,如果有本科生小朋友愿意接手的话可以联系我(issue里直接提也可以). ciallo (∠·ω )⌒★


## 项目简介

GenAI 是一个基于 Flask 的聊天机器人接口服务，兼容 OpenAI 的聊天完成接口，利用上海科技大学的 GenAI API 进行智能对话。项目通过封装 GenAI API，支持思维链、流式响应和普通响应，从而方便客户端集成与调用。该项目适合开发具有中文支持及本地化需求的智能聊天机器人应用。

**OpenAI Compatible 功能对比**

| 能力项                                    | OpenAI 官方接口 | 本项目实现情况 | 说明                                                       |
| ----------------------------------------- | --------------- | -------------- | ---------------------------------------------------------- |
| `POST /v1/chat/completions`               | ✅ 原生支持     | ✅ 已兼容      | 入参/出参保持 OpenAI 风格，转发至 GenAI 上游               |
| `POST /v1/responses`                      | ✅ 原生支持     | ✅ 最小兼容    | 支持基础 `input`、流式与非流式输出                         |
| 流式输出（SSE）                           | ✅              | ✅             | 支持 Chat Completions 与 Responses 两条链路                |
| 非流式输出                                | ✅              | ✅             | 统一聚合上游增量后返回标准 JSON                            |
| 推理内容字段（reasoning）                 | 部分模型支持    | ✅ 兼容输出    | 通过 `reasoning_content` / `response.reasoning.delta` 暴露 |
| Tool Calling（`tools/tool_choice`）       | ✅ 原生         | ✅ 提示词兼容  | 上游无原生工具调用，本项目做 JSON 约定与本地解析           |
| 旧版函数调用（`functions/function_call`） | 已逐步废弃      | ✅ 兼容        | 自动转换为 `tools/tool_choice` 语义                        |
| 图片输入（Vision）                        | ✅              | ✅             | 服务端自动上传图片并注入 `imageUrl/width/height`（本地/Azure 模型均已实测） |
| 模型列表接口（`GET /v1/models`）          | ✅              | ✅             | 返回本项目映射后的可用模型列表                             |
| 认证头兼容（Bearer/API Key）              | ✅              | ✅             | 支持 `Authorization`、`X-Access-Token`、`api-key` 等       |
| 联网搜索 / 深度思考                        | 无原生字段      | ✅ 已映射      | 模型名后缀 `-search` / `-thinking` / `-nothink` 或请求体 `net_go` / `thinking` 字段 |
| Token 用量（usage）                        | ✅              | ✅ 部分        | `total_tokens` 为上游真实值；`prompt`/`completion` 分项为估算 |

### Agent Tool 生态测试

| 客户端 / Agent | 兼容性 |
|---|---|
| opencode | ✅ 实测通过（2026-09-17）：多轮工具循环正常。模型需配置 `tool_call: true`，见下文"接入 opencode" |
| Chatbox | ✅ 完美支持 | 
| Kilo Code | ❌ 不支持(模型限制) |

## 快速开始

```bash
# 1. 拿代码、装环境（需要 Python 3.11+ 和 uv）
git clone https://github.com/quantumxiaol/GenAI2OpenAI && cd GenAI2OpenAI
uv sync

# 2. 配置凭据（学号@密码，用于自动登录；也可浏览器抓 token，见下文 Token 获取）
cp .env.example .env   # 然后编辑 .env 填入 GENAI_ACCOUNT=学号@密码

# 3. 启动（默认端口 11435；建议带会话归组，避免网页版会话列表被 API 请求刷屏）
uv run genai2openai --chat-group-id ApiProxy

# 4. 冒烟验证（8 项全 PASS 即一切正常）
uv run python tools/smoke_test.py
```

客户端接入：

- **opencode**：见下文[接入 opencode](#接入-opencode)，有覆盖全部模型的完整配置
- **任意 OpenAI 客户端**：`base_url = http://127.0.0.1:11435/v1`，模型如 `deepseek-v4.1`、`kimi-k3-search`

平台再次升级导致失效时，先跑 `uv run tools/probe_upstream.py` 看上游原始报文，再对照 `docs/模型列表.md` 里的协议说明排查。

## 安装与运行

### 环境要求

- Python 3.11 及以上版本
- 依赖包见 `pyproject.toml`，推荐使用 uv 管理环境。

### 启动服务

```bash
uv run genai2openai [--token <token>] [--account <student_id@password>] [--upload-token <upload_token>] [--log-level INFO] [--port 11435]
```

端口默认 11435（Ollama 端口后一位，避开 macOS AirPlay 对 5000 的占用）。服务将在本地 `0.0.0.0:11435` 端口启动。`uv run main.py` 与 `uv run python -m genai2openai` 是等价的兼容入口。

### 项目结构

```
src/genai2openai/       # 主包（标准 SRC 布局）
├── cli.py              # 命令行入口、启动时 token 流程
├── config.py           # 上游地址、请求头与运行时配置
├── registry.py         # 模型映射表与远端模型发现
├── auth.py             # token 缓存 / 校验 / 请求头提取
├── cas.py              # 统一身份认证（CAS）自动登录
├── images.py           # 图片上传与缓存
├── messages.py         # OpenAI 消息到上游格式的归一化
├── tool_calling.py     # 工具调用兼容层（提示词 + 本地解析）
├── upstream.py         # GenAI SSE 上游客户端
└── routes/             # Flask 路由：chat / responses / meta
tools/                  # 客户端工具（benchmark、上下文长度测试），不属于主包
```

可选参数：

- `--token` ：若不在启动时提供，可由客户端在每次请求中通过 `Authorization: Bearer <token>` 或其他兼容 API key 请求头传递。
- `--account`：上海科技大学统一身份认证账号，格式为 `学号@密码`。当未提供 `--token` 时，服务启动时会自动登录并获取 GenAI token。
- `--upload-token`：图片上传接口 `token` 请求头值（默认内置项目当前可用值）。
- `--log-level`：控制台日志级别，支持 `DEBUG / INFO / WARNING / ERROR / CRITICAL`，默认 `INFO`。
- `--port`：监听端口，默认 `11435`。
- `--host`：监听地址，默认 `127.0.0.1`（仅本机）；局域网/反向代理场景用 `--host 0.0.0.0` 显式放开。
- `--api-key`：代理自身的 API 鉴权密钥，设置后 `/v1/*` 请求需携带 `Authorization: Bearer <key>`（兼容 `X-API-Key` / `api-key` 头）；`/health` 与 CORS 预检放行。**注意**：开启后客户端的 Bearer 头只用于代理鉴权，不再作为上游 token 透传，上游凭据由服务端启动配置（`--account`/`--token`/`.env`）提供。
- `--chat-group-id`：固定上游会话分组 ID，所有 API 请求归入网页版同一条会话（默认不发送，每次请求各自建会话）。

以上参数都可以写进项目根目录的 `.env` 文件（已 gitignore，参考 `.env.example`），避免密码出现在命令行和进程列表中：

```bash
GENAI_ACCOUNT=学号@密码
GENAI_TOKEN=eyJ...
GENAI_UPLOAD_TOKEN=
GENAI_PORT=11435
GENAI_HOST=127.0.0.1
GENAI_CHAT_GROUP_ID=ApiProxy
GENAI_API_KEY=   # 通过 frp 等暴露到本机以外时务必设置
```

命令行参数优先于 `.env`。

### 对外暴露（frp + 鉴权）

要把服务暴露到本机以外（如 frp 穿透），推荐组合：

1. `.env` 设置 `GENAI_API_KEY=<随机长密钥>`（可用 `openssl rand -hex 24` 生成）；
2. frp 的 frpc 与本服同机时，保持默认 `--host 127.0.0.1` 即可（frpc 会代连本机回环端口），不要把服务直接绑到公网网卡；
3. 客户端把 `apiKey` 配成同一个密钥（opencode 配置里的 `"apiKey": "unused"` 换成真实密钥）。

## 功能和用法

- 兼容 OpenAI API，支持 `POST /v1/chat/completions`、`POST /v1/responses`接口，实现智能聊天功能。
- 支持流式（stream）及非流式响应，方便高效地获取 AI 回复。
- `POST /v1/chat/completions` 支持基于提示词工程和 JSON 解析的 OpenAI `tools`/`tool_choice` 兼容工具调用，也兼容旧版 `functions`/`function_call` 入参。
- `POST /v1/chat/completions` 支持图片输入（服务端自动上传到 GenAI 图片服务后再发起对话），本地与 Azure 模型均已验证可用。
- 提供 `/v1/models` 接口列出可用模型，如 `kimi-k3`、`deepseek-v4.1`、`gpt-6-astra`、`glm-5.3-flash` 等。
- 内置 `/health` 健康检查接口，用于服务状态监测。

### 支持模型

| 模型 id         | 路由                   | 思维链                 | first_token_delay | 输出速度        |
| --------------- | ---------------------- | ---------------------- | ----------------- | --------------- |
| kimi-k3         | 本地（不限量）         | ✅ `reasoning_content` | 0.489s            | 11.57 tokens/s  |
| deepseek-v4.1   | 本地（不限量）         | 未知                   | 0.117s            | 449.95 tokens/s |
| glm-5.3-flash   | 本地（不限量）         | 未知                   | 0.198s            | 403.89 tokens/s |
| qwen-3.8        | 本地（不限量）         | 未知                   | 0.214s            | 411.43 tokens/s |
| gpt-6-astra     | Azure（100万 tokens/月）| 隐藏                  | 8.420s            | 75.30 tokens/s  |
| gpt-5.6-sol     | Azure（100万 tokens/月）| 隐藏                  | 3.412s            | 122.54 tokens/s |
| gpt-5.6-terra   | Azure（100万 tokens/月）| 隐藏                  | 4.784s            | 172.85 tokens/s |
| gpt-5.6-luna    | Azure（100万 tokens/月）| 隐藏                  | 4.100s            | 391.74 tokens/s |

兼容层同时兼容上游请求名和实际模型名，详见[模型列表](docs/模型列表.md)。
旧版模型（deepseek-v3/r1、gpt-5.5 等）已于 2026 年 9 月平台升级后全部下线。
性能数据由 `tools/benchmark_models.py` 实测（约 300 字中文生成任务），更新于 `2026-09-16`。kimi-k3 输出速度低是因为思维链较长，正文生成速度实际更快。

### 测试模型上下文长度

项目内置 `context_length_tester` skill，可用于测试模型的实际上下文处理能力：

```bash
# 大海捞针测试（推荐）
uv run tools/skills/context_length_tester/context_length_tester.py --model kimi-k3

# 快速探测 API 上限
uv run tools/skills/context_length_tester/context_length_tester.py --model kimi-k3 --mode probe
```

测试方法采用**大海捞针法**（Needle in a Haystack）：在长文本中间插入关键信息，验证模型能否准确检索。这比简单的二分查找更能反映模型的真实上下文处理能力。

**注意**：Azure GPT 模型有严格的额度限制，无法进行上下文长度测试。建议参考各模型的官方文档了解其标称上下文长度。

### 工具调用兼容

上游 GenAI API 没有原生 tool calling 能力。本项目在 Chat Completions 接口中通过系统提示词要求模型输出工具调用 JSON，并在本地解析为 OpenAI 兼容的 `tool_calls`：

```json
{
  "model": "gpt-6-astra",
  "messages": [{ "role": "user", "content": "上海今天适合带伞吗？" }],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "查询城市天气",
        "parameters": {
          "type": "object",
          "properties": {
            "city": { "type": "string" }
          },
          "required": ["city"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

如果模型决定调用工具，非流式响应会返回 `finish_reason: "tool_calls"` 和 `message.tool_calls`。流式请求也会返回兼容的 `tool_calls` chunk，但为了可靠解析 JSON，带工具的流式请求会先在服务端收集完整上游输出后再发送结果。

兼容性补充：

- 三种格式全部识别并合并：提示词约定的 **JSON**、XML 标签块 `<tool_call>...</tool_call>`、以及 **Kimi 原生工具标记**（`call tool="..."` 特殊 token 序列，K3 会无视提示词直接输出它）；同一轮混用多种格式也不会丢调用，同名同参数的重复调用自动去重。
- 实测提示：Kimi-K3 思考链长、且偶尔声称"没有工具可用"（幻觉，实际调用已成功），agent 场景更推荐 `deepseek-v4.1`。
- 当模型输出多个调用时，会按顺序解析为多个 `tool_calls`。

### 接入 opencode

opencode 对自定义 provider 的模型默认不启用工具调用，需要在模型配置里显式声明 `tool_call: true`（配置位置：项目根目录 `opencode.json`，或全局 `~/.config/opencode/opencode.json`）。

接入当前全部 8 个模型（含联网/深思变体）的完整配置：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "genai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "GenAI",
      "options": {
        "baseURL": "http://127.0.0.1:11435/v1",
        "apiKey": "unused"
      },
      "models": {
        "kimi-k3":                { "name": "Kimi K3",             "tool_call": true, "reasoning": true, "limit": { "context": 131072, "output": 16384 } },
        "kimi-k3-search":         { "name": "Kimi K3 (联网)",      "tool_call": true, "reasoning": true, "limit": { "context": 131072, "output": 16384 } },
        "deepseek-v4.1":          { "name": "DeepSeek V4.1",       "tool_call": true, "limit": { "context": 131072, "output": 16384 } },
        "deepseek-v4.1-search":   { "name": "DeepSeek V4.1 (联网)","tool_call": true, "limit": { "context": 131072, "output": 16384 } },
        "deepseek-v4.1-thinking": { "name": "DeepSeek V4.1 (深思)","tool_call": true, "reasoning": true, "limit": { "context": 131072, "output": 16384 } },
        "glm-5.3-flash":          { "name": "GLM 5.3 Flash",       "tool_call": true, "limit": { "context": 131072, "output": 16384 } },
        "glm-5.3-flash-search":   { "name": "GLM 5.3 Flash (联网)","tool_call": true, "limit": { "context": 131072, "output": 16384 } },
        "qwen-3.8":               { "name": "Qwen 3.8",            "tool_call": true, "limit": { "context": 131072, "output": 16384 } },
        "qwen-3.8-search":        { "name": "Qwen 3.8 (联网)",     "tool_call": true, "limit": { "context": 131072, "output": 16384 } },
        "gpt-6-astra":            { "name": "GPT-6 Astra",         "tool_call": true, "limit": { "context": 262144, "output": 16384 } },
        "gpt-5.6-sol":            { "name": "GPT-5.6 Sol",         "tool_call": true, "limit": { "context": 262144, "output": 16384 } },
        "gpt-5.6-terra":          { "name": "GPT-5.6 Terra",       "tool_call": true, "limit": { "context": 262144, "output": 16384 } },
        "gpt-5.6-luna":           { "name": "GPT-5.6 Luna",        "tool_call": true, "limit": { "context": 262144, "output": 16384 } }
      }
    }
  },
  "model": "genai/deepseek-v4.1"
}
```

注意：

- `baseURL` 必须是 `http://`（本服务不跑 TLS），末尾带 `/v1`。
- 服务以 `--account` / `.env` 账号模式启动时 `apiKey` 可任意填；服务端设置了 `GENAI_API_KEY`（鉴权模式）时，`apiKey` 必须填同一个密钥。
- 模型后缀 `-search` / `-thinking` / `-nothink` 直接当独立模型配；`reasoning: true` 让 opencode 把思维链渲染成思考块。
- `limit.context` 是估算值（自定义模型没有元数据），实测后可用 `tools/skills/context_length_tester` 校准。
- 模型选择建议：agent 任务主力 `deepseek-v4.1`（快、不话痨）；重推理用 `kimi-k3`（强制思考，慢但深）；GPT 系有 100 万 tokens/月额度，留给本地模型解决不了的硬任务。

### 图片输入

`/v1/chat/completions` 支持 OpenAI 常见多模态消息格式：

- `type: "image_url"` + `image_url.url`（可传公网图片 URL）
- `type: "input_image"` + `image_url.url` / `url`
- 支持 `data:image/...;base64,...` 的 data URL

服务端行为：

1. 从最后一条包含图片的 user message 提取图片输入。
2. 自动调用 GenAI 图片上传接口 `https://genaipic.shanghaitech.edu.cn//sys/common/upload`。
3. 将返回的 `imageUrl`、`width`、`height` 透传到上游对话请求。

2026-09-18 实测：本地部署模型（kimi-k3、deepseek-v4.1）与 Azure 路由模型均可正常读图，`imageUrl` 与 `imageUrls[]` 两种字段形式上游都接受。

注意：带图片的请求不会携带 `chatGroupId`（上游对该组合会报图片加载错误），因此图片问答不会归入归组会话。

示例：

```bash
curl http://127.0.0.1:11435/v1/chat/completions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-6-astra",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "这张图里有什么？"},
          {
            "type": "image_url",
            "image_url": {
              "url": "https://example.com/demo.jpg"
            }
          }
        ]
      }
    ]
  }'
```

### 联网搜索与深度思考

上游的联网搜索（`netGo`）和深度思考（`thinking`）开关通过**模型名后缀**或**请求体显式字段**开启（显式字段优先）：

| 功能 | 模型名后缀 | 请求体字段 |
| ---- | ---------- | ---------- |
| 联网搜索 | `kimi-k3-search` | `"net_go": true` |
| 深度思考开 | `kimi-k3-thinking` | `"thinking": true` |
| 深度思考关 | `kimi-k3-nothink` | `"thinking": false` |

- 后缀可组合，如 `kimi-k3-search-thinking`；适用于全部模型，大小写不敏感。
- 不指定时跟随上游默认。注意 `thinking` 开关的实际效果取决于模型：DeepSeek-V4.1、Kimi-K3 等都可用 `-thinking` 开启思维链（上游 2026-09-18 起 K3 默认不再强制思考，想快就直接用默认形态）。
- 联网检索结果直接融入回答文本（含引用标记），无额外返回字段。

### 会话记录

默认情况下，每次 API 请求都会以最后一条用户消息为标题，在 GenAI 网页版会话列表中创建一条新会话。如需避免刷屏，启动时加 `--chat-group-id <固定串>`（或 `.env` 中配置 `GENAI_CHAT_GROUP_ID`），所有 API 请求会归入网页版同一条会话。

## Token 获取

1. 首先前往[GenAI 对话平台](https://genai.shanghaitech.edu.cn/dashboard/analysis)
2. 打开浏览器开发者工具，随便发送一条消息，捕获名为`chat`的请求
3. 复制请求标头中的`x-access-token`字段，即为`<token>`

服务启动时可通过 `--token <token>` 设置默认 GenAI token；也可通过 `--account <学号@密码>` 在启动时自动登录获取 token（成功后会缓存到当前目录的 `.genai_token_cache`，下次启动自动复用）。也可以单独运行登录工具仅获取 token：

```bash
uv run genai2openai-login --credential '学号@密码'
```

客户端也可以通过传统的 API key 传递 token，此时请求级 key 会覆盖启动参数中的默认 token，并作为上游 GenAI 的 `X-Access-Token` 使用。

支持的请求头：

- `Authorization: Bearer <token>`（推荐，兼容 OpenAI SDK）
- `X-Access-Token: <token>`
- `api-key: <token>`
- `X-API-Key: <token>`

示例：

```bash
curl http://127.0.0.1:11435/v1/chat/completions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"kimi-k3","messages":[{"role":"user","content":"你好"}]}'
```

![图片说明](images/chrome.png)

对于图片功能, 需要捕获`upload` API, 提取请求 header 中的 `token` ,然后通过 `--upload-token` 传入.

## 开发与贡献指南

- 欢迎 fork 并提交 PR，改进功能或修复 bug。
- 请遵守项目代码风格，代码中请添加必要注释。
- 贡献代码时建议附带测试，确保功能完整性。
- 遇到问题可通过 issue 反馈。

## 联系方式与许可

- 原作者联系邮箱：arnoliu@shanghaitech.edu.cn（上游仓库：[ShanghaitechGeekPie/GenAI2OpenAI](https://github.com/ShanghaitechGeekPie/GenAI2OpenAI)）
- 本分支（2026-09 新版平台适配）的 issue 请提在本 fork 仓库。
- 本项目采用 MIT 许可证（保留原作者版权与许可声明），详见 LICENSE 文件。
