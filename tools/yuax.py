#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扩展集仓库的共享工具库（自包含，只依赖 Python 标准库）。

口径来源（**逐字镜像，不许自创**）：
  * 容器格式 ........ docs/扩展集设计.md §3 / CxdecExtractorLoader/ExtensionSet.cpp
  * manifest 表头 ... ExtensionSet.cpp `WriteManifest`（10 列 TSV，'#' 注释行，CRLF）
  * 会社/作品净化 .... ExtensionSet.cpp `MakeSafePathComponent`
  * 名字哈希 ......... file_hash   = BLAKE2s-256( UTF16LE(裸名 + 盐) )
  * 目录哈希 ......... domain_hash = 字节反转( SipHash-2-4( UTF16LE(目录 + 盐), key=0^16 ) )
                       目录归一化：'\\'->'/'、小写、补结尾 '/'；空串 / "/" / "%EmptyString%" 记根
  这两个哈希实现与本仓库 `../../../YuaExtract/_verify/restore-harness/lib/hash-extension.py`
  同源（那份已与产品 resource_hash.cpp 对账：85,285 条重算零不一致）。

**本库不声称能验证"名字对不对"**：档案里不存哈希层，名字的最终把关是还原率这个事实。
"""

import hashlib
import os
import re
import struct
import zlib

MAGIC = b"YXS1"
HEADER_SIZE = 20
CHUNK_ENTRY_SIZE = 12
CHUNK_IDS = {0x0001: "META", 0x0002: "IDENTITY", 0x0003: "DIRS", 0x0004: "FILES",
             0x0005: "EXCEPTIONS", 0x0006: "RULES", 0x0007: "STATS"}
FLAG_ZLIB = 0x1

INDEX_FILENAME = "index.txt"
ARCHIVE_FILENAME = "rules.int"
CATALOG_FILENAME = "catalog.json"
YANKED_FILENAME = "yanked.json"
INDEX_HEADER = ("# 会社\t作品\t目标exe摘要\t兄弟exe摘要\t盐摘要\t档案摘要\t"
                "条数\t生成时间\t安装时间\t来源")
INDEX_COLUMNS = INDEX_HEADER.lstrip("#").strip().split("\t")

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# ---------------- 容器解析 ----------------

def parse_archive(path):
    """读一份 rules.int，返回 dict：
    ok / problems[] / meta{} / identity{} / stats{} / dirs[] / files[] / exceptions[] /
    raw_size / sha256 / chunks[]
    只读，不写任何文件。
    """

    out = {"ok": False, "problems": [], "meta": {}, "identity": {}, "stats": {},
           "dirs": [], "files": [], "exceptions": [], "chunks": [],
           "raw_size": 0, "sha256": ""}

    with open(path, "rb") as fh:
        raw = fh.read()
    out["raw_size"] = len(raw)
    out["sha256"] = hashlib.sha256(raw).hexdigest().upper()

    problems = out["problems"]
    if len(raw) < HEADER_SIZE:
        problems.append("文件只有 %d 字节，连 %d 字节的头都不够" % (len(raw), HEADER_SIZE))
        return out

    magic, version, header_size, flags, crc_stored, chunk_count = struct.unpack_from(
        "<4sHHIII", raw, 0)
    if magic != MAGIC:
        problems.append("magic 不是 YXS1（读到 %r）" % magic)
        return out
    if version != 1:
        problems.append("版本不支持：%d" % version)
        return out
    if header_size < HEADER_SIZE:
        problems.append("headerSize 异常：%d" % header_size)
        return out

    payload_stored = raw[header_size + CHUNK_ENTRY_SIZE * chunk_count:]
    payload = bytes(payload_stored)
    # CRC-32 对**未压缩** payload（§3）；若 flags bit0 置位则先 zlib 解压
    if flags & FLAG_ZLIB:
        try:
            payload = zlib.decompress(payload_stored)
        except zlib.error as exc:
            problems.append("zlib 解压失败：%s" % exc)
            return out
    crc_actual = zlib.crc32(payload) & 0xFFFFFFFF
    if crc_actual != crc_stored:
        problems.append("CRC32 不符：存 %08X / 算 %08X" % (crc_stored, crc_actual))
        return out

    # chunk 表
    table = {}
    for i in range(chunk_count):
        cid, cflags, offset, size = struct.unpack_from("<HHII", raw,
                                                       header_size + CHUNK_ENTRY_SIZE * i)
        name = CHUNK_IDS.get(cid, "UNKNOWN_%04X" % cid)
        table[name] = payload[offset:offset + size]
        out["chunks"].append((name, offset, size))

    for name in ("META", "IDENTITY", "STATS"):
        if name not in table:
            problems.append("缺少 %s chunk" % name)
    if "DIRS" not in table or "FILES" not in table:
        problems.append("缺少 DIRS/FILES chunk")
    if problems:
        return out

    out["meta"] = parse_kv(table["META"].decode("utf-8", errors="replace"))
    out["identity"] = parse_kv(table["IDENTITY"].decode("utf-8", errors="replace"))
    out["stats"] = parse_kv(table["STATS"].decode("utf-8", errors="replace"))
    out["dirs"] = split_lines(table["DIRS"].decode("utf-8", errors="replace"))
    out["files"] = split_lines(table["FILES"].decode("utf-8", errors="replace"))
    out["exceptions"] = split_lines(table["EXCEPTIONS"].decode("utf-8", errors="replace"))
    out["ok"] = True
    return out


def split_lines(text):
    return [line for line in text.replace("\r\n", "\n").split("\n") if line]


def parse_kv(text):
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line:
            continue
        key, sep, value = line.partition("=")
        out[key if sep else key] = value if sep else ""
    return out


# ---------------- 哈希与净化（镜像产品口径） ----------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            h.update(block)
    return h.hexdigest().upper()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def file_hash(name, salt):
    return hashlib.blake2s((name + salt).encode("utf-16-le"),
                           digest_size=32).hexdigest().upper()


_MASK64 = (1 << 64) - 1


def _rotl(v, c):
    return ((v << c) | (v >> (64 - c))) & _MASK64


def _siphash24(data, key):
    k0 = int.from_bytes(key[0:8], "little")
    k1 = int.from_bytes(key[8:16], "little")
    v0 = 0x736F6D6570736575 ^ k0
    v1 = 0x646F72616E646F6D ^ k1
    v2 = 0x6C7967656E657261 ^ k0
    v3 = 0x7465646279746573 ^ k1

    def rnd():
        nonlocal v0, v1, v2, v3
        v0 = (v0 + v1) & _MASK64
        v1 = _rotl(v1, 13) ^ v0
        v0 = _rotl(v0, 32)
        v2 = (v2 + v3) & _MASK64
        v3 = _rotl(v3, 16) ^ v2
        v0 = (v0 + v3) & _MASK64
        v3 = _rotl(v3, 21) ^ v0
        v2 = (v2 + v1) & _MASK64
        v1 = _rotl(v1, 17) ^ v2
        v2 = _rotl(v2, 32)

    n = len(data)
    for i in range(0, n - (n % 8), 8):
        m = int.from_bytes(data[i:i + 8], "little")
        v3 ^= m
        rnd(); rnd()
        v0 ^= m
    tail = data[n - (n % 8):]
    b = (n & 0xFF) << 56
    for i, byte in enumerate(tail):
        b |= byte << (8 * i)
    v3 ^= b
    rnd(); rnd()
    v0 ^= b
    v2 ^= 0xFF
    for _ in range(4):
        rnd()
    return (v0 ^ v1 ^ v2 ^ v3) & _MASK64


def normalize_dir(name):
    text = (name or "").strip()
    if text in ("", "/", "\\", "%EmptyString%"):
        return "/"
    text = text.replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    text = text.strip("/").lower()
    if not text:
        return "/"
    return text + "/"


def domain_hash(directory, salt):
    d = normalize_dir(directory)
    message = ("" if d == "/" else d) + salt
    value = int.from_bytes(
        _siphash24(message.encode("utf-16-le"), b"\x00" * 16).to_bytes(8, "big"),
        "little")
    return value.to_bytes(8, "big").hex().upper()


def safe_component(raw):
    """镜像 ExtensionSet.cpp `MakeSafePathComponent`：去控制字符与 <>:"/\\|?*，再削首尾空格与点。"""
    bad = set('<>:"/\\|?*')
    kept = [ch for ch in raw if ord(ch) >= 0x20 and ch not in bad]
    return "".join(kept).strip(" .")


