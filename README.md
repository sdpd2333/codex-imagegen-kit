# relay-imagegen for Codex

> 用你自己的 **OpenAI 兼容中转站（image 分组）**，在 Codex 对话里随时生成 / 修改图片。
>
> 一个 skill + 一个零第三方依赖的 Python 脚本。不用切网页，不用另开对话，边写代码边出图。

```text
你：画一张 16:9 的科技感 banner，深色背景、发光线条，不要文字
Codex：调用 $relay-imagegen → 请求中转站图片接口 → 保存 PNG → 回复预览
```

- 纯 Python 标准库，无需 `pip install`
- 支持文生图、参考图、改图、多尺寸/质量/数量
- 自动路由：先 `/v1/images/generations`，失败自动兜底 `/v1/responses`
- Windows（`install.ps1`）/ macOS / Linux（`install.sh`）
- 20 项离线自测，零成本验证脚本

---

## 为什么需要它

Codex 内置的 `image_generation`（`image_gen`）工具**只对 ChatGPT 官方登录的后端暴露**。
只要 provider 是自定义地址（中转站、Azure、OneAPI 代理等），客户端在构造请求时会把这个工具
从 `tools[]` 里去掉——所以 `image_generation = true`、`--enable image_generation` 都不起作用：

- 模型报 `built-in image_gen tool is not available in this session`
- 或者干脆用 PowerShell 画一个占位 PNG 交差

相关上游 issue（截至本文仍 open）：

