# YuaExtract 扩展集仓库

这里存放 [YuaExtract](https://github.com/YukiRinne/YuaExtract) 的**扩展集**（`rules.int`）：
一部游戏「名字 ↔ 哈希」的知识档案，让同一部游戏以后解包还原时**不必再跑一遍游戏做动态采集**。

> **档案里只有「文件名 / 目录名」和它们的哈希，不含任何游戏资源本体。**
> 另外要如实说明：工具**验不了"名字对不对"**（档案里不存哈希层，本地没有可对照的哈希）；
> 信任根是本仓库的 `catalog.json` / `index.txt`，名字的最终把关是**还原率这个事实**。

## 目录结构

```text
index.txt                       ← 兼容索引：10 列 TSV，由脚本自动生成，禁止手写
yanked.json                     ← 可选；记录已撤回的扩展集元数据
tools/
  yuax.py                       ← 共享库（容器解析 / 哈希 / 校验 / ASCII 附件命名）
  build-index.py                ← 扫描目录重生成 index.txt（--check 供 CI 只读核对）
  validate.py                   ← 深度校验（CI 门卫）
  build-catalog.py              ← 构建 GitHub Releases 分发的 catalog.json 与附件资产
  submission.py                 ← Issue 自动校验与 /approve 审批入库脚本
<会社>/<作品>/rules.int          ← 档案本体（会社 / 作品用净化后的名字，见下）
<会社>/<作品>/info.txt           ← 可选；只给人看，工具不读
```

## 命名约定

- **会社**：官方罗马字或通行译名（如 `Yuzu`）
- **作品**：目标 exe 名去扩展名（如 `limelight_lj`）
- 只允许 Windows 目录名合法字符（会去掉 `< > : " / \ | ? *` 与控制字符、首尾空格和点）
- **同一部游戏请认准已有目录再投稿**，不要换个名字重发一份

## 怎么投稿（推荐：Issue 表单一键投稿）

玩家无需 Fork 仓库，也不用跑命令行脚本：

1. 将 YuaExtract 生成的 `rules.int` **压缩为一个 `.zip`**。
2. 打开 [投稿或更新扩展集 Issue 表单](https://github.com/YukiRinne/Yua-Extensions/issues/new?template=submit-extension.yml)，把 `.zip` 拖入「扩展集文件」输入框（也可补充来源与说明）。
3. 提交后机器人会在 1 分钟内自动完成结构、CRC、哈希自洽性与条数比对检查并回复结果。
4. 维护者评论 `/approve <检查编号>` 后，系统自动开 PR、复核并发布到 GitHub Releases（若文件有误可修改 Issue 后评论 `/recheck`）。

如需撤回或恢复已收录扩展集，请提交 [撤回或恢复扩展集表单](https://github.com/YukiRinne/Yua-Extensions/issues/new?template=change-extension.yml)。维护者亦可直接走 PR 流程（CI 同样强制校验 `index.txt` 与深度完整性）。

## 工具用法（本地）

```bash
python -m unittest discover -s tests -v   # 运行仓库脚本与校验链路单元测试
python tools/build-index.py               # 重生成 index.txt
python tools/build-index.py --check       # 只核对不写盘（CI）
python tools/validate.py                  # 深度校验，发现任何问题退出码 1
python tools/build-catalog.py --output dist/registry --base-url https://example.invalid
```

需要 Python 3.8+，**仅依赖标准库**。完整发布契约见 [Registry v1 契约](docs/registry-v1.md)。

## 在 YuaExtract 里使用本仓库

新版 YuaExtract 默认直接从本仓库的 GitHub Releases 拉取目录，并支持内置镜像源自动回退（`gh-proxy.com` → `ghproxy.vip` → `GitHub 官方`）：

```text
https://github.com/YukiRinne/Yua-Extensions/releases/latest/download/catalog.json
```

老版本或手动指定仓库目录基址时，仍兼容原有的 `raw` / `jsDelivr` 地址：

```text
https://raw.githubusercontent.com/YukiRinne/Yua-Extensions/main/
https://cdn.jsdelivr.net/gh/YukiRinne/Yua-Extensions@main/
```

无论走直连还是镜像下载，客户端都会执行完整信任链校验（`sha256` 对索引 / 容器结构与 CRC / 内嵌 exe 摘要 / 会社作品交叉校验 / 自校验例外为 0），任一项不通过都会丢弃临时文件且不修改本地扩展集库。

## 许可

待定（仓库内容只有文件名与哈希，不含任何游戏资源本体；正式公开前请先确认许可与合规）。