# ---------------- 索引 TSV ----------------

def parse_index(text):
    """解析 index.txt。返回 (rows, problems)。
    与产品 ParseManifestText 同口径：'#' 注释行与空行跳过；**列数不对 / 会社或作品为空 = 跳过该行并点名行号**；
    '字段里写了值但值坏掉'（摘要非空却非 64 位 hex、条数非空却非整数）= 整份不可用。
    """
    rows = []
    problems = []
    import re
    hex_re = re.compile(r"^[0-9A-Fa-f]{64}$")

    for lineno, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != len(INDEX_COLUMNS):
            problems.append("第 %d 行：%d 列（应为 %d 列），已跳过"
                            % (lineno, len(cols), len(INDEX_COLUMNS)))
            continue
        publisher, title = cols[0].strip(), cols[1].strip()
        if not publisher or not title:
            problems.append("第 %d 行：会社或作品为空，已跳过" % lineno)
            continue
        digest_fields = {"目标exe摘要": cols[2], "兄弟exe摘要": cols[3],
                         "盐摘要": cols[4], "档案摘要": cols[5]}
        bad = [k for k, v in digest_fields.items() if v and not hex_re.match(v)]
        if bad:
            problems.append("第 %d 行：摘要字段不是 64 位 hex（%s）—— 整份索引不可用"
                            % (lineno, "、".join(bad)))
            return rows, problems
        if cols[6] and not cols[6].isdigit():
            problems.append("第 %d 行：条数不是整数 —— 整份索引不可用" % lineno)
            return rows, problems
        rows.append(dict(zip(INDEX_COLUMNS, [c.strip() for c in cols])))

    return rows, problems


