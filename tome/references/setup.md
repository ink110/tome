# tome 配置与依赖

所有路径、模型、媒体存储都由 `~/.config/tome/config.json` 决定。
第一次用先跑向导，再跑预检：

```bash
python3 ~/.claude/skills/tome/scripts/setup.py
python3 ~/.claude/skills/tome/scripts/doctor.py
```

`doctor.py` 只检查、不安装；缺什么会直接告诉你怎么补。

## 三种模型档位

| 档位 | 怎么工作 | 需要什么 | 适合 |
| --- | --- | --- | --- |
| `claude`（默认） | 脚本只落原文，蒸馏/打标签由 Claude Code 直接完成 | 无 | 第一次用、几十篇以内、不想配 API Key |
| `openai` | 脚本调用任意 OpenAI 兼容接口 | `base_url` + `model` + 环境变量里的 API Key | 几百篇批量、成本可控 |
| `ollama` | 脚本调用本地 Ollama | 已启动的 Ollama + 拉好的模型 | 内容不出本机 |

切换：

```bash
python3 ~/.claude/skills/tome/scripts/setup.py --provider openai \
  --base-url https://api.deepseek.com/v1 --model deepseek-chat
export TOME_API_KEY=sk-...
```

本地模型显存对照（Ollama）：

| 模型 | 显存/内存 | 说明 |
| --- | --- | --- |
| `qwen2.5:7b` | ~8GB | 能跑，长文质量一般 |
| `qwen2.5:14b` | ~16GB | 默认推荐，性价比最好 |
| `qwen2.5:32b` | ~32GB | 质量最好，长口播稿更稳 |

`claude` 档位下，需要脚本批量处理的两个入口会改变行为：

- 公众号：`wechat_distill.py --plan` 导出任务 → Claude 写结果 → `--apply` 回写。
- B站：先 `crawl_collection.py --raw-only`，再由 Claude 逐集生成结构化稿。

## 两种信息源模式

| 模式 | 目录 | 链接 | 适合 |
| --- | --- | --- | --- |
| `folder`（默认） | `<工作目录>/<专题>/` | 普通 Markdown 链接 | 没有 Obsidian，或不想被库结构绑住 |
| `obsidian` | `<库根>/topics/<专题>/` | `[[双链]]` + `index.md` | 已经在用 Obsidian 管知识库 |

两者目录结构完全一样（`00-收集` / `10-画像` / `20-输出` / `assets`），切换只影响链接写法和根目录。

```bash
# 切换成 Obsidian
python3 ~/.claude/skills/tome/scripts/setup.py --vault ~/Documents/我的库
```

## 媒体存储：Eagle 可选

默认把公众号图片 / GIF / 视频存到 `<专题>/assets/`，用标准 Markdown 引用，**不需要 Eagle**。
开启 Eagle 后会额外上传一份，并在笔记里保留 `eagle://` 溯源链接。

```bash
# 编辑 ~/.config/tome/config.json
"media": { "eagle": { "enabled": true, "folder": "tome素材" } }
```

## B站 cookie

只影响 B站字幕和播放数据，公众号流程不需要。

1. Chrome / Edge 安装 **Get cookies.txt LOCALLY**
   （[Chrome 商店](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)）。
2. 登录 bilibili.com。
3. 点扩展 → Export → 选 **Netscape** 格式 → 保存为 `~/.bilibili-cookies.txt`。

必须导出**完整** cookie（SESSDATA、DedeUserID、bili_ticket 等都要），只拷 SESSDATA 会返回 -101。
这个文件不要提交到任何仓库。

## 环境变量（覆盖配置文件）

| 变量 | 作用 |
| --- | --- |
| `TOME_CONFIG` | 指定配置文件路径（测试多套配置时用） |
| `TOME_PROVIDER` | `claude` / `openai` / `ollama` |
| `TOME_MODEL` | 覆盖 Ollama 模型名 |
| `TOME_BASE_URL` / `TOME_API_KEY` | OpenAI 兼容接口地址与密钥 |
| `TOME_WORKSPACE` / `TOME_VAULT` | 覆盖信息源根目录 |

密钥只从环境变量读，不写进配置文件，更不写进仓库。
