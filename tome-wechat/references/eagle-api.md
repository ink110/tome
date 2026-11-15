# Eagle API 速查（v4.0.0，localhost:41595）

> 供脚本与后续扩展复用。字段来自 Eagle API 实测 + Eagle Integration 插件 `main.js` 逆向。

## 通用

- Base：`http://localhost:41595/api`
- 所有响应形如 `{"status": "success" | "error", "data": …}`，出错时可能带 `message`。

## 端点

| 端点 | 方法 | 关键入参 | 返回 `data` |
| --- | --- | --- | --- |
| `/library/info` | GET | — | `library.path`（库根路径） |
| `/folder/list` | GET | — | 文件夹树数组（含 `id` / `name` / `children`） |
| `/folder/create` | POST | `folderName`, `parent?` | `{id, …}` |
| `/item/addFromPath` | POST | `path`, `name`, `folderId?`, `annotation?` | **itemId（字符串）** |
| `/item/addFromURL` | POST | `url`, `name?`, `folderId?`, `headers?` | 新 item（本技能未用） |
| `/item/info?id=` | GET | — | `{name, ext, …}` |
| `/item/list` | GET | `keyword?` 等 | item 数组（含 `folders` 数组，**不是** `folderId`） |

## 关键结论（逆向所得）

- `addFromPath` 成功后 `data` 就是新 item 的 **id（字符串）**，直接拼 `eagle://item/{id}`。
- item 本地真实文件路径 = `{library.path}/images/{itemId}.info/{name}.{ext}`。
- item 的文件夹字段是 `folders`（数组），不是 `folderId`。
- `addFromURL` 支持 `headers.referer` 绕过防盗链，但本技能走「自下载 + `addFromPath`」，对微信更可控。
