# 微信「图片消息」（无正文纯图帖）处理方案

适用：单篇抓取报「未取到正文（js_content 为空）」exit 1，但链接在浏览器能正常打开。
这类帖子是小红书式纯图卡片（1 张封面 + N 张图），页面没有 `js_content`，内容全在 JS 变量里。

## 判断

```bash
curl -sL -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" <URL> -o /tmp/wx.html
grep -c 'picture_page_info_list' /tmp/wx.html   # >0 即图片消息
```

## 页面结构（实测 2026-10-28）

- 数据在全文 JS `window.cgiDataNew = {...}`（JS 对象字面量，**不是**合法 JSON，别直接 json.loads）。
- 图列表：`picture_page_info_list: [...]`，每项 `cdn_url: '...'` 即原图（JS 单引号字符串，括号配对截取数组）。
- **每 3 项左右夹一个 `http://318.wxapp.tc.qq.com/.../stodownload?...` 链接**：是 live photo 视频版，直接下载会 400，**跳过**（静态图已够）。
- 元数据：`cgiDataNew` 里的 `nick_name`（公众号名）、`title`、`create_time`（`'2026-10-22 15:00'` 格式）、`desc`（通常只有 #话题# 标签，没有正文）。

## 处理流程（无现成脚本，按此手动执行）

1. **解析**：curl 下载页面（带 UA）→ 正则取 `picture_page_info_list` 数组 → 提取全部 `cdn_url`，过滤掉 `tc.qq.com/stodownload`。
2. **下载图片**：必须带 `Referer: https://mp.weixin.qq.com/`（缺了 403）。存到库根 `eagle-cache/articles/<topic>/<标题>-<序号>.<png|jpg>`（Obsidian 模式），序号保持数组顺序（即原文图片顺序）。`wx_fmt=` 决定扩展名。
3. **上传 Eagle**：`POST http://localhost:41595/api/item/addFromPath`，与单篇流程一致。**Eagle 未启动会 connection refused**——此时跳过 Eagle 链接只写本地嵌入，笔记不受影响，之后可补传。
4. **OCR 提取文字**：本地 视觉模型（Ollama 可用 `qwen3-vl:32b-instruct`，8192 ctx 足够），逐张调用 `/api/generate`，提示词要点：「逐字转写、保持结构、表格用 Markdown、不要总结不要省略、看不清用 [?]」。实测 13 张约 6–8 分钟（100% GPU），结果缓存到临时 JSON 防中断重来。
5. **组装笔记**：frontmatter 与单篇约定一致（author 用 `nick_name`）；正文开头加一行说明「本文为微信图片消息，文字由视觉模型从 N 张图逐字提取」；然后**每张图 `![[eagle-cache/articles/<topic>/…]]` 在上、提取文字在下**，方便对照校对；末尾更新 `index.md`。

## 质量与边界

- qwen3-vl 对中文卡片图转写质量好（榜单表格可完整转 Markdown），但本质是 OCR，个别字可能错，交付时提醒用户 重要数据对图核一下。
- OCR 结果若被模型包在 ``` 围栏里，写笔记前剥掉。
- 提取的是「图上文字」，不是文章正文——没有的东西（如作者只在口头补充的信息）不要脑补。
- 样例笔记：`<专题>/00-收集/<文章标题>.md`。
