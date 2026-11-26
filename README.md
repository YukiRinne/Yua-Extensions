# YuaExtract 扩展集仓库

这里是 [YuaExtract](https://github.com/YukiRinne/YuaExtract) 的官方**扩展集**仓库。

**扩展集（`rules.int`）是什么？**  
可以把它理解成一部游戏的「文件名对照表」。平时解包新游戏需要启动游戏做动态收集；只要有人把这份对照表分享到这里，大家以后解同一部游戏就可以**直接套用扩展集，免跑游戏一键还原文件名**。

> **安全与合规说明**
> - 扩展集只包含「文件名 / 目录名」和必要的校验摘要，**不含任何游戏图片、音频、脚本等资源本体**。
> - 工具会严格校验文件完整性（SHA-256、CRC32、归属摘要、自校验例外为 0），但**验不了「名字本身是否准确」**；信任根是本仓库发布的目录，最终效果以实际解包还原率为准。

---

## 目录

- [一、怎么下载并使用扩展集（普通用户）](#一怎么下载并使用扩展集普通用户)
- [二、怎么投稿分享你的扩展集（零代码 4 步完成）](#二怎么投稿分享你的扩展集零代码-4-步完成)
- [三、怎么申请撤回或恢复扩展集](#三怎么申请撤回或恢复扩展集)
- [四、维护者审核命令速查](#四维护者审核命令速查)
- [五、常见问题（FAQ）](#五常见问题faq)
- [六、仓库结构与本地开发命令（进阶）](#六仓库结构与本地开发命令进阶)

---

## 一、怎么下载并使用扩展集（普通用户）

只需 **3 步**，全程在 YuaExtract 软件内完成：

1. **检查并下载扩展集**
   - 打开 **YuaExtract**，切换到顶部的 **「扩展集」** 页签，点击 **「检查更新」**。
   - 弹窗会自动拉取本仓库的最新扩展集列表；选中你要的游戏，点 **「下载并安装选中」** 即可。
   - *(网络提示：新版内置了 `gh-proxy.com` → `ghproxy.vip` → `GitHub 官方` 自动回退。如果连不上，可以点弹窗里的 **「管理下载源」** 调整顺序或添加自己的 HTTPS 镜像。)*
2. **选择游戏并切换模式**
   - 切回 **「首页」**，把游戏主程序 `.exe`（或游戏文件夹）拖进窗口。
   - 把提取模式切换为 **「该作扩展集提取」**，在下方选中刚才安装好的扩展集。
3. **跟着主按钮走完三步**
   - 按右侧主按钮依次完成：**生成静态 Hash 表 → 开始解包 XP3 → 开始还原资源名**，全程无需启动游戏。

<details>
<summary>老版本 YuaExtract 或手动配置仓库地址</summary>

- **新版默认入口（GitHub Releases 目录，推荐）**：
  ```text
  https://github.com/YukiRinne/Yua-Extensions/releases/latest/download/catalog.json
  ```
- **老版本兼容地址（传统 `index.txt` 目录基址）**：
  ```text
  https://raw.githubusercontent.com/YukiRinne/Yua-Extensions/main/
  https://cdn.jsdelivr.net/gh/YukiRinne/Yua-Extensions@main/
  ```

无论直连还是通过镜像下载，客户端都会完整校验 `SHA-256`、容器结构与 `CRC32`、目标 exe 摘要、会社/作品一致性，以及自校验例外数为 0；任一项不通过都会丢弃临时文件，不会改动本地扩展集库。

</details>

---

## 二、怎么投稿分享你的扩展集（零代码 4 步完成）

**不需要会用 Git，不需要 Fork 仓库，也不用敲任何命令**，浏览器里拖文件就能投稿。

### 第 1 步：在 YuaExtract 里生成并自测 `rules.int`

1. 先用首页的 **「传统动态模式」** 完成一次该游戏的解包与资源名还原。
2. 切到 **「制作扩展集」**（或 **「制作」**）页签：
   - 点 **「信息」**（编辑当前扩展集信息），填好：
     - **会社**：官方罗马字或通行译名（例如 `Yuzu Soft`）
     - **作品**：游戏名称（例如 `LimeLight Lemonade Jam`）
     - **贡献者 / 版本 / 留言**：按实际情况填写
   - 点 **「生成当前游戏扩展集」**，生成 `rules.int`。
   - 点 **「测试」**（只读推演，不会改动任何游戏文件），确认还原率正常且**自校验例外为 0**。
   - 点 **「草稿」**（打开当前扩展集目录），就能看到生成的 `rules.int`（通常在 `<工作区>\ExtensionDraft\rules.int`）。

### 第 2 步：把 `rules.int` 压缩成 `.zip`

- 右键找到的 `rules.int`，把它压缩成一个 **`.zip`** 文件（例如 `LimeLight Lemonade Jam.zip`）。
- **为什么必须压缩？** GitHub Issue 网页不支持直接上传 `.int` 后缀的文件，打包成 `.zip` 后就可以直接拖进去。
- **注意**：一个 `.zip` 里请只放 **1 份** `rules.int`，大小不超过 **64 MiB**。

### 第 3 步：打开投稿表单，拖进去提交

1. 点这里打开表单：👉 **[投稿或更新扩展集](https://github.com/YukiRinne/Yua-Extensions/issues/new?template=submit-extension.yml)**
2. 标题写上容易辨认的游戏名（例如 `[扩展集投稿] Yuzu Soft / LimeLight Lemonade Jam`）。
3. 把第 2 步打好的 **`.zip`** 直接拖进 **「扩展集文件」** 输入框，等它自动上传并变成链接（也可以直接粘贴公网 `https://` 直链）。
   - *会社、作品、条数、目标 exe 摘要等信息都会由机器人自动从 `rules.int` 里读取，不用手填。*
4. 如有需要（例如游戏版本号、补丁情况、更新了哪些内容），可以在 **「补充说明」** 里简单写一句，然后点击 **Submit new issue** 提交。

### 第 4 步：等待机器人自动检查 & 维护者审核上线

- 提交后约 **1 分钟内**，机器人会在 Issue 下方自动回复检查报告：
  - **检查通过**：等待维护者审核即可。批准后系统会自动合入仓库并发布新版 Release，之后所有人打开 YuaExtract 点「检查更新」就能搜到它。
  - **检查未通过**：按机器人回复的具体原因修正后，编辑 Issue 换上新的 `.zip`（或编辑后在评论区回复 `/recheck`）即可重新检查。

### 投稿前小提醒

- **更新已有游戏**：请先在仓库目录或 [`index.txt`](index.txt) 确认现有的「会社 / 作品」名称，生成 `rules.int` 时保持一致，不要换个别名重复新建目录。
- **目录名限制**：会社和作品名请避免使用 Windows 不允许的字符（`< > : " / \ | ? *`）及保留设备名。
- **提交前务必验证**：请先在本地用「测试」或实际解包确认还原效果良好，且自校验例外为 `0`。

---

## 三、怎么申请撤回或恢复扩展集

如果发现仓库中已收录的某份扩展集存在严重错误需要下架，或需要恢复之前撤回的记录：

1. 打开 👉 **[撤回或恢复扩展集](https://github.com/YukiRinne/Yua-Extensions/issues/new?template=change-extension.yml)**。
2. 选择操作（**撤回扩展集** / **恢复撤回记录**），填好与仓库目录一致的 **会社**、**作品** 和 **原因或说明** 并提交。
3. 机器人核对无误、维护者批准后会自动生效并重新发布目录。

---

## 四、维护者审核命令速查

机器人完成自动检查后，会在回复里给出本次检查的编号（`<检查编号>`）。具有仓库写入权限的维护者直接在对应 Issue 下评论即可：

| 评论命令 | 效果 |
| --- | --- |
| `/approve <检查编号>` | 从对应检查产物中读取已验证字节，自动创建 PR → 二次校验 → 合并到 `main` → 发布最新 GitHub Release → 自动关闭 Issue |
| `/reject <原因>` | 拒绝本次投稿，回复原因并关闭 Issue（不填原因则使用默认提示） |
| `/recheck` | 重新触发一次自动检查（投稿人本人或维护者均可使用） |

> 安全机制：审批时不会重新下载外部链接，并且会校验 Issue 正文自检查后未被篡改；如果投稿人修改了正文，必须先重新检查再审批。

---

## 五、常见问题（FAQ）

**Q：把 `rules.int` 拖进 GitHub Issue 输入框时提示不支持该文件类型？**  
A：GitHub 附件默认不接受 `.int` 后缀。右键把 `rules.int` 压缩成 `.zip` 再拖进去即可。

**Q：在国内点「检查更新」连不上 GitHub 怎么办？**  
A：新版 YuaExtract 默认会依次尝试 `gh-proxy.com` → `ghproxy.vip` → `GitHub 官方`。如果仍然失败，可以在「在线更新扩展集」窗口点 **「管理下载源」** 调整优先级、添加可用的 HTTPS 反代，或开启本地代理后再试。

**Q：走第三方镜像下载安全吗？会被篡改吗？**  
A：安全。客户端下载扩展集后会在本地完整校验 `SHA-256`、容器头与 `CRC32`、目标 exe 摘要以及自校验例外数为 0，任何篡改都会直接拦截并丢弃。

**Q：我想更新仓库里已经有的游戏扩展集，应该怎么做？**  
A：和首次投稿完全一样，只要在 YuaExtract 里把「会社」和「作品」填得和仓库现有目录一致，按上面的 4 步提交新 `.zip` 即可。机器人会自动比对新旧条目数变化。

---

## 六、仓库结构与本地开发命令（进阶）

<details>
<summary>点击展开：目录结构、本地脚本命令与 Registry v1 契约</summary>

### 目录结构

```text
index.txt                       ← 兼容索引：10 列 TSV，由脚本自动生成，禁止手写
yanked.json                     ← 可选；记录已撤回的扩展集元数据（备份存放在 .yanked/）
docs/registry-v1.md             ← Registry v1 发布格式、catalog.json 字段与安全契约
tools/
  yuax.py                       ← 共享库（容器解析 / 哈希 / 校验 / ASCII 附件命名）
  build-index.py                ← 扫描目录重生成 index.txt（--check 供 CI 只读核对）
  validate.py                   ← 深度校验（CI 门卫）
  build-catalog.py              ← 构建 GitHub Releases 分发的 catalog.json 与附件资产
  submission.py                 ← Issue 自动校验与 /approve 审批入库脚本
tests/
  test_registry.py              ← 仓库工具与投稿流程单元测试
<会社>/<作品>/rules.int          ← 扩展集档案本体
<会社>/<作品>/info.txt           ← 可选；给人看的摘要信息，工具不读
```

### 本地校验与构建命令

需要 Python 3.8+，**仅依赖标准库**：

```bash
python -m unittest discover -s tests -v   # 运行单元测试
python tools/build-index.py               # 重生成 index.txt
python tools/build-index.py --check       # 只核对不写盘（CI 使用）
python tools/validate.py                  # 深度校验（任一项失败退出码 1）
python tools/build-catalog.py --output dist/registry --base-url https://example.invalid
```

维护者也可以直接通过传统 PR 提交扩展集，CI（`validate.yml`）同样会强制检查 `index.txt` 与档案完整性；合并到 `main` 后 `publish.yml` 会自动发布新的 GitHub Release。完整技术规范见 [Registry v1 契约](docs/registry-v1.md)。

</details>

---

## 许可

待定（本仓库内容只有文件名与校验哈希，不含任何游戏资源本体；正式公开前请先确认许可与合规要求）。
