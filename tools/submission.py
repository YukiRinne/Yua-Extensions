#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Issue 投稿与维护审批机器人（权限隔离，仅依赖 Python 3 标准库）。

安全边界（复刻 Sakura-Registry 模型）：
  1. check 阶段仅有 contents:read；下载并深度校验投稿的 rules.int（或含 rules.int 的 zip），
     把 candidate.json + rules.int + info.txt 存成 GitHub Actions Artifact。
  2. moderate 阶段（维护者评论 `/approve <run_id>` 或 `/reject ...`）：
     - 校验审批者具备 write/maintain/admin 仓库权限；
     - 校验 Issue 仍为 open 且正文 SHA-256 与检查时完全一致（防审批前调包）；
     - 只从该次成功的 `submission` Artifact 取字节，**绝不重新下载用户链接**；
     - 重新用 yuax 校验档案与摘要后，创建 PR 走 validate 工作流门禁。
"""

import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
import urllib.request
import zipfile

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import yuax  # noqa: E402

MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_ZIP_ENTRIES = 64
MAX_COMMENT_BYTES = 60000
ARTIFACT_NAME = "submission"
SUBMISSION_WORKFLOW_FILE = "submission.yml"
SUBMISSION_DIR = Path(".local/submission")


class SubmissionError(RuntimeError):
    """可控的人可读错误信息（进 Issue 回复或 workflow 日志）。"""


def _load_build_index():
    spec = importlib.util.spec_from_file_location("build_index", TOOLS_DIR / "build-index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def body_sha256(body):
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def parse_issue_sections(body):
    """解析 GitHub Issue Form 生成的 `### <标题>` Markdown 段落。"""
    sections = {}
    current = None
    buf = []
    for raw_line in (body or "").replace("\r\n", "\n").split("\n"):
        if raw_line.startswith("### "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = raw_line[4:].strip()
            buf = []
        elif current is not None:
            buf.append(raw_line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _clean_form_value(text):
    val = (text or "").strip()
    if val in ("_No response_", "*No response*", ""):
        return ""
    return val


def extract_download_url(section_text):
    """从 Issue 文本段提取唯一 HTTPS 下载地址。"""
    text = _clean_form_value(section_text)
    if not text:
        raise SubmissionError("SUBMISSION_EMPTY: 「扩展集文件」未填写，请拖入压缩后的 .zip 或粘贴 HTTPS 链接。")

    urls = []
    # 优先匹配 Markdown 链接 [text](url) 与 HTML href="url"，最后扫描裸 https:// 链接。
    # 裸链接扫描允许 IPv6 host 里的 `[` / `]`，再把仅作外层定界的末尾 `]` / `)` 剥掉。
    for match in re.finditer(r'https?://[^\s<>"\']+', text):
        candidate = match.group(0).rstrip(".,;:")
        if candidate.endswith(")") and candidate.count(")") > candidate.count("("):
            candidate = candidate.rstrip(")")
        if candidate.endswith("]") and candidate.count("]") > candidate.count("["):
            candidate = candidate.rstrip("]")
        candidate = candidate.rstrip(".,;:")
        if candidate and candidate not in urls:
            urls.append(candidate)

    if not urls:
        raise SubmissionError("DOWNLOAD_URL_MISSING: 未在「扩展集文件」中找到下载链接，请拖入 .zip 附件或填写 https:// 链接。")
    if len(urls) > 1:
        raise SubmissionError(f"DOWNLOAD_URL_AMBIGUOUS: 检测到 {len(urls)} 个链接，请每次投稿只提交 1 份文件。")

    url = urls[0]
    validate_public_https_url(url)
    return url


def validate_public_https_url(url):
    try:
        parts = urlsplit((url or "").strip())
    except ValueError as exc:
        raise SubmissionError(f"URL_INVALID: URL 格式无效（{exc}）。") from exc
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise SubmissionError(f"URL_NOT_HTTPS: 只允许公网 https:// 下载地址：{url}")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise SubmissionError("URL_FORBIDDEN_CREDENTIALS_OR_FRAGMENT: 下载地址不允许包含用户名、密码或 #fragment。")
    host = parts.hostname.lower().strip("[]")
    if host == "localhost" or host.endswith(".localhost"):
        raise SubmissionError("URL_HOST_BLOCKED: 不允许访问 localhost。")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise SubmissionError(f"URL_HOST_BLOCKED: 不允许访问非公网 IP（{host}）。")
    return parts


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_https_url(newurl)
        next_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if next_req is not None and "Authorization" in next_req.headers:
            # Cross-host redirects (e.g., github.com -> objects.githubusercontent.com)
            # must never receive GH_TOKEN.
            if urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
                next_req.remove_header("Authorization")
        return next_req


def download_bytes_https(url, max_bytes=MAX_DOWNLOAD_BYTES, github_token=None, timeout=30):
    parts = validate_public_https_url(url)
    headers = {"User-Agent": "Yua-Extensions-Bot/1.0"}
    if github_token and (parts.hostname or "").lower() in ("github.com", "api.github.com"):
        headers["Authorization"] = f"Bearer {github_token}"

    opener = urllib.request.build_opener(_HttpsOnlyRedirect())
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            validate_public_https_url(final_url)
            content_len = resp.headers.get("Content-Length")
            if content_len and content_len.isdigit() and int(content_len) > max_bytes:
                raise SubmissionError(f"DOWNLOAD_TOO_LARGE: 文件声明大小 {content_len} 字节，超过 {max_bytes} 上限。")
            chunks = []
            total = 0
            while True:
                block = resp.read(65536)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise SubmissionError(f"DOWNLOAD_TOO_LARGE: 实际下载超过 {max_bytes} 字节上限。")
                chunks.append(block)
            return b"".join(chunks)
    except HTTPError as exc:
        raise SubmissionError(f"DOWNLOAD_HTTP_ERROR: 下载失败（HTTP {exc.code}）。") from exc
    except URLError as exc:
        raise SubmissionError(f"DOWNLOAD_NETWORK_ERROR: 网络请求失败：{exc.reason}") from exc


def extract_archive_bytes(raw_bytes):
    """从下载到的 .int 或 .zip 字节中安全提取唯一 rules.int 字节。"""
    if not raw_bytes:
        raise SubmissionError("EMPTY_UPLOAD: 下载内容为空（0 字节）。")
    if len(raw_bytes) > MAX_DOWNLOAD_BYTES:
        raise SubmissionError(f"ARCHIVE_TOO_LARGE: 文件超过 {MAX_DOWNLOAD_BYTES} 字节。")

    if raw_bytes.startswith(yuax.MAGIC):
        return raw_bytes

    if raw_bytes.startswith(b"PK\x03\x04") or zipfile.is_zipfile(io.BytesIO(raw_bytes)):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                infos = zf.infolist()
                if len(infos) > MAX_ZIP_ENTRIES:
                    raise SubmissionError(f"ZIP_TOO_MANY_FILES: 压缩包文件数 {len(infos)} 超过 {MAX_ZIP_ENTRIES}。")
                total_uncompressed = 0
                int_candidates = []
                for info in infos:
                    if info.is_dir():
                        continue
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == 0o120000:
                        raise SubmissionError(f"ZIP_SYMLINK_FORBIDDEN: 压缩包不允许符号链接：{info.filename}")
                    norm = info.filename.replace("\\", "/")
                    parts = [p for p in norm.split("/") if p]
                    if norm.startswith("/") or ".." in parts or ":" in norm:
                        raise SubmissionError(f"ZIP_UNSAFE_PATH: 压缩包路径不安全：{info.filename}")
                    if parts and parts[0] == "__MACOSX":
                        continue
                    if info.file_size > MAX_DOWNLOAD_BYTES:
                        raise SubmissionError(f"ZIP_MEMBER_TOO_LARGE: {info.filename} 解压后超过上限。")
                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_DOWNLOAD_BYTES:
                        raise SubmissionError("ZIP_TOTAL_TOO_LARGE: 压缩包解压总大小超过上限。")
                    if norm.lower().endswith(".int"):
                        int_candidates.append(info)

                if not int_candidates:
                    raise SubmissionError("ZIP_NO_INT_FILE: 压缩包里未找到 .int（rules.int）文件。")
                if len(int_candidates) > 1:
                    names = ", ".join(c.filename for c in int_candidates[:5])
                    raise SubmissionError(f"ZIP_MULTIPLE_INT_FILES: 压缩包里包含多个 .int 文件（{names}），每次投稿仅限 1 份。")

                chosen = int_candidates[0]
                with zf.open(chosen, "r") as member:
                    data = member.read(MAX_DOWNLOAD_BYTES + 1)
                if len(data) > MAX_DOWNLOAD_BYTES:
                    raise SubmissionError("ZIP_MEMBER_TOO_LARGE: 解压出的数据超过上限。")
                if not data.startswith(yuax.MAGIC):
                    raise SubmissionError(f"ARCHIVE_BAD_MAGIC: {chosen.filename} 不是有效的 YXS1 扩展集档案。")
                return data
        except zipfile.BadZipFile as exc:
            raise SubmissionError(f"ZIP_CORRUPT: 压缩包损坏：{exc}") from exc

    raise SubmissionError("UNSUPPORTED_FILE_FORMAT: 既不是 rules.int（YXS1），也不是合法 .zip 压缩包。")


def parse_local_index(repo_root):
    index_path = repo_root / yuax.INDEX_FILENAME
    if not index_path.is_file():
        return []
    text = index_path.read_text(encoding="utf-8-sig")
    rows, problems = yuax.parse_index(text)
    if problems:
        raise SubmissionError("MAIN_INDEX_INVALID: " + "; ".join(problems))
    return rows


def load_yanked_file(repo_root):
    path = repo_root / yuax.YANKED_FILENAME
    if not path.is_file():
        return {"schema_version": 1, "yanked": []}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("yanked"), list):
        raise SubmissionError("YANKED_JSON_INVALID")
    return data


def _find_index_slot(rows, safe_pub, safe_title):
    for row in rows:
        r_pub = yuax.safe_component(row["会社"]).lower()
        r_title = yuax.safe_component(row["作品"]).lower()
        if r_pub == safe_pub.lower() and r_title == safe_title.lower():
            return row
    return None


def prepare_upsert_candidate(issue, archive_bytes, repo_root, notes=""):
    with tempfile.NamedTemporaryFile(suffix=".int", delete=False) as tmp:
        tmp.write(archive_bytes)
        tmp_path = Path(tmp.name)
    try:
        parsed = yuax.parse_archive(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)

    problems = yuax.validate_archive_parsed(parsed)
    if problems:
        raise SubmissionError("ARCHIVE_VALIDATION_FAILED: " + "；".join(problems))

    meta = parsed["meta"]
    identity = parsed["identity"]
    publisher = yuax.safe_component(meta["publisher"])
    title = yuax.safe_component(meta["title"])
    archive_sha = parsed["sha256"]
    exe_sha = identity.get("target.sha256", "").strip().upper()
    entry_count = len(parsed["dirs"]) + len(parsed["files"])

    rows = parse_local_index(repo_root)
    existing = _find_index_slot(rows, publisher, title)
    yanked_data = load_yanked_file(repo_root)
    is_yanked = any(
        yuax.safe_component(item.get("publisher", "")).lower() == publisher.lower()
        and yuax.safe_component(item.get("title", "")).lower() == title.lower()
        for item in yanked_data["yanked"]
    )

    if is_yanked:
        raise SubmissionError(
            f"SLOT_CURRENTLY_YANKED: `{publisher}/{title}` 当前处于撤回状态，请先提交“恢复撤回记录”后再更新。"
        )

    if existing and existing["档案摘要"].upper() == archive_sha.upper():
        raise SubmissionError(f"DUPLICATE_ARCHIVE: 库内已有完全相同的 `{publisher}/{title}`（SHA-256 相同），无需重复收录。")

    warnings = []
    for row in rows:
        slot = f"{row['会社']}/{row['作品']}"
        if row["目标exe摘要"].upper() == exe_sha and slot.lower() != f"{publisher}/{title}".lower():
            warnings.append(f"目标 exe 摘要与库内既有条目 `{slot}` 相同，请确认是否同游戏重复投稿。")

    if existing is None:
        status_note = f"新增扩展集（共 {entry_count:,} 条）"
    else:
        old_count = int(existing["条数"] or "0")
        delta = entry_count - old_count
        if delta >= 0:
            status_note = f"更新已有扩展集（原 {old_count:,} 条 → 新 {entry_count:,} 条，+{delta:,} 条）"
        else:
            status_note = f"覆写警告（原 {old_count:,} 条 → 新 {entry_count:,} 条，减少 {-delta:,} 条）"
            warnings.append(status_note)

    info_text = yuax.render_info_txt(parsed)
    candidate = {
        "schema_version": 1,
        "action": "upsert",
        "issue_number": int(issue["number"]),
        "issue_body_sha256": body_sha256(issue.get("body", "")),
        "publisher": publisher,
        "title": title,
        "contributor": meta.get("contributor", ""),
        "version": meta.get("version", ""),
        "entry_count": entry_count,
        "dir_count": len(parsed["dirs"]),
        "file_count": len(parsed["files"]),
        "exe_sha256": exe_sha,
        "archive_sha256": archive_sha,
        "archive_size": len(archive_bytes),
        "status_note": status_note,
        "warnings": warnings,
        "notes": notes,
    }
    return candidate, archive_bytes, info_text


def prepare_change_candidate(issue, sections, repo_root):
    action_text = _clean_form_value(sections.get("操作"))
    publisher_raw = _clean_form_value(sections.get("会社"))
    title_raw = _clean_form_value(sections.get("作品"))
    reason = _clean_form_value(sections.get("原因或说明"))

    publisher = yuax.safe_component(publisher_raw)
    title = yuax.safe_component(title_raw)
    if not publisher or not title:
        raise SubmissionError("SLOT_REQUIRED: 「会社」和「作品」均不能为空。")
    if not reason:
        raise SubmissionError("REASON_REQUIRED: 「原因或说明」不能为空。")

    rows = parse_local_index(repo_root)
    yanked_data = load_yanked_file(repo_root)
    existing = _find_index_slot(rows, publisher, title)
    yanked_item = next(
        (
            item
            for item in yanked_data["yanked"]
            if yuax.safe_component(item.get("publisher", "")).lower() == publisher.lower()
            and yuax.safe_component(item.get("title", "")).lower() == title.lower()
        ),
        None,
    )

    if action_text == "撤回扩展集":
        if existing is None:
            raise SubmissionError(f"ACTIVE_SLOT_NOT_FOUND: 库内找不到在架扩展集 `{publisher}/{title}`。")
        archive_path = repo_root / existing["会社"] / existing["作品"] / yuax.ARCHIVE_FILENAME
        if not archive_path.is_file():
            raise SubmissionError(f"ACTIVE_ARCHIVE_MISSING: 缺少文件 {archive_path}")
        parsed = yuax.parse_archive(str(archive_path))
        if not parsed.get("ok"):
            raise SubmissionError("ACTIVE_ARCHIVE_CORRUPT")
        meta = parsed["meta"]
        identity = parsed["identity"]
        try:
            exe_size = int(identity.get("target.size", "0") or "0")
        except ValueError:
            exe_size = 0
        yank_record = {
            "id": f"{existing['会社']}/{existing['作品']}",
            "publisher": existing["会社"],
            "title": existing["作品"],
            "contributor": meta.get("contributor", ""),
            "version": meta.get("version", ""),
            "date": meta.get("date", ""),
            "comment": meta.get("comment", ""),
            "generated_at": meta.get("generatedAt", ""),
            "tool_version": meta.get("toolVersion", ""),
            "entry_count": len(parsed["dirs"]) + len(parsed["files"]),
            "directory_count": len(parsed["dirs"]),
            "file_count": len(parsed["files"]),
            "exception_count": len(parsed["exceptions"]),
            "exe_name": identity.get("target.name", ""),
            "exe_size": exe_size,
            "exe_sha256": identity.get("target.sha256", "").upper(),
            "sibling_sha256": identity.get("sibling.sha256", "").upper(),
            "salt_sha256": yuax.sha256_text(identity.get("salt", "")),
            "archive_sha256": parsed["sha256"],
            "yank_reason": reason,
            "yanked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return {
            "schema_version": 1,
            "action": "yank",
            "issue_number": int(issue["number"]),
            "issue_body_sha256": body_sha256(issue.get("body", "")),
            "publisher": existing["会社"],
            "title": existing["作品"],
            "reason": reason,
            "status_note": f"撤回扩展集 `{existing['会社']}/{existing['作品']}`（原因：{reason}）",
            "warnings": [],
            "yank_record": yank_record,
        }

    if action_text == "恢复撤回记录":
        if yanked_item is None:
            raise SubmissionError(f"YANKED_SLOT_NOT_FOUND: `yanked.json` 中没有 `{publisher}/{title}` 的撤回记录。")
        backup_archive = repo_root / ".yanked" / yanked_item["publisher"] / yanked_item["title"] / yuax.ARCHIVE_FILENAME
        if not backup_archive.is_file():
            raise SubmissionError(
                f"YANKED_BACKUP_MISSING: 找不到 `.yanked/{yanked_item['publisher']}/{yanked_item['title']}/rules.int` 归档，请直接重新投稿。"
            )
        return {
            "schema_version": 1,
            "action": "unyank",
            "issue_number": int(issue["number"]),
            "issue_body_sha256": body_sha256(issue.get("body", "")),
            "publisher": yanked_item["publisher"],
            "title": yanked_item["title"],
            "reason": reason,
            "status_note": f"恢复已撤回扩展集 `{yanked_item['publisher']}/{yanked_item['title']}`（说明：{reason}）",
            "warnings": [],
        }

    raise SubmissionError(f"ACTION_INVALID: 不支持的操作：{action_text!r}")


def prepare_submission_from_issue(issue, repo_root, github_token=None, fetcher=download_bytes_https):
    """解析 Issue、执行校验并构建 candidate 与可选档案字节。"""
    sections = parse_issue_sections(issue.get("body", ""))
    if "操作" in sections and "扩展集文件" not in sections:
        candidate = prepare_change_candidate(issue, sections, repo_root)
        return candidate, None, None

    if "扩展集文件" not in sections:
        raise SubmissionError("ISSUE_FORM_UNRECOGNIZED: 未识别到标准投稿/变更表单字段。")

    url = extract_download_url(sections["扩展集文件"])
    raw = fetcher(url, MAX_DOWNLOAD_BYTES, github_token)
    archive_bytes = extract_archive_bytes(raw)
    notes = _clean_form_value(sections.get("补充说明"))
    return prepare_upsert_candidate(issue, archive_bytes, repo_root, notes=notes)


def apply_candidate_to_repo(candidate, repo_root, archive_bytes=None, info_text=None):
    """把已审批通过的 candidate 应用到本地仓库工作区并重生成 index.txt。"""
    build_index = _load_build_index()
    action = candidate["action"]
    pub = candidate["publisher"]
    title = candidate["title"]

    if action == "upsert":
        if not archive_bytes:
            raise SubmissionError("CANDIDATE_MISSING_ARCHIVE")
        actual_sha = hashlib.sha256(archive_bytes).hexdigest().upper()
        if actual_sha != candidate["archive_sha256"].upper():
            raise SubmissionError("CANDIDATE_SHA256_MISMATCH")

        slot_dir = repo_root / pub / title
        slot_dir.mkdir(parents=True, exist_ok=True)
        archive_path = slot_dir / yuax.ARCHIVE_FILENAME
        archive_path.write_bytes(archive_bytes)
        parsed = yuax.parse_archive(str(archive_path))
        problems = yuax.validate_archive_parsed(parsed, expected_publisher=pub, expected_title=title)
        if problems:
            raise SubmissionError("ARCHIVE_RECHECK_FAILED: " + "；".join(problems))
        rendered_info = info_text or yuax.render_info_txt(parsed)
        (slot_dir / "info.txt").write_bytes(rendered_info.encode("utf-8"))

    elif action == "yank":
        src_dir = repo_root / pub / title
        if not (src_dir / yuax.ARCHIVE_FILENAME).is_file():
            raise SubmissionError(f"ACTIVE_ARCHIVE_MISSING: {src_dir}")
        dst_dir = repo_root / ".yanked" / pub / title
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        if dst_dir.exists():
            for child in dst_dir.iterdir():
                child.unlink()
        else:
            dst_dir.mkdir(parents=True, exist_ok=True)
        for name in (yuax.ARCHIVE_FILENAME, "info.txt"):
            src_file = src_dir / name
            if src_file.is_file():
                (dst_dir / name).write_bytes(src_file.read_bytes())
                src_file.unlink()
        try:
            src_dir.rmdir()
            pub_dir = repo_root / pub
            if pub_dir.is_dir() and not any(pub_dir.iterdir()):
                pub_dir.rmdir()
        except OSError:
            pass

        yanked_data = load_yanked_file(repo_root)
        yanked_data["yanked"] = [
            item
            for item in yanked_data["yanked"]
            if not (
                yuax.safe_component(item.get("publisher", "")).lower() == yuax.safe_component(pub).lower()
                and yuax.safe_component(item.get("title", "")).lower() == yuax.safe_component(title).lower()
            )
        ]
        yanked_data["yanked"].append(candidate["yank_record"])
        yanked_data["yanked"].sort(key=lambda i: (i["publisher"].lower(), i["title"].lower()))
        (repo_root / yuax.YANKED_FILENAME).write_text(
            json.dumps(yanked_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    elif action == "unyank":
        src_dir = repo_root / ".yanked" / pub / title
        src_archive = src_dir / yuax.ARCHIVE_FILENAME
        if not src_archive.is_file():
            raise SubmissionError(f"YANKED_ARCHIVE_MISSING: {src_archive}")
        parsed = yuax.parse_archive(str(src_archive))
        problems = yuax.validate_archive_parsed(parsed, expected_publisher=pub, expected_title=title)
        if problems:
            raise SubmissionError("RESTORED_ARCHIVE_INVALID: " + "；".join(problems))

        dst_dir = repo_root / pub / title
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / yuax.ARCHIVE_FILENAME).write_bytes(src_archive.read_bytes())
        src_archive.unlink()
        src_info = src_dir / "info.txt"
        if src_info.is_file():
            (dst_dir / "info.txt").write_bytes(src_info.read_bytes())
            src_info.unlink()
        else:
            (dst_dir / "info.txt").write_bytes(yuax.render_info_txt(parsed).encode("utf-8"))
        try:
            src_dir.rmdir()
            pdir = repo_root / ".yanked" / pub
            if pdir.is_dir() and not any(pdir.iterdir()):
                pdir.rmdir()
        except OSError:
            pass

        yanked_data = load_yanked_file(repo_root)
        yanked_data["yanked"] = [
            item
            for item in yanked_data["yanked"]
            if not (
                yuax.safe_component(item.get("publisher", "")).lower() == yuax.safe_component(pub).lower()
                and yuax.safe_component(item.get("title", "")).lower() == yuax.safe_component(title).lower()
            )
        ]
        yanked_path = repo_root / yuax.YANKED_FILENAME
        if yanked_data["yanked"]:
            yanked_path.write_text(
                json.dumps(yanked_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        elif yanked_path.is_file():
            yanked_path.unlink()
    else:
        raise SubmissionError(f"UNKNOWN_CANDIDATE_ACTION: {action}")

    # 重生成 index.txt
    orig_root = build_index.REPO_ROOT
    try:
        build_index.REPO_ROOT = str(repo_root)
        rows, problems = build_index.scan()
    finally:
        build_index.REPO_ROOT = orig_root
    if problems:
        raise SubmissionError("BUILD_INDEX_FAILED: " + "; ".join(problems))
    (repo_root / yuax.INDEX_FILENAME).write_bytes(build_index.render(rows).encode("utf-8"))


# ---------------- GitHub REST 客户端 ----------------


class GitHubClient:
    def __init__(self, token=None, api_base="https://api.github.com"):
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        self.api_base = api_base.rstrip("/")

    def request(self, method, path, payload=None, accept="application/vnd.github+json"):
        url = path if path.startswith("https://") else f"{self.api_base}/{path.lstrip('/')}"
        parts = validate_public_https_url(url)
        headers = {
            "Accept": accept,
            "User-Agent": "Yua-Extensions-Bot/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token and (parts.hostname or "").lower() in ("api.github.com", "github.com"):
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype or raw.lstrip().startswith((b"{", b"[")):
                    return json.loads(raw.decode("utf-8"))
                return raw
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise SubmissionError(f"GITHUB_API_ERROR ({exc.code} {method} {path}): {detail}") from exc

    def download_artifact_zip(self, url):
        # Artifact download URL redirects to Azure Blob Storage (must strip Authorization).
        return download_bytes_https(url, max_bytes=MAX_DOWNLOAD_BYTES, github_token=self.token)


def require_maintainer(api, repository, login):
    if not login:
        raise SubmissionError("MAINTAINER_LOGIN_MISSING")
    perm = api.request("GET", f"repos/{repository}/collaborators/{login}/permission")
    level = perm.get("permission", "")
    if level not in ("admin", "maintain", "write"):
        raise SubmissionError(f"PERMISSION_DENIED: 用户 `{login}` 无审核合并权限（{level}）。")


def require_unchanged_issue(live_issue, approved_issue):
    if live_issue.get("state") != "open":
        raise SubmissionError("ISSUE_NOT_OPEN: Issue 已关闭，不能继续操作。")
    if body_sha256(live_issue.get("body", "")) != body_sha256(approved_issue.get("body", "")):
        raise SubmissionError("ISSUE_CHANGED_RECHECK_REQUIRED: Issue 正文已被修改，请先评论 `/recheck` 重新检查。")


def load_approved_artifact(api, repository, run_id, live_issue):
    run = api.request("GET", f"repos/{repository}/actions/runs/{run_id}")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise SubmissionError(f"CHECK_RUN_NOT_SUCCESS: 检查任务 {run_id} 未成功完成（{run.get('conclusion')}）。")
    if run.get("event") not in ("issues", "issue_comment"):
        raise SubmissionError(f"CHECK_RUN_EVENT_INVALID: 任务 {run_id} 不是由 Issue 检查触发。")
    wf_path = str(run.get("path", ""))
    if SUBMISSION_WORKFLOW_FILE not in wf_path:
        raise SubmissionError(f"CHECK_RUN_WORKFLOW_INVALID: 任务 {run_id} 不是 {SUBMISSION_WORKFLOW_FILE}。")

    artifacts = api.request("GET", f"repos/{repository}/actions/runs/{run_id}/artifacts").get("artifacts", [])
    target = next((a for a in artifacts if a.get("name") == ARTIFACT_NAME and not a.get("expired")), None)
    if target is None:
        raise SubmissionError(f"SUBMISSION_ARTIFACT_MISSING: 任务 {run_id} 中找不到 `{ARTIFACT_NAME}` 产物。")

    raw_zip = api.download_artifact_zip(target["archive_download_url"])
    with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
        if "candidate.json" not in zf.namelist():
            raise SubmissionError("ARTIFACT_MISSING_CANDIDATE")
        candidate = json.loads(zf.read("candidate.json").decode("utf-8"))
        if int(candidate.get("issue_number", -1)) != int(live_issue["number"]):
            raise SubmissionError("CANDIDATE_ISSUE_MISMATCH: 检查编号与当前 Issue 不匹配。")
        if candidate.get("issue_body_sha256") != body_sha256(live_issue.get("body", "")):
            raise SubmissionError("ISSUE_CHANGED_RECHECK_REQUIRED: Issue 正文自检查后已被修改，请 `/recheck`。")
        archive_bytes = zf.read(yuax.ARCHIVE_FILENAME) if yuax.ARCHIVE_FILENAME in zf.namelist() else None
        info_text = zf.read("info.txt").decode("utf-8") if "info.txt" in zf.namelist() else None
        return candidate, archive_bytes, info_text


def _git_run(repo_root, *args):
    import subprocess
    res = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise SubmissionError(f"GIT_ERROR ({' '.join(args)}): {res.stderr.strip()}")
    return res.stdout.strip()


def format_check_comment(candidate, repository, run_id):
    action = candidate["action"]
    lines = [
        f"自动检查通过：**`{candidate['publisher']} / {candidate['title']}`**",
        "",
        f"- 变更类型：{candidate['status_note']}",
    ]
    if action == "upsert":
        lines.extend([
            f"- 贡献者：{candidate.get('contributor') or '-'} · 版本：{candidate.get('version') or '-'}",
            f"- 档案条数：目录 {candidate['dir_count']:,} + 文件 {candidate['file_count']:,} = **{candidate['entry_count']:,}** 条（例外 0 条）",
            f"- 目标 exe SHA-256：`{candidate['exe_sha256']}`",
            f"- 档案 SHA-256：`{candidate['archive_sha256']}`（{candidate['archive_size']:,} 字节）",
        ])
        if candidate.get("notes"):
            lines.append(f"- 补充说明：{candidate['notes']}")
    if candidate.get("warnings"):
        lines.append("")
        lines.append("> **提醒**：" + "；".join(candidate["warnings"]))
    lines.extend([
        "",
        f"[查看检查运行记录](https://github.com/{repository}/actions/runs/{run_id})",
        "",
        f"维护者确认无误后，请评论 `/approve {run_id}`（系统将自动创建 PR、复核并发布到 Releases）。如需拒绝可评论 `/reject 原因`。",
    ])
    return "\n".join(lines)


def open_submission_pulls(api, repository, issue_number):
    pulls = api.request("GET", f"repos/{repository}/pulls?state=open&per_page=100")
    prefix = f"submission/issue-{issue_number}-"
    return [p for p in pulls if p.get("head", {}).get("ref", "").startswith(prefix)]


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: submission.py <check|report|moderate|merge|finish>")
    mode = sys.argv[1]
    repo_root = TOOLS_DIR.parent
    api = GitHubClient()
    repository = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    number = int(event["issue"]["number"])
    issue_ep = f"repos/{repository}/issues/{number}"
    SUBMISSION_DIR.mkdir(parents=True, exist_ok=True)

    if mode == "report":
        report = json.loads((SUBMISSION_DIR / "result.json").read_text(encoding="utf-8"))
        api.request("POST", f"{issue_ep}/comments", {"body": report["body"][:MAX_COMMENT_BYTES]})
        return 0

    if mode == "finish":
        run_url = f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        release_url = os.environ.get("CATALOG_RELEASE", "").strip()
        if release_url:
            api.request("PATCH", issue_ep, {"state": "closed", "state_reason": "completed"})
            body = f"扩展集已收录并发布到 GitHub Releases。[查看发布版本]({release_url})。"
        else:
            stage = os.environ.get("FAILED_STAGE", "处理投稿")
            body = f"{stage}失败。[查看运行记录]({run_url})。"
        api.request("POST", f"{issue_ep}/comments", {"body": body})
        return 0

    live_issue = api.request("GET", issue_ep)
    if "pull_request" in live_issue:
        return 0

    try:
        if mode == "check":
            if os.environ.get("GITHUB_EVENT_NAME") == "issue_comment":
                sender = event["sender"]["login"]
                if sender != live_issue["user"]["login"]:
                    require_maintainer(api, repository, sender)
            candidate, archive_bytes, info_text = prepare_submission_from_issue(
                live_issue, repo_root, github_token=api.token
            )
            (SUBMISSION_DIR / "candidate.json").write_text(
                json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if archive_bytes is not None:
                (SUBMISSION_DIR / yuax.ARCHIVE_FILENAME).write_bytes(archive_bytes)
            if info_text is not None:
                (SUBMISSION_DIR / "info.txt").write_bytes(info_text.encode("utf-8"))
            run_id = os.environ["GITHUB_RUN_ID"]
            (SUBMISSION_DIR / "result.json").write_text(
                json.dumps({"body": format_check_comment(candidate, repository, run_id)}, ensure_ascii=False),
                encoding="utf-8",
            )
            return 0

        if mode == "moderate":
            require_maintainer(api, repository, event["sender"]["login"])
            command = event["comment"]["body"].strip()
            match = re.fullmatch(r"/approve ([0-9]+)", command)
            if match:
                run_id = match.group(1)
                require_unchanged_issue(live_issue, event["issue"])
                candidate, archive_bytes, info_text = load_approved_artifact(api, repository, run_id, live_issue)
                for old_pr in open_submission_pulls(api, repository, number):
                    api.request("PATCH", f"repos/{repository}/pulls/{old_pr['number']}", {"state": "closed"})

                repo_info = api.request("GET", f"repos/{repository}")
                default_branch = repo_info["default_branch"]
                base_sha = _git_run(repo_root, "rev-parse", "HEAD")
                branch = f"submission/issue-{number}-{run_id}"
                _git_run(repo_root, "checkout", "-B", branch)
                apply_candidate_to_repo(candidate, repo_root, archive_bytes=archive_bytes, info_text=info_text)
                _git_run(repo_root, "config", "user.name", "github-actions[bot]")
                _git_run(repo_root, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
                _git_run(repo_root, "add", "-A")
                _git_run(
                    repo_root,
                    "commit",
                    "-m",
                    f"extensions({candidate['action']}): {candidate['publisher']}/{candidate['title']} (#{number})",
                )
                head_sha = _git_run(repo_root, "rev-parse", "HEAD")
                _git_run(repo_root, "push", "-f", "origin", branch)

                pull = api.request("POST", f"repos/{repository}/pulls", {
                    "title": f"[投稿 #{number}] {candidate['action']}: {candidate['publisher']} / {candidate['title']}",
                    "head": branch,
                    "base": default_branch,
                    "body": f"Closes #{number}\n\n来自自动检查任务 {run_id}。\n\n{candidate['status_note']}",
                })
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
                    out.write(f"pull_request={pull['number']}\nhead_sha={head_sha}\nbase_sha={base_sha}\n")
                return 0

            if command == "/reject" or command.startswith("/reject "):
                if live_issue.get("state") == "closed":
                    return 0
                reason = command.removeprefix("/reject").strip() or "本次投稿未收录。"
                for old_pr in open_submission_pulls(api, repository, number):
                    api.request("PATCH", f"repos/{repository}/pulls/{old_pr['number']}", {"state": "closed"})
                api.request("POST", f"{issue_ep}/comments", {"body": reason})
                api.request("PATCH", issue_ep, {"state": "closed", "state_reason": "not_planned"})
                return 0

            raise SubmissionError("COMMAND_INVALID: 请使用 `/approve <检查编号>` 或 `/reject [原因]`。")

        if mode == "merge":
            require_maintainer(api, repository, event["sender"]["login"])
            require_unchanged_issue(live_issue, event["issue"])
            pr_number = os.environ["PULL_REQUEST"]
            head_sha = os.environ["HEAD_SHA"]
            api.request("PUT", f"repos/{repository}/pulls/{pr_number}/merge", {
                "merge_method": "squash",
                "sha": head_sha,
            })
            return 0

        raise SubmissionError(f"UNKNOWN_MODE: {mode}")
    except (SubmissionError, OSError, ValueError, zipfile.BadZipFile) as exc:
        if mode == "check":
            body = f"自动检查未通过：\n\n```text\n{exc}\n```\n\n修正后编辑 Issue 或评论 `/recheck` 重新检查。"
            (SUBMISSION_DIR / "result.json").write_text(
                json.dumps({"body": body}, ensure_ascii=False), encoding="utf-8"
            )
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
