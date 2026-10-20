---
name: relay-imagegen
description: Generate or edit raster images through the user's own OpenAI-compatible image relay / 中转站 image 分组 (text-to-image, reference-image edit, size & quality control). Use whenever the user asks to 画图, 生图, 出图, 改图, generate/draw/create/edit an image, logo, icon, illustration, cover, avatar, poster, or wallpaper. Also use it when the built-in image_gen tool reports "not available in this session" — this skill is the reliable path for relay/proxy providers.
---

# relay-imagegen

通过用户自己的中转站「图片分组」API 生成或修改图片。Codex 内置的 `image_gen`
工具在「自定义 provider / 中转站」下不会被暴露（客户端硬门槛），所以不要反复尝试
内置工具、也不要回答「无法生成图片」——直接用本技能。

## 配置（一次性，由用户完成）

配置文件：`%USERPROFILE%\.codex\imagegen\config.env`

```env
BASE_URL=https://你的中转站/v1
API_KEY=sk-图片分组的key
IMAGE_MODEL=auto
RESPONSES_MODEL=
ROUTE=auto
OUT_DIR=outputs/codex-images
```

- `API_KEY` 必须是**图片分组**的 key；聊天那个 key 不一定能在图片分组用。
- `IMAGE_MODEL=auto` 时脚本会先查 `/v1/models`，优先 `gpt-image-2.5`，否则 `gpt-image-2`。
- `OUT_DIR` 用**当前工作区的相对路径**（Codex shell 沙箱只允许写工作区；写到
  `C:\Users\...\Pictures` 会报 `sandbox: file access denied`）。
- 不要把 key 写进回复、日志或提交到 git；不要在回复里展示 key。

## 脚本

`scripts/imagegen.py`，只依赖 Python 标准库。假设技能安装在
`%USERPROFILE%\.codex\skills\relay-imagegen`，命令为：

```powershell
py "$env:USERPROFILE\.codex\skills\relay-imagegen\scripts\imagegen.py" <子命令> ...
```

如果技能装在别处，用本 SKILL.md 同目录下的 `scripts/imagegen.py` 绝对路径。

## 首次使用先 probe（不花钱）

```powershell
py "$env:USERPROFILE\.codex\skills\relay-imagegen\scripts\imagegen.py" probe
```

确认：配置文件存在、base url 正确、key 打了码、`/v1/models` 能看到
`gpt-image-*`。若 probe 失败，把 stderr 的原始错误告诉用户，不要猜。

## 生成图片

```powershell
py "$env:USERPROFILE\.codex\skills\relay-imagegen\scripts\imagegen.py" generate `
  --prompt "<详细提示词>" `
  --size 1024x1024 `
  --quality medium
```

可选参数：

| 参数 | 说明 |
| --- | --- |
| `--size` | `1024x1024` 方图（默认）、`1024x1536` 竖版、`1536x1024` 横版，或中转站支持的自定义 `宽x高` |
| `--quality` | `low` / `medium` / `high`；草稿用 low，成品用 medium/high |
| `--n` | 一次 1–10 张，默认 1 |
| `--out` | 指定精确输出文件路径 |
| `--ref` | 参考图（可重复），走 responses 路由做风格/构图参考 |
| `--route` | `auto`（默认，先 images 再 responses）/ `images` / `responses` |
| `--json` | 输出结构化 JSON（含文件路径和 markdown） |
| `--dry-run` | 只打印计划，不发请求 |

`auto` 的含义：先 POST `{BASE_URL}/images/generations`；404/400 时自动改用
`{BASE_URL}/responses` + `image_generation` 工具 + `RESPONSES_MODEL`（不填则读
`~/.codex/config.toml` 顶层 `model`）。

## 改图 / 图生图

```powershell
py "$env:USERPROFILE\.codex\skills\relay-imagegen\scripts\imagegen.py" edit `
  --prompt "只把背景换成雪山，人物、服装、姿势、构图保持不变" `
  --image "C:\path\to\input.png" `
  --size 1024x1024 `
  --quality medium
```

`edit` 先走 `/v1/images/edits`（multipart），失败后自动用 responses 路由 + 参考图兜底。

## 回复用户的规则

1. 生成成功后，脚本会打印 `Saved image(s):` 与 markdown 预览。
2. 回复里用**正斜杠**路径展示图片，否则 Codex 预览面板可能显示 Unable to load file：

   ```markdown
   ![imagegen](C:/Users/xxx/project/outputs/codex-images/relay-image-20261020-213000-01.png)
   ```

3. 同时给出绝对路径和使用的路由（images / responses）。
4. 失败时只报告一行原因 + 原始错误，不要伪造图片、不要用占位 PNG 冒充。
5. 用户说「再来一张/换个风格/把背景换掉」时，带上上一张图作为 `--ref` 或 `--image`，
   不要凭空重画。
6. 本技能在任意对话轮次都能调用，不需要新开会话；生成物落在工作区内，可直接被代码引用。

## 提示词习惯

- 方图/头像/图标 → `1024x1024`；竖版壁纸/海报 → `1024x1536`；横版 banner/封面 → `1536x1024`。
- 提示词写清：主体、风格、构图、光线、配色、是否要文字（默认不要文字）。
- 需要精确文字时，明确写出引号内文字，并提醒用户生图模型对文字仍可能出错。