def index_line(row):
    """ManifestRow 形状 -> 一行 TSV。row 用 dict，键 = INDEX_COLUMNS。"""
    return "\t".join(row.get(col, "") for col in INDEX_COLUMNS)


def is_windows_reserved_name(name):
    """判别 Windows 设备保留名（含带扩展名形式如 CON.txt）。"""
    cleaned = (name or "").strip(" .")
    if not cleaned:
        return True
    stem = cleaned.split(".", 1)[0].upper()
    return stem in _WINDOWS_RESERVED_NAMES


def _ascii_slug(text):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    slug = re.sub(r"_+", "_", slug).strip("._-")
    if ".." in slug:
        slug = slug.replace("..", "_")
    return slug


def ascii_asset_name(publisher, title, archive_sha256):
    """生成 GitHub Releases 安全的纯 ASCII 附件名（含档案 sha256 前 12 位防碰撞）。"""
    pub_slug = _ascii_slug(publisher)
    title_slug = _ascii_slug(title)
    sha12 = (archive_sha256 or "").strip().lower()[:12]
    if not re.fullmatch(r"[0-9a-f]{12}", sha12):
        raise ValueError("INVALID_ARCHIVE_SHA256")
    if pub_slug and title_slug:
        base = f"{pub_slug}__{title_slug}"
    else:
        slot_key = f"{(publisher or '').strip().lower()}\0{(title or '').strip().lower()}"
        base = f"ext_{sha256_text(slot_key).lower()[:8]}"
    max_base_len = 80 - len(f"__{sha12}.int")
    if len(base) > max_base_len:
        base = base[:max_base_len].rstrip("._-")
        if not base:
            slot_key = f"{(publisher or '').strip().lower()}\0{(title or '').strip().lower()}"
            base = f"ext_{sha256_text(slot_key).lower()[:8]}"
    name = f"{base}__{sha12}.int"
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.int", name):
        raise ValueError("ASSET_NAME_NOT_ASCII")
    return name


