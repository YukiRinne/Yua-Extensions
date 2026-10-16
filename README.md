# YuaExtract 扩展集仓库

这里存放 [YuaExtract](https://example.invalid/) 的**扩展集**（`rules.int`）：
一部游戏「名字 ↔ 哈希」的知识档案，让同一部游戏以后解包还原时**不必再跑一遍游戏做动态采集**。

> **档案里只有「文件名 / 目录名」和它们的哈希，不含任何游戏资源本体。**
> 另外要如实说明：工具**验不了"名字对不对"**（档案里不存哈希层，本地没有可对照的哈希）；
> 信任根是本仓库的 `index.txt`，名字的最终把关是**还原率这个事实**。

## 目录结构

```text
index.txt                       ← 索引：就是一份 manifest.txt（10 列 TSV），由脚本自动生成，禁止手写
tools/
  build-index.py                ← 扫描目录重生成 index.txt（--check 供 CI 只读核对）
  validate.py                   ← 深度校验（CI 门卫）
  yuax.py                       ← 共享库（容器解析 / 哈希 / 名字净化），与产品逐字同口径
<会社>/<作品>/rules.int          ← 档案本体（会社 / 作品用净化后的名字，见下）
<会社>/<作品>/info.txt           ← 可选；只给人看，工具不读
```

## 命名约定

- **会社**：官方罗马字或通行译名（如 `Yuzu`）
- **作品**：目标 exe 名去扩展名（如 `limelight_lj`）
- 只允许 Windows 目录名合法字符（会去掉 `< > : " / \ | ? *` 与控制字符、首尾空格和点）
- **同一部游戏请认准已有目录再投稿**，不要换个名字重发一份

## 怎么投稿

1. PR 一份 `<会社>/<作品>/rules.int`（+ 可选 `info.txt`）
2. CI 会自动：重生成 `index.txt` 核对一致 → 逐条深度校验（sha256 / 容器结构 / CRC / 例外为 0 /
   内嵌 exe 摘要与索引一致 / 目录名与档案 META 一致）
3. 人工合并

## 工具用法（本地）

```bash
python tools/build-index.py          # 重生成 index.txt（投稿后由维护者/CI 生成，不要手写）
python tools/build-index.py --check  # 只核对不写盘（CI）
python tools/validate.py             # 深度校验，发现任何问题退出码 1
```

需要 Python 3.8+，只依赖标准库。

## 在 YuaExtract 里使用本仓库

「扩展集」页签 → 「在线更新」→ 仓库地址填下面任一基址：

```text
https://raw.githubusercontent.com/<owner>/<repo>/main/
https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/      ← 国内可达性通常更好
```

工具会：拉索引 → 列出与本地库的差异 → 下载 → **按信任链校验**（sha256 / 容器结构 / 内嵌 exe 摘要对索引 /
档案自洽，任一不过即拒且本地零改动）→ 入库。**入库只是写进库；用不用仍由工具里的归属校验把关。**

## 许可

待定（仓库内容只有文件名与哈希，不含任何游戏资源本体；正式公开前请先确认许可与合规）。
