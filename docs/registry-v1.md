# Yua-Extensions Registry v1 契约

本仓库遵循“Git 存校验后档案 + Issue 机器人投稿审批 + GitHub Releases 原子分发”的发布模型。

## 1. Git 仓库结构

- `<会社>/<作品>/rules.int`：在架扩展集（`YXS1` 容器，`EXCEPTIONS == 0` 且目录名与 `META` 一致）。
- `<会社>/<作品>/info.txt`：只读副本，仅供人类查阅。
- `index.txt`：10 列 TSV，由 `python tools/build-index.py` 自动重生成（禁止手写），同时作为旧版客户端与 `main` 分支的兼容清单。
- `yanked.json`（可选）：撤回记录表（`schema_version: 1`、`yanked[]`），对应备份档案保留在 `.yanked/<会社>/<作品>/` 中以便恢复。

## 2. GitHub Releases 分发产物（`publish.yml`）

每次 `main` 变更或审批合并完成后：
1. `build` 作业（只读权限）运行 `unittest`、`build-index.py --check`、`validate.py` 与 `build-catalog.py`，产出目录产物 `Artifact`。
2. `publish` 作业核对 `main` 上 `index.txt` 未发生变化后，创建**草稿 Release**（Tag：`catalog-<short_sha>-<run_id>-<attempt>`），将 `catalog.json`、`index.txt` 和全部 `.int` 附件上传完整后，原子公开并设为 `latest`。

### Release 附件命名
- 为避免 GitHub Releases 对非 ASCII 字符或空格做转码改名，所有 `.int` 附件统一使用纯 ASCII 文件名：
  - ASCII 名称：`<pub_slug>__<title_slug>__<sha256[:12]>.int`
  - 含非 ASCII 名称：`ext_<slot_sha256[:8]>__<sha256[:12]>.int`
- 始终带上档案 `sha256` 前 12 位，确保不同版本附件名唯一且稳定。

### `catalog.json` v1 结构

入口地址：`https://github.com/YukiRinne/Yua-Extensions/releases/latest/download/catalog.json`

```json
{
  "schema_version": 1,
  "repository": "https://github.com/YukiRinne/Yua-Extensions",
  "generated_at": "2026-11-20T00:00:00Z",
  "release_tag": "catalog-...",
  "entries": [
    {
      "id": "Yuzu/limelight_lj",
      "publisher": "Yuzu",
      "title": "limelight_lj",
      "contributor": "Rinne",
      "version": "v1.0",
      "date": "2026-10-15",
      "comment": "",
      "generated_at": "2026-10-15 14:52:34",
      "tool_version": "YuaExtract extension-set/1",
      "entry_count": 85285,
      "directory_count": 49,
      "file_count": 85236,
      "exception_count": 0,
      "exe_name": "limelight_lj.exe",
      "exe_size": 4408832,
      "exe_sha256": "...",
      "sibling_sha256": "...",
      "salt_sha256": "...",
      "archive_sha256": "...",
      "yanked": false,
      "yank_reason": "",
      "package": {
        "path": "Yuzu__limelight_lj__3a12fd2ba90a.int",
        "url": "https://github.com/YukiRinne/Yua-Extensions/releases/download/.../Yuzu__limelight_lj__3a12fd2ba90a.int",
        "size": 238299,
        "sha256": "..."
      }
    }
  ]
}
```

客户端安全约束：
- 仅接受 `https://` 地址，下载后仍须以 `archive_sha256` + `exe_sha256` + 容器解码 + `EXCEPTIONS == 0` 完整验签，即使通过第三方镜像代理也不可绕过。
- `yanked: true` 的条目（`package: null`）不得用于安装。

## 3. Issue 自动投稿流（权限隔离）

1. 用户通过「投稿或更新扩展集」Issue 表单提交 `.zip`（内含单个 `rules.int`）或公网 `https://` 链接；`check`（仅 `contents: read`）校验档案、比对当前索引并保存 Artifact。
2. 维护者检查机器人回复报告后，评论 `/approve <run_id>`（或 `/reject 原因`）。
3. `moderate` 仅从对应 `run_id` 的 Artifact 读取已校验字节，核对 Issue 正文 SHA-256 未篡改后自动发起 PR，经 `validate.yml` 二次校验后合并并触发 `publish.yml` 发布。
