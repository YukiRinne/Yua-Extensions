#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 Yua-Extensions 静态目录产物（catalog.json + index.txt + ASCII 命名的 .int 资产）
并准备 GitHub Releases 原子上传资产目录。

仅依赖 Python 3 标准库。

用法：
  1) 生成发布目录（build 阶段只读执行）：
     python tools/build-catalog.py --output dist/registry --base-url https://example.invalid

  2) 准备 GitHub Release 上传文件（publish 阶段执行，扁平复制并改写固定下载 URL）：
     python tools/build-catalog.py --prepare-release-assets \\
       --distribution dist/registry --output dist/assets \\
       --base-url https://github.com/YukiRinne/Yua-Extensions/releases/download/<tag> \\
       --release-tag <tag>
"""

import argparse
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import yuax  # noqa: E402

DEFAULT_REPO_ROOT = TOOLS_DIR.parent
DEFAULT_REPOSITORY_URL = "https://github.com/YukiRinne/Yua-Extensions"


def _load_build_index():
    spec = importlib.util.spec_from_file_location("build_index", TOOLS_DIR / "build-index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate_base_url(url):
    text = (url or "").strip()
    parts = urlsplit(text)
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise ValueError(f"INVALID_BASE_URL: must be https with host: {url!r}")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"INVALID_BASE_URL: credentials forbidden: {url!r}")
    if parts.query or parts.fragment:
        raise ValueError(f"INVALID_BASE_URL: query/fragment forbidden: {url!r}")
    return text.rstrip("/")


def ensure_empty_output_dir(path):
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"OUTPUT_NOT_DIR: {path}")
        if any(path.iterdir()):
            raise ValueError(f"OUTPUT_DIR_NOT_EMPTY: {path}")
    else:
        path.mkdir(parents=True, exist_ok=False)


def load_yanked_entries(repo_root):
    yanked_path = repo_root / yuax.YANKED_FILENAME
    if not yanked_path.is_file():
        return []
    data = json.loads(yanked_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"INVALID_YANKED_SCHEMA: {yanked_path}")
    entries = data.get("yanked")
    if not isinstance(entries, list):
        raise ValueError(f"INVALID_YANKED_LIST: {yanked_path}")
    normalized = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("INVALID_YANKED_ITEM")
        pub = str(item.get("publisher", "")).strip()
        title = str(item.get("title", "")).strip()
        reason = str(item.get("yank_reason", "")).strip()
        if not pub or not title or not reason:
            raise ValueError(f"INVALID_YANKED_ITEM_FIELDS: {item!r}")
        slot = (yuax.safe_component(pub).lower(), yuax.safe_component(title).lower())
        if not slot[0] or not slot[1]:
            raise ValueError(f"INVALID_YANKED_SLOT: {pub}/{title}")
        if slot in seen:
            raise ValueError(f"DUPLICATE_YANKED_SLOT: {pub}/{title}")
        seen.add(slot)
        entry = {
            "id": f"{pub}/{title}",
            "publisher": pub,
            "title": title,
            "contributor": str(item.get("contributor", "")),
            "version": str(item.get("version", "")),
            "date": str(item.get("date", "")),
            "comment": str(item.get("comment", "")),
            "generated_at": str(item.get("generated_at", "")),
            "tool_version": str(item.get("tool_version", "")),
            "entry_count": int(item.get("entry_count", 0)),
            "directory_count": int(item.get("directory_count", 0)),
            "file_count": int(item.get("file_count", 0)),
            "exception_count": int(item.get("exception_count", 0)),
            "exe_name": str(item.get("exe_name", "")),
            "exe_size": int(item.get("exe_size", 0)),
            "exe_sha256": str(item.get("exe_sha256", "")).upper(),
            "sibling_sha256": str(item.get("sibling_sha256", "")).upper(),
            "salt_sha256": str(item.get("salt_sha256", "")).upper(),
            "archive_sha256": str(item.get("archive_sha256", "")).upper(),
            "yanked": True,
            "yank_reason": reason,
            "package": None,
        }
        normalized.append((slot, entry))
    return normalized


def build_distribution(repo_root, output_dir, base_url, release_tag="", repository_url=DEFAULT_REPOSITORY_URL):
    base = validate_base_url(base_url)
    ensure_empty_output_dir(output_dir)

    build_index = _load_build_index()
    entries = []
    seen_slots = set()
    seen_assets = set()
    seen_exe = {}
    rows = []

    for pub_dir in sorted(repo_root.iterdir(), key=lambda p: p.name.lower()):
        if not pub_dir.is_dir() or pub_dir.name.startswith("."):
            continue
        for title_dir in sorted(pub_dir.iterdir(), key=lambda p: p.name.lower()):
            archive_path = title_dir / yuax.ARCHIVE_FILENAME
            if not title_dir.is_dir() or not archive_path.is_file():
                continue

            publisher = pub_dir.name
            title = title_dir.name
            parsed = yuax.parse_archive(str(archive_path))
            problems = yuax.validate_archive_parsed(
                parsed, expected_publisher=publisher, expected_title=title
            )
            if problems:
                raise ValueError(f"{publisher}/{title}: " + "; ".join(problems))

            safe_slot = (yuax.safe_component(publisher).lower(), yuax.safe_component(title).lower())
            if safe_slot in seen_slots:
                raise ValueError(f"DUPLICATE_SLOT: {publisher}/{title}")
            seen_slots.add(safe_slot)

            meta = parsed["meta"]
            identity = parsed["identity"]
            exe_sha = identity.get("target.sha256", "").strip().upper()
            sibling_sha = identity.get("sibling.sha256", "").strip().upper()
            salt = identity.get("salt", "")
            salt_sha = yuax.sha256_text(salt)
            archive_sha = parsed["sha256"]
            dir_count = len(parsed["dirs"])
            file_count = len(parsed["files"])
            exc_count = len(parsed["exceptions"])
            total_count = dir_count + file_count

            if exe_sha in seen_exe and seen_exe[exe_sha] != f"{publisher}/{title}":
                print(
                    f"[warn] {publisher}/{title} shares exe_sha256 with {seen_exe[exe_sha]}",
                    file=sys.stderr,
                )
            seen_exe.setdefault(exe_sha, f"{publisher}/{title}")

            asset_name = yuax.ascii_asset_name(publisher, title, archive_sha)
            if asset_name in seen_assets:
                raise ValueError(f"DUPLICATE_ASSET_NAME: {asset_name}")
            seen_assets.add(asset_name)

            assets_dir = output_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive_path, assets_dir / asset_name)

            try:
                exe_size = int(identity.get("target.size", "0") or "0")
            except ValueError:
                exe_size = 0

            entries.append({
                "id": f"{publisher}/{title}",
                "publisher": publisher,
                "title": title,
                "contributor": meta.get("contributor", ""),
                "version": meta.get("version", ""),
                "date": meta.get("date", ""),
                "comment": meta.get("comment", ""),
                "generated_at": meta.get("generatedAt", ""),
                "tool_version": meta.get("toolVersion", ""),
                "entry_count": total_count,
                "directory_count": dir_count,
                "file_count": file_count,
                "exception_count": exc_count,
                "exe_name": identity.get("target.name", ""),
                "exe_size": exe_size,
                "exe_sha256": exe_sha,
                "sibling_sha256": sibling_sha,
                "salt_sha256": salt_sha,
                "archive_sha256": archive_sha,
                "yanked": False,
                "yank_reason": "",
                "package": {
                    "path": f"assets/{asset_name}",
                    "url": f"{base}/assets/{asset_name}",
                    "size": parsed["raw_size"],
                    "sha256": archive_sha,
                },
            })

            rows.append({
                "会社": publisher,
                "作品": title,
                "目标exe摘要": exe_sha,
                "兄弟exe摘要": sibling_sha,
                "盐摘要": salt_sha,
                "档案摘要": archive_sha,
                "条数": str(total_count),
                "生成时间": meta.get("generatedAt", "") or "-",
                "安装时间": "-",
                "来源": build_index.SOURCE_LABEL,
            })

    rows.sort(key=lambda r: (r["会社"].lower(), r["作品"].lower()))
    rendered_index = build_index.render(rows)

    existing_index = repo_root / yuax.INDEX_FILENAME
    if existing_index.is_file():
        existing_text = existing_index.read_text(encoding="utf-8-sig")
        if build_index.normalize_lines(existing_text) != build_index.normalize_lines(rendered_index):
            raise ValueError("INDEX_OUT_OF_SYNC: run python tools/build-index.py")

    for slot, yanked_entry in load_yanked_entries(repo_root):
        if slot in seen_slots:
            raise ValueError(
                f"YANKED_SLOT_ALSO_ACTIVE: {yanked_entry['publisher']}/{yanked_entry['title']}"
            )
        seen_slots.add(slot)
        entries.append(yanked_entry)

    entries.sort(key=lambda e: (e["publisher"].lower(), e["title"].lower()))

    catalog = {
        "schema_version": 1,
        "repository": repository_url.rstrip("/"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "release_tag": release_tag or "",
        "entries": entries,
    }

    catalog_path = output_dir / "catalog" / "v1" / yuax.CATALOG_FILENAME
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / yuax.INDEX_FILENAME).write_bytes(rendered_index.encode("utf-8"))
    return catalog


def prepare_release_assets(distribution_dir, output_dir, base_url, release_tag=""):
    base = validate_base_url(base_url)
    dist_root = distribution_dir.resolve(strict=True)
    catalog_path = dist_root / "catalog" / "v1" / yuax.CATALOG_FILENAME
    index_path = dist_root / yuax.INDEX_FILENAME
    if not catalog_path.is_file():
        raise ValueError(f"CATALOG_MISSING: {catalog_path}")
    if not index_path.is_file():
        raise ValueError(f"INDEX_MISSING: {index_path}")

    ensure_empty_output_dir(output_dir)
    catalog = copy.deepcopy(json.loads(catalog_path.read_text(encoding="utf-8")))
    if release_tag:
        catalog["release_tag"] = release_tag

    seen_names = set()
    for entry in catalog.get("entries", []):
        package = entry.get("package")
        if package is None:
            continue
        src = (dist_root / package["path"]).resolve(strict=True)
        if not src.is_relative_to(dist_root):
            raise ValueError(f"PACKAGE_OUTSIDE_DISTRIBUTION: {package['path']}")
        name = Path(package["path"]).name
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.int", name):
            raise ValueError(f"ASSET_NAME_INVALID: {name}")
        if name in seen_names:
            raise ValueError(f"DUPLICATE_ASSET_NAME: {name}")
        seen_names.add(name)

        dst = output_dir / name
        shutil.copyfile(src, dst)
        actual_sha = yuax.sha256_file(str(dst))
        if actual_sha != str(package.get("sha256", "")).upper():
            raise ValueError(f"ASSET_SHA256_MISMATCH: {name}")
        package["path"] = name
        package["url"] = f"{base}/{name}"

    (output_dir / yuax.CATALOG_FILENAME).write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copyfile(index_path, output_dir / yuax.INDEX_FILENAME)
    return catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-release-assets", action="store_true")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--distribution", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY_URL)
    args = parser.parse_args()

    if args.prepare_release_assets:
        if not args.distribution:
            parser.error("--distribution is required with --prepare-release-assets")
        catalog = prepare_release_assets(
            args.distribution, args.output, args.base_url, release_tag=args.release_tag
        )
        print(
            f"[release-assets] prepared {len(catalog['entries'])} entries -> {args.output}"
        )
    else:
        catalog = build_distribution(
            args.repo.resolve(),
            args.output,
            args.base_url,
            release_tag=args.release_tag,
            repository_url=args.repository,
        )
        print(f"[build-catalog] built {len(catalog['entries'])} entries -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