- [openai/codex#24465 — Allow custom (non-ChatGPT) model providers to opt into the built-in image_generation hosted tool](https://github.com/openai/codex/issues/24465)
- [openai/codex#19133 — Windows/WSL 下内置 image_gen 不可用](https://github.com/openai/codex/issues/19133)

同时，很多中转站把画图能力放在**独立的 image 分组**：聊天分组不支持图片工具，图片请求要走
`/v1/images/generations`（或 `/v1/responses` 的 `image_generation` 工具），而 Codex 的 provider
只会走聊天链路，不会自动路由过去。

本项目用「skill + 脚本」补上这条链路：让模型能用一条 shell 命令直接调用 image 分组。

## 工作原理

```text
Codex 对话（任意轮次）
 └─ 模型判断需要画图，调用 $relay-imagegen 技能
     └─ 执行: python scripts/imagegen.py generate --prompt "..."
         └─ POST {BASE_URL}/images/generations（失败则 /responses）
             └─ 保存 PNG 到工作区 outputs/codex-images/
                 └─ 模型可 view_image 回看 → 回复 markdown 预览
```

因为产物是工作区内的文件，模型可以继续读图、改图，也可以直接把路径写进代码/文档。

## 功能

| 能力 | 说明 |
| --- | --- |
| 文生图 | `/v1/images/generations`，支持尺寸 / 质量 / 数量 |
| 参考图 | 把已有图片作为 `--ref` 传入（走 responses 路由） |
| 改图 | `/v1/images/edits`（multipart），失败自动回退 responses + 参考图 |
| 自动选模型 | 查 `/v1/models`，优先 `gpt-image-2.5`，其次 `gpt-image-2` |
| 路由兜底 | `images` 失败自动切 `responses`（需要对话模型） |
| 重试 | 429 / 5xx / 超时自动退避重试，默认 2 次 |
| 流式解析 | 支持 responses SSE，避免长请求被网关 524 |
| 只读体检 | `probe` 只查模型列表，不产生生图费用 |
| 结构化输出 | `--json`，方便脚本/批量调用 |

## 环境要求

- Codex CLI / Codex Desktop，且使用自定义 provider（中转站 / OpenAI 兼容网关）
- Python **3.9+**（只用标准库）
- 一个支持图片接口的中转站，以及 **image 分组**的 API key
  - 常见站点：`/v1/images/generations` + `gpt-image-2` / `gpt-image-2.5` 系列
  - 也有站点只支持 `/v1/responses` + `image_generation` 工具

## 安装

先 clone 到任意目录：

```bash
git clone https://github.com/<you>/relay-imagegen.git
cd relay-imagegen
```

### Windows（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会：

1. 安装 skill 到 `%USERPROFILE%\.codex\skills\relay-imagegen`
2. 创建配置目录 `%USERPROFILE%\.codex\imagegen\`
3. 如果本目录有 `config.env` 就复制它，否则复制 `config.example.env`（已存在的配置不会被覆盖）

### macOS / Linux

```bash
chmod +x install.sh
./install.sh
```

等价于把 `skill/` 复制到 `~/.codex/skills/relay-imagegen/`，并在 `~/.codex/imagegen/` 生成配置。

### 配置

编辑 `~/.codex/imagegen/config.env`（Windows：`%USERPROFILE%\.codex\imagegen\config.env`）：

```env
BASE_URL=https://your-relay.example.com/v1
API_KEY=sk-your-image-group-key
IMAGE_MODEL=auto
RESPONSES_MODEL=
ROUTE=auto
OUT_DIR=outputs/codex-images
```

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `BASE_URL` | ✅ | OpenAI 兼容根地址，通常以 `/v1` 结尾；只写 `https://host` 会自动补 `/v1` |
| `API_KEY` | ✅ | **图片分组**的 key；聊天 key 可能 403/404/无可用渠道 |
| `IMAGE_MODEL` | | `auto`（默认）或写死模型名 |
| `RESPONSES_MODEL` | | 仅 responses 路由用；留空则读 `~/.codex/config.toml` 顶层 `model` |
| `ROUTE` | | `auto`（默认）/ `images` / `responses` |
| `OUT_DIR` | | 输出目录，留空则 `./outputs/codex-images`；**必须在 Codex 工作区内** |

> 也支持环境变量覆盖：`IMAGEGEN_BASE_URL`、`IMAGEGEN_API_KEY`、`IMAGEGEN_IMAGE_MODEL`、
> `IMAGEGEN_RESPONSES_MODEL`、`IMAGEGEN_ROUTE`、`IMAGEGEN_OUT_DIR`、`IMAGEGEN_CONFIG`。

### 重启 Codex

技能目录在 Codex 启动时扫描，安装后需要重启 Codex。

## 使用

### 在对话里

重启后直接自然语言：

```text
画一张 16:9 的科技感 banner，深色背景、发光线条，不要文字
```

```text
帮我做一张极简 App 图标：蓝白配色，中心一个闪电，纯色背景
```

```text
把这张图的背景换成雪山，人物、衣服、姿势和构图都别动
```

```text
给这个项目画一个图标，放到 assets/，然后把 index.html 的 favicon 引用改掉
```

```text
刚才那张再来一版，改成赛博朋克霓虹风格
```

- 同一对话、任意轮次都能调用，**不需要单开生图对话**。
- 想更稳地触发，可以把 [`AGENTS.md.example`](AGENTS.md.example) 的内容追加到全局
  `~/.codex/AGENTS.md` 或项目根目录的 `AGENTS.md`。
- 回复里的图片用正斜杠路径（脚本输出的 markdown 已处理）。

### 命令行

```bash
# 只读体检，不花钱
python3 ~/.codex/skills/relay-imagegen/scripts/imagegen.py probe

# 生成
python3 ~/.codex/skills/relay-imagegen/scripts/imagegen.py generate \
  --prompt "一只坐在窗台上的橘猫，写实风格，柔和晨光" \
  --size 1024x1024 --quality low

# 改图
python3 ~/.codex/skills/relay-imagegen/scripts/imagegen.py edit \
  --prompt "只把背景换成雪山，其他保持不变" \
  --image ./input.png

# 机器可读输出
python3 ~/.codex/skills/relay-imagegen/scripts/imagegen.py generate \
  --prompt "极简几何海报" --json
```

Windows 上把 `python3` 换成 `py` 即可。

| 参数 | 说明 |
| --- | --- |
| `--size` | `1024x1024`（默认）/ `1024x1536`（竖）/ `1536x1024`（横）/ 站点支持的 `宽x高` |
| `--quality` | `low` / `medium` / `high` / `auto`；草稿 low，成品 medium/high |
| `--n` | 一次 1–10 张，默认 1 |
| `--out` | 指定精确输出文件路径 |
| `--ref` | 参考图（可重复；只走 responses 路由） |
| `--route` | `auto` / `images` / `responses` |
| `--json` | 输出 JSON（含文件路径和 markdown） |
| `--dry-run` | 只打印计划，不发请求 |

退出码：`0` 成功，`2` 配置/参数错误，`3` 无图片数据，`4` HTTP/网络错误，`5` 所有路由失败。

## 输出目录与 Codex 沙箱

Codex 执行 shell 命令跑在沙箱里，**默认只允许写工作区/临时目录**。所以：

- 默认输出到当前工作区的 `outputs/codex-images/`，模型可直接引用、`view_image`
- `OUT_DIR` 写成工作区外的绝对路径（如用户目录 Pictures）会报
  `sandbox: file access denied`
- 想放别处时，用 `--out` 指向工作区内路径

建议给项目 `.gitignore` 加一行 `outputs/`，避免素材被提交。

## 故障排查

| 现象 | 大概率原因 | 处理 |
| --- | --- | --- |
| `built-in image_gen tool is not available in this session` | 自定义 provider 下内置工具被关闭 | 用本 skill，别再调 feature flag |
| `401 invalid api key` | key 错/过期/带空格 | 重新复制图片分组 key |
| `403` / `无可用渠道` / `model not allowed` | token 不在 image 分组 | 新建「分组=image」的令牌 |
| `/images/generations` 404/405 | 站点不支持 Images API | `ROUTE=responses` + 填 `RESPONSES_MODEL` |
| `400 unknown tool: image_generation` | 该分组不支持 Responses 图片工具 | 改回 `ROUTE=images` |
| `524` / 超时 | 站点网关超时 | 降 quality、用 1024x1024、重试 |
| 返回成功但没有图片数据 | 站点返回结构非标准 | 加 `--json` 看原始响应 |
| 预览 `Unable to load file` | markdown 用了反斜杠 | 改成 `/` |
| `sandbox: file access denied` | 输出目录在工作区外 | 用工作区内相对路径 |
| 模型总是先试内置 `image_gen` | 触发不稳定 | 配 `AGENTS.md`（见上） |

## 测试

不花钱的离线自测（内置 mock 中转站，覆盖所有路由）：

```bash
python3 tests/test_offline.py     # Windows: py tests\test_offline.py
```

覆盖：probe、`/images/generations`、`/responses`（SSE）、multipart 改图、自动路由、
`--out`、`--dry-run`、错误 key、参考图路由，共 20 项检查。

## 安全

- **不要把 `config.env` 提交到 git**（仓库自带 `.gitignore` 已排除）。
- 本仓库不包含任何真实 API key、站点地址或个人信息。
- 建议为图片生成单独创建 key，便于限额/轮换；key 泄露后到中转站后台重置。
- 脚本只会以 `sk-abc...wxyz` 形式显示 key，不会打印完整值。

## 已知限制

- 不是 Codex 原生工具：触发依赖 skill 描述，偶尔需要点名 `$relay-imagegen`；配 `AGENTS.md` 可稳定。
- 依赖中转站实现：站点必须支持 `/v1/images/generations` 或 `/v1/responses` 图片工具。
- 改图能力取决于站点是否支持 `/v1/images/edits`；不支持时会回退 responses + 参考图。
- 出图是同步等待，通常 10–60 秒/张，速度取决于站点。
- 计费走中转站的图片分组，与 Codex 订阅无关。

## 目录结构

```text
.
├─ README.md
├─ install.ps1                 # Windows 安装
├─ install.sh                  # macOS / Linux 安装
├─ config.example.env          # 配置模板（无真实 key）
├─ AGENTS.md.example           # 可选的 AGENTS.md 指引片段
├─ .gitignore
├─ skill/
│  ├─ SKILL.md                 # Codex skill 说明
│  └─ scripts/
│     └─ imagegen.py           # 主脚本（标准库实现）
└─ tests/
   ├─ mock_relay.py            # 本地 mock 中转站
   └─ test_offline.py          # 20 项离线自测
```

## 参考

- [openai/codex#24465](https://github.com/openai/codex/issues/24465)、[#19133](https://github.com/openai/codex/issues/19133)：内置 image_generation 对自定义 provider 的限制
- [farion1231/cc-switch](https://github.com/farion1231/cc-switch)：中转站 / provider 切换
- 同类思路的社区项目：
  [YanRuiZhan/codex-image2-routing-guide](https://github.com/YanRuiZhan/codex-image2-routing-guide)、
  [ic0n311/codex-image-skill](https://github.com/ic0n311/codex-image-skill)、
  [fengfengzhidao/codex-image2-skill](https://github.com/fengfengzhidao/codex-image2-skill)
- Codex CLI 文档：<https://developers.openai.com/codex/cli/features>

---

## English TL;DR

A Codex skill + zero-dependency Python script that lets your agent generate or edit images through
your **own OpenAI-compatible relay's image group**, instead of the built-in `image_gen` tool — which
Codex only exposes for ChatGPT-auth providers
([#24465](https://github.com/openai/codex/issues/24465), [#19133](https://github.com/openai/codex/issues/19133)).

```bash
git clone https://github.com/<you>/relay-imagegen.git && cd relay-imagegen
./install.sh          # Windows: powershell -ExecutionPolicy Bypass -File .\install.ps1
# edit ~/.codex/imagegen/config.env (BASE_URL / API_KEY / IMAGE_MODEL)
python3 ~/.codex/skills/relay-imagegen/scripts/imagegen.py probe
```

Then restart Codex and simply ask it to draw something. Works mid-conversation, supports
text-to-image, reference images and edits, auto-routes between `/images/generations` and
`/responses`, and keeps output inside the workspace so the agent can read it back.

## License

MIT.
