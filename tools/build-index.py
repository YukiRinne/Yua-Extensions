#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描仓库里的 <会社>/<作品>/rules.int，自动生成 index.txt。

索引**永远自动生成、禁止手写**：会社 / 作品 / 各摘要 / 条数全部取自档案本身
（META + IDENTITY + 实测 sha256），这从根上消灭"索引名 ≠ 档案 META 名"（设计 §14.4 第 9 条）。

用法：
  python tools/build-index.py                 # 重生成 index.txt
  python tools/build-index.py --check         # 不写盘：index.txt 与重生成结果不一致则退出码 1（CI 用）

只读档案，只写 index.txt；不碰别的任何文件。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yuax  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_LABEL = "yua-extensions"


def scan():
    """返回 (rows, problems)。rows 按 (会社, 作品) 不区分大小写排序。"""
    rows = []
    problems = []
    for publisher in sorted(os.listdir(REPO_ROOT), key=str.lower):
        pub_dir = os.path.join(REPO_ROOT, publisher)
        if not os.path.isdir(pub_dir) or publisher.startswith("."):
            continue
        for title in sorted(os.listdir(pub_dir), key=str.lower):
            title_dir = os.path.join(pub_dir, title)
            archive = os.path.join(title_dir, yuax.ARCHIVE_FILENAME)
            if not os.path.isdir(title_dir) or not os.path.isfile(archive):
                continue

            parsed = yuax.parse_archive(archive)
            if not parsed["ok"]:
                problems.append("%s/%s：%s" % (publisher, title,
                                               "；".join(parsed["problems"])))
                continue

            meta = parsed["meta"]
            identity = parsed["identity"]
            # P-1 的仓库侧复刻：目录名必须与档案 META 一致（净化后比），
            # 否则工具端"用户看到的槽位"与"实际落盘的槽位"会分家。
            safe_pub = yuax.safe_component(meta.get("publisher", ""))
            safe_title = yuax.safe_component(meta.get("title", ""))
            if safe_pub.lower() != publisher.lower() or safe_title.lower() != title.lower():
                problems.append(
                    "%s/%s：目录名与档案 META 不一致（META 说 %s / %s）—— "
                    "请把目录改名为 %s/%s，或重新生成档案"
                    % (publisher, title, meta.get("publisher", "?"),
                       meta.get("title", "?"), safe_pub, safe_title))
                continue

            if parsed["exceptions"]:
                problems.append("%s/%s：档案自校验例外 %d 条（必须为 0）"
                                % (publisher, title, len(parsed["exceptions"])))
                continue
            if not identity.get("target.sha256"):
                problems.append("%s/%s：档案缺目标 exe 摘要（索引是信任根，不能空）"
                                % (publisher, title))
                continue

            rows.append({
                "会社": publisher,
                "作品": title,
                "目标exe摘要": identity.get("target.sha256", "").upper(),
                "兄弟exe摘要": identity.get("sibling.sha256", "").upper(),
                "盐摘要": yuax.sha256_text(identity.get("salt", "")),
                "档案摘要": parsed["sha256"],
                "条数": str(len(parsed["dirs"]) + len(parsed["files"])),
                "生成时间": meta.get("generatedAt", "") or "-",
                "安装时间": "-",
                "来源": SOURCE_LABEL,
            })

    rows.sort(key=lambda r: (r["会社"].lower(), r["作品"].lower()))
    return rows, problems


def render(rows):
    lines = [yuax.INDEX_HEADER] + [yuax.index_line(r) for r in rows]
    return "\r\n".join(lines) + "\r\n"


def main():
    check_only = "--check" in sys.argv
    rows, problems = scan()
    for problem in problems:
        print("[问题] %s" % problem, file=sys.stderr)

    index_path = os.path.join(REPO_ROOT, yuax.INDEX_FILENAME)
    rendered = render(rows)

    if check_only:
        existing = ""
        if os.path.isfile(index_path):
            with io_open(index_path) as fh:
                existing = fh.read()
        if existing != rendered:
            print("[CI] index.txt 与重生成结果不一致 —— 请运行 "
                  "python tools/build-index.py 后重新提交", file=sys.stderr)
            return 1
        print("[CI] index.txt 与重生成结果一致（%d 行）" % len(rows))
        return 0

    with open(index_path, "wb") as fh:
        fh.write(rendered.encode("utf-8"))
    print("[build] index.txt：%d 行" % len(rows))
    for r in rows:
        print("  - %s / %s（%s 条，档案摘要 %s…）"
              % (r["会社"], r["作品"], r["条数"], r["档案摘要"][:16]))
    if problems:
        print("注意：存在 %d 条未入索引的问题（见上）" % len(problems), file=sys.stderr)
        return 1
    return 0


def io_open(path):
    import io
    return io.open(path, "r", encoding="utf-8-sig", newline="")


if __name__ == "__main__":
    sys.exit(main())
