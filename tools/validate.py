#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扩展集仓库的深度校验（CI 门卫）。

对 index.txt 的每一条做工具端信任链（设计 §14.4）的**仓库侧复刻**：
  1. 索引行格式（10 列、摘要 hex、会社/作品非空）
  2. 档案存在，且 sha256 == 索引里的 档案摘要
  3. 解码：magic / 版本 / headerSize / CRC32 / chunk 结构
  4. 内嵌 target.sha256 == 索引里的 目标exe摘要（索引是信任根）
  5. 档案自洽性：EXCEPTIONS == 0；盐展开后的哈希条数与名字条数一致（防截断/防重名塌缩）
  6. 目录名与档案 META 一致（P-1：索引 / 目录 / META 三个名字来源必须同一条）
  7. 盐摘要 == sha256(UTF-8(盐))
外加整仓：无重复 (会社,作品)、同一 exe 摘要不得挂两个不同标题。

只读；不写任何文件。发现任何问题退出码 1。
用法： python tools/validate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yuax  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

failures = []
warnings = []
checked = 0


def fail(msg):
    failures.append(msg)


def warn(msg):
    warnings.append(msg)


def main():
    index_path = os.path.join(REPO_ROOT, yuax.INDEX_FILENAME)
    if not os.path.isfile(index_path):
        print("index.txt 不存在——请先运行 python tools/build-index.py", file=sys.stderr)
        return 1
    with open(index_path, "rb") as fh:
        text = fh.read().decode("utf-8-sig")

    rows, problems = yuax.parse_index(text)
    for problem in problems:
        fail("索引解析：%s" % problem)

    checked = 0
    seen_slots = {}
    seen_exe = {}
    for row in rows:
        checked += 1
        publisher = row["会社"]
        title = row["作品"]
        label = "%s / %s" % (publisher, title)

        # 2) 路径必须用 sanitize 后的名字拼（与产品落库口径一致）
        safe_pub = yuax.safe_component(publisher)
        safe_title = yuax.safe_component(title)
        if not safe_pub or not safe_title:
            fail("%s：会社/作品净化后为空，做不出库目录名" % label)
            continue
        archive_path = os.path.join(REPO_ROOT, safe_pub, safe_title, yuax.ARCHIVE_FILENAME)
        if not os.path.isfile(archive_path):
            fail("%s：档案不存在（按净化后的目录名找）：%s" % (label, archive_path))
            continue

        # 2) sha256 对索引
        actual_sha = yuax.sha256_file(archive_path)
        if actual_sha != row["档案摘要"].upper():
            fail("%s：档案 sha256 %s… 与索引 %s… 不符 —— 字节被改过或索引写错"
                 % (label, actual_sha[:16], row["档案摘要"][:16]))
            continue

        # 3) 解码 + 结构
        parsed = yuax.parse_archive(archive_path)
        if not parsed["ok"]:
            fail("%s：档案解码失败：%s" % (label, "；".join(parsed["problems"])))
            continue
        meta = parsed["meta"]
        identity = parsed["identity"]

        # 6) 目录名 == 档案 META 名（P-1）
        meta_pub = meta.get("publisher", "")
        meta_title = meta.get("title", "")
        if (meta_pub.lower() != publisher.lower() or meta_title.lower() != title.lower()):
            fail("%s：索引名与档案 META 名不一致（META 说 %s / %s）—— 三个名字来源必须同一条"
                 % (label, meta_pub, meta_title))

        # 4) 内嵌 exe 摘要 == 索引
        embedded_exe = identity.get("target.sha256", "").upper()
        if not embedded_exe:
            fail("%s：档案缺目标 exe 摘要（索引是信任根，这条不能空）" % label)
        elif embedded_exe != row["目标exe摘要"].upper():
            fail("%s：档案内嵌目标 exe 摘要 %s… 与索引 %s… 不符"
                 % (label, embedded_exe[:16], row["目标exe摘要"][:16]))

        # 7) 盐摘要
        salt = identity.get("salt", "")
        if yuax.sha256_text(salt) != row["盐摘要"].upper():
            fail("%s：盐摘要与档案内嵌的盐对不上" % label)
        if identity.get("saltKnown", "0") != "1":
            warn("%s：档案 saltKnown != 1（无盐档案展开不出任何哈希，确认是有意的）" % label)

        # 5) 自洽性：例外为 0 + 哈希展开条数与名字条数一致（防截断、防重名塌缩）
        if parsed["exceptions"]:
            fail("%s：自校验例外 %d 条（必须为 0）：%s"
                 % (label, len(parsed["exceptions"]), parsed["exceptions"][:3]))
        file_hashes = {yuax.file_hash(n, salt) for n in parsed["files"]}
        dir_hashes = {yuax.domain_hash(yuax.normalize_dir(n), salt) for n in parsed["dirs"]}
        if len(file_hashes) != len(parsed["files"]):
            fail("%s：文件名展开后哈希有塌缩（%d 个名字 -> %d 个哈希）"
                 % (label, len(parsed["files"]), len(file_hashes)))
        if len(dir_hashes) != len(parsed["dirs"]):
            fail("%s：目录名展开后哈希有塌缩（%d 个名字 -> %d 个哈希）"
                 % (label, len(parsed["dirs"]), len(dir_hashes)))
        stats_files = parsed["stats"].get("files", "")
        stats_dirs = parsed["stats"].get("directories", "")
        if stats_files and int(stats_files) != len(parsed["files"]):
            fail("%s：STATS.files=%s 与 FILES chunk %d 条不一致"
                 % (label, stats_files, len(parsed["files"])))
        if stats_dirs and int(stats_dirs) != len(parsed["dirs"]):
            fail("%s：STATS.directories=%s 与 DIRS chunk %d 条不一致"
                 % (label, stats_dirs, len(parsed["dirs"])))
        if row["条数"] and int(row["条数"]) != len(parsed["dirs"]) + len(parsed["files"]):
            fail("%s：索引条数 %s 与档案 %d 条不一致"
                 % (label, row["条数"], len(parsed["dirs"]) + len(parsed["files"])))

        # 整仓口径：同槽位 / 同 exe 摘要
        slot = (safe_pub.lower(), safe_title.lower())
        if slot in seen_slots:
            fail("%s：槽位与「%s」重复（净化后同名）" % (label, seen_slots[slot]))
        else:
            seen_slots[slot] = label
        exe = row["目标exe摘要"].upper()
        if exe:
            if exe in seen_exe and seen_exe[exe] != label:
                warn("%s：目标 exe 摘要与「%s」相同（同一游戏投了两份不同名字？请确认是有意的）"
                     % (label, seen_exe[exe]))
            else:
                seen_exe[exe] = label

    # 信息级：仓库里存在"有目录但没进索引"的条目
    for publisher in sorted(os.listdir(REPO_ROOT), key=str.lower):
        pub_dir = os.path.join(REPO_ROOT, publisher)
        if not os.path.isdir(pub_dir) or publisher.startswith("."):
            continue
        for title in sorted(os.listdir(pub_dir), key=str.lower):
            if not os.path.isfile(os.path.join(pub_dir, title, yuax.ARCHIVE_FILENAME)):
                continue
            slot = (publisher.lower(), title.lower())
            if slot not in {(k[0].lower(), k[1].lower()) for k in seen_slots}:
                warn("目录 %s/%s 里有 rules.int 但不在 index.txt 里" % (publisher, title))

    print("校验完成：%d 条索引；失败 %d，提醒 %d"
          % (checked, len(failures), len(warnings)))
    for msg in failures:
        print("  [FAIL] %s" % msg)
    for msg in warnings:
        print("  [提醒] %s" % msg)
    print("结论：%s" % ("全部通过" if not failures else "存在失败"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