def validate_archive_parsed(parsed, expected_publisher=None, expected_title=None):
    """对 parse_archive 结果做深度安全与自洽校验；返回问题列表（空列表表示通过）。"""
    problems = []
    if not parsed.get("ok"):
        return list(parsed.get("problems") or ["档案解码失败"])

    meta = parsed.get("meta") or {}
    identity = parsed.get("identity") or {}
    stats = parsed.get("stats") or {}

    publisher = meta.get("publisher", "").strip()
    title = meta.get("title", "").strip()
    safe_pub = safe_component(publisher)
    safe_title = safe_component(title)
    if not publisher or not title:
        problems.append("META 缺少 publisher 或 title")
    elif not safe_pub or not safe_title:
        problems.append("META 的 publisher 或 title 净化后为空，无法作为目录名")
    elif is_windows_reserved_name(safe_pub) or is_windows_reserved_name(safe_title):
        problems.append("META 的 publisher 或 title 命中 Windows 保留设备名")

    if expected_publisher is not None and safe_component(expected_publisher).lower() != safe_pub.lower():
        problems.append(
            "目录与档案 META 会社不一致（期望 %s，实际 %s）" % (expected_publisher, meta.get("publisher", "?"))
        )
    if expected_title is not None and safe_component(expected_title).lower() != safe_title.lower():
        problems.append(
            "目录与档案 META 作品不一致（期望 %s，实际 %s）" % (expected_title, meta.get("title", "?"))
        )

    hex_re = re.compile(r"^[0-9A-F]{64}$")
    target_sha = identity.get("target.sha256", "").strip().upper()
    if not hex_re.match(target_sha):
        problems.append("IDENTITY 缺少有效的 64 位目标 exe SHA-256（target.sha256）")
    sibling_sha = identity.get("sibling.sha256", "").strip().upper()
    if sibling_sha and not hex_re.match(sibling_sha):
        problems.append("IDENTITY 的 sibling.sha256 格式不是 64 位 SHA-256")

    exceptions = parsed.get("exceptions") or []
    if exceptions:
        problems.append("自校验例外 %d 条（必须为 0）：%s" % (len(exceptions), exceptions[:3]))

    dirs = parsed.get("dirs") or []
    files = parsed.get("files") or []
    if len(dirs) + len(files) == 0:
        problems.append("档案条数为 0（DIRS 与 FILES 均为空）")

    salt = identity.get("salt", "")
    file_hashes = {file_hash(n, salt) for n in files}
    dir_hashes = {domain_hash(normalize_dir(n), salt) for n in dirs}
    if len(file_hashes) != len(files):
        problems.append("文件名展开后哈希有塌缩（%d 个名字 -> %d 个哈希）" % (len(files), len(file_hashes)))
    if len(dir_hashes) != len(dirs):
        problems.append("目录名展开后哈希有塌缩（%d 个名字 -> %d 个哈希）" % (len(dirs), len(dir_hashes)))

    stats_files = stats.get("files", "")
    stats_dirs = stats.get("directories", "")
    stats_exceptions = stats.get("exceptions", "")
    if stats_files and (not stats_files.isdigit() or int(stats_files) != len(files)):
        problems.append("STATS.files=%s 与 FILES chunk %d 条不一致" % (stats_files, len(files)))
    if stats_dirs and (not stats_dirs.isdigit() or int(stats_dirs) != len(dirs)):
        problems.append("STATS.directories=%s 与 DIRS chunk %d 条不一致" % (stats_dirs, len(dirs)))
    if stats_exceptions and (not stats_exceptions.isdigit() or int(stats_exceptions) != len(exceptions)):
        problems.append("STATS.exceptions=%s 与 EXCEPTIONS chunk %d 条不一致" % (stats_exceptions, len(exceptions)))

    return problems


def render_info_txt(parsed):
    """生成不含投稿人本机磁盘路径的只读 info.txt 文本（CRLF）。"""
    meta = parsed.get("meta") or {}
    identity = parsed.get("identity") or {}
    stats = parsed.get("stats") or {}
    dirs = parsed.get("dirs") or []
    files = parsed.get("files") or []
    exceptions = parsed.get("exceptions") or []

    salt = identity.get("salt", "")
    salt_known = identity.get("saltKnown", "0")
    salt_display = salt if salt else "（空串）"
    target_exe = identity.get("target.name", "") or "-"
    target_size = identity.get("target.size", "") or "0"
    target_sha = identity.get("target.sha256", "").upper() or "-"
    sibling_exe = identity.get("sibling.name", "") or "-"
    sibling_sha = identity.get("sibling.sha256", "").upper() or "-"

    lines = [
        "YuaExtract 扩展集信息（只读副本，仅供人看）",
        "工具一律不读本文件；加载只认 rules.int 里的 META chunk。",
        "",
        f"会社：{meta.get('publisher', '')}",
        f"作品：{meta.get('title', '')}",
        f"贡献者：{meta.get('contributor', '')}",
        f"版本：{meta.get('version', '')}",
        f"日期：{meta.get('date', '')}",
        f"留言：{meta.get('comment', '')}",
        f"生成时间：{meta.get('generatedAt', '')}",
        f"工具版本：{meta.get('toolVersion', '')}",
        "",
        f"盐：{salt_display}（saltKnown={salt_known}）",
        f"盐摘要（sha256）：{sha256_text(salt)}",
        f"档案摘要（sha256）：{parsed.get('sha256', '')}",
        f"目标 exe：{target_exe}（{target_size} 字节）",
        f"目标 exe 摘要：{target_sha}",
        f"兄弟 exe：{sibling_exe}",
        f"兄弟 exe 摘要：{sibling_sha}",
        "",
        f"目录条数：{len(dirs)}",
        f"文件条数：{len(files)}",
        f"例外条数：{len(exceptions)}",
        f"去重条数：{stats.get('duplicates', '0')}",
    ]
    return "\r\n".join(lines) + "\r\n"
