#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yua-Extensions 仓库工具链与投稿工作流单元测试（纯标准库）。"""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import yuax  # noqa: E402
import submission  # noqa: E402


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_catalog = _load_module("build_catalog", "build-catalog.py")
build_index = _load_module("build_index", "build-index.py")


def build_synthetic_yxs1(
    publisher="TestPub",
    title="TestGame",
    salt="salt123",
    dirs=None,
    files=None,
    exe_sha="A" * 64,
    contributor="Tester",
    version="1.0.0",
    generated_at="2026-11-20 10:00:00",
    exceptions=None,
):
    dirs = dirs if dirs is not None else ["/", "system/"]
    files = files if files is not None else ["title.ks", "config.tjs"]
    exceptions = exceptions if exceptions is not None else []

    meta_text = "\n".join([
        f"publisher={publisher}",
        f"title={title}",
        f"contributor={contributor}",
        f"version={version}",
        "date=2026-11-20",
        "comment=test archive",
        f"generatedAt={generated_at}",
        "toolVersion=YuaExtract extension-set/1",
    ])
    identity_text = "\n".join([
        f"target.name={title}.exe",
        "target.size=123456",
        f"target.sha256={exe_sha}",
        "sibling.name=",
        "sibling.size=0",
        "sibling.sha256=",
        f"salt={salt}",
        "saltKnown=1",
        "source.dir=E:\\Secret\\LocalPath\\ShouldNotLeak",
    ])
    stats_text = "\n".join([
        f"directories={len(dirs)}",
        f"files={len(files)}",
        f"exceptions={len(exceptions)}",
        "duplicates=0",
    ])

    raw_chunks = [
        (0x0001, meta_text.encode("utf-8")),
        (0x0002, identity_text.encode("utf-8")),
        (0x0003, "\n".join(dirs).encode("utf-8")),
        (0x0004, "\n".join(files).encode("utf-8")),
        (0x0005, "\n".join(exceptions).encode("utf-8")),
        (0x0007, stats_text.encode("utf-8")),
    ]
    payload = bytearray()
    entries = []
    for cid, data in raw_chunks:
        offset = len(payload)
        payload.extend(data)
        entries.append((cid, 0, offset, len(data)))

    crc = zlib.crc32(bytes(payload)) & 0xFFFFFFFF
    compressed = zlib.compress(bytes(payload))
    header = struct.pack("<4sHHIII", b"YXS1", 1, 20, yuax.FLAG_ZLIB, crc, len(entries))
    table = b"".join(struct.pack("<HHII", cid, flg, off, sz) for cid, flg, off, sz in entries)
    return header + table + compressed


def make_zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestYuaxHelpers(unittest.TestCase):
    def test_ascii_asset_name_and_reserved_names(self):
        sha = "3A12FD2BA90A4EDC4F8857D2A5501DA3FB91CA64F9AECA7B00052D970F90DABC"
        name = yuax.ascii_asset_name("Yuzu", "limelight_lj", sha)
        self.assertEqual(name, "Yuzu__limelight_lj__3a12fd2ba90a.int")

        jp_name = yuax.ascii_asset_name("ゆずソフト", "喫茶ステラ", sha)
        self.assertTrue(jp_name.startswith("ext_"))
        self.assertTrue(jp_name.endswith("__3a12fd2ba90a.int"))
        self.assertTrue(jp_name.isascii())

        long_name = yuax.ascii_asset_name("A" * 60, "B" * 60, sha)
        self.assertLessEqual(len(long_name), 80)

        self.assertTrue(yuax.is_windows_reserved_name("CON"))
        self.assertTrue(yuax.is_windows_reserved_name("com1.txt"))
        self.assertFalse(yuax.is_windows_reserved_name("Yuzu"))

    def test_render_info_txt_excludes_local_path(self):
        blob = build_synthetic_yxs1()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rules.int"
            p.write_bytes(blob)
            parsed = yuax.parse_archive(str(p))
            self.assertEqual(yuax.validate_archive_parsed(parsed), [])
            info = yuax.render_info_txt(parsed)
            self.assertNotIn("ShouldNotLeak", info)
            self.assertIn("TestPub", info)


class TestBuildCatalog(unittest.TestCase):
    def test_real_repo_build_and_prepare_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            assets = Path(tmp) / "assets"
            catalog = build_catalog.build_distribution(
                REPO_ROOT, dist, "https://example.invalid", release_tag="test-tag"
            )
            self.assertEqual(catalog["schema_version"], 1)
            self.assertGreaterEqual(len(catalog["entries"]), 1)
            slot = "Yuzu Soft/LimeLight Lemonade Jam"
            yuzu = next(e for e in catalog["entries"] if e["id"] == slot)
            self.assertEqual(yuzu["entry_count"], 85285)
            self.assertEqual(yuzu["directory_count"], 49)
            self.assertEqual(yuzu["file_count"], 85236)
            self.assertEqual(yuzu["contributor"], "Rinne")
            self.assertEqual(yuzu["version"], "v1.0")
            self.assertEqual(
                yuzu["package"]["path"],
                f"assets/Yuzu_Soft__LimeLight_Lemonade_Jam__{yuzu['archive_sha256'][:12].lower()}.int",
            )

            prepped = build_catalog.prepare_release_assets(
                dist,
                assets,
                "https://github.com/YukiRinne/Yua-Extensions/releases/download/vtest",
                release_tag="vtest",
            )
            self.assertEqual(prepped["release_tag"], "vtest")
            out_yuzu = next(e for e in prepped["entries"] if e["id"] == slot)
            asset_file = assets / out_yuzu["package"]["path"]
            self.assertTrue(asset_file.is_file())
            self.assertEqual(
                yuax.sha256_file(str(asset_file)),
                out_yuzu["archive_sha256"],
            )
            self.assertTrue(
                out_yuzu["package"]["url"].startswith(
                    "https://github.com/YukiRinne/Yua-Extensions/releases/download/vtest/"
                )
            )
            self.assertTrue((assets / "catalog.json").is_file())
            self.assertTrue((assets / "index.txt").is_file())

    def test_non_ascii_and_yanked_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            slot = repo / "ゆずソフト" / "喫茶ステラ"
            slot.mkdir(parents=True)
            blob = build_synthetic_yxs1(publisher="ゆずソフト", title="喫茶ステラ", exe_sha="B" * 64)
            (slot / "rules.int").write_bytes(blob)

            # Build index.txt matching disk
            orig_root = build_index.REPO_ROOT
            try:
                build_index.REPO_ROOT = str(repo)
                rows, probs = build_index.scan()
                self.assertEqual(probs, [])
                (repo / "index.txt").write_bytes(build_index.render(rows).encode("utf-8"))
            finally:
                build_index.REPO_ROOT = orig_root

            yanked = {
                "schema_version": 1,
                "yanked": [{
                    "id": "OldStudio/BrokenGame",
                    "publisher": "OldStudio",
                    "title": "BrokenGame",
                    "entry_count": 100,
                    "exe_sha256": "C" * 64,
                    "archive_sha256": "D" * 64,
                    "yank_reason": "测试撤回",
                }],
            }
            (repo / "yanked.json").write_text(json.dumps(yanked), encoding="utf-8")

            dist = Path(tmp) / "dist"
            cat = build_catalog.build_distribution(repo, dist, "https://example.invalid")
            self.assertEqual(len(cat["entries"]), 2)
            y_item = next(e for e in cat["entries"] if e["id"] == "OldStudio/BrokenGame")
            self.assertTrue(y_item["yanked"])
            self.assertIsNone(y_item["package"])
            jp_item = next(e for e in cat["entries"] if e["id"] == "ゆずソフト/喫茶ステラ")
            self.assertTrue(Path(jp_item["package"]["path"]).name.isascii())


class TestSubmissionWorkflow(unittest.TestCase):
    def _setup_mini_repo(self, root):
        shutil.copytree(REPO_ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        slot = root / "StudioA" / "GameOne"
        slot.mkdir(parents=True)
        blob = build_synthetic_yxs1(
            publisher="StudioA",
            title="GameOne",
            dirs=["/", "a/", "b/"],
            files=["1.png", "2.png", "3.png"],
            exe_sha="1" * 64,
        )
        (slot / "rules.int").write_bytes(blob)
        orig = build_index.REPO_ROOT
        try:
            build_index.REPO_ROOT = str(root)
            rows, _ = build_index.scan()
            (root / "index.txt").write_bytes(build_index.render(rows).encode("utf-8"))
        finally:
            build_index.REPO_ROOT = orig
        return blob

    def test_extract_url_and_zip_security(self):
        with self.assertRaises(submission.SubmissionError):
            submission.extract_download_url("http://example.com/a.zip")
        with self.assertRaises(submission.SubmissionError):
            submission.extract_download_url("https://127.0.0.1/a.zip")
        with self.assertRaises(submission.SubmissionError):
            submission.extract_download_url("https://[::1]/a.zip")
        with self.assertRaises(submission.SubmissionError):
            submission.extract_download_url("https://a.com/1.zip https://a.com/2.zip")

        url = submission.extract_download_url("[rules.zip](https://github.com/user-attachments/files/1/rules.zip)")
        self.assertEqual(url, "https://github.com/user-attachments/files/1/rules.zip")

        bad_zip = make_zip_bytes({"../rules.int": build_synthetic_yxs1()})
        with self.assertRaises(submission.SubmissionError):
            submission.extract_archive_bytes(bad_zip)

        multi_zip = make_zip_bytes({"a.int": build_synthetic_yxs1(), "b.int": build_synthetic_yxs1()})
        with self.assertRaises(submission.SubmissionError):
            submission.extract_archive_bytes(multi_zip)

        good_blob = build_synthetic_yxs1()
        good_zip = make_zip_bytes({"nested/rules.int": good_blob})
        self.assertEqual(submission.extract_archive_bytes(good_zip), good_blob)

    def test_duplicate_downgrade_and_yank_unyank_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            old_blob = self._setup_mini_repo(repo)
            old_zip = make_zip_bytes({"rules.int": old_blob})

            dup_issue = {
                "number": 10,
                "state": "open",
                "body": "### 扩展集文件\n\nhttps://github.com/user-attachments/files/10/rules.zip\n",
            }
            with self.assertRaises(submission.SubmissionError) as ctx:
                submission.prepare_submission_from_issue(
                    dup_issue, repo, fetcher=lambda *_args: old_zip
                )
            self.assertIn("DUPLICATE_ARCHIVE", str(ctx.exception))

            smaller_blob = build_synthetic_yxs1(
                publisher="StudioA", title="GameOne", dirs=["/"], files=["1.png"], exe_sha="1" * 64
            )
            smaller_zip = make_zip_bytes({"rules.int": smaller_blob})
            cand, out_blob, info = submission.prepare_submission_from_issue(
                dup_issue, repo, fetcher=lambda *_args: smaller_zip
            )
            self.assertEqual(cand["action"], "upsert")
            self.assertTrue(any("覆写警告" in w for w in cand["warnings"]))
            self.assertEqual(out_blob, smaller_blob)

            # Apply update
            submission.apply_candidate_to_repo(cand, repo, archive_bytes=out_blob, info_text=info)
            rows = submission.parse_local_index(repo)
            self.assertEqual(rows[0]["条数"], "2")

            # Yank -> archive moved to .yanked/ and yanked.json written
            yank_issue = {
                "number": 11,
                "state": "open",
                "body": "### 操作\n\n撤回扩展集\n\n### 会社\n\nStudioA\n\n### 作品\n\nGameOne\n\n### 原因或说明\n\n条目有误\n",
            }
            y_cand, _, _ = submission.prepare_submission_from_issue(yank_issue, repo)
            self.assertEqual(y_cand["action"], "yank")
            submission.apply_candidate_to_repo(y_cand, repo)
            self.assertFalse((repo / "StudioA" / "GameOne" / "rules.int").exists())
            self.assertTrue((repo / ".yanked" / "StudioA" / "GameOne" / "rules.int").is_file())
            self.assertEqual(submission.parse_local_index(repo), [])

            # Unyank -> restored
            unyank_issue = {
                "number": 12,
                "state": "open",
                "body": "### 操作\n\n恢复撤回记录\n\n### 会社\n\nStudioA\n\n### 作品\n\nGameOne\n\n### 原因或说明\n\n已确认恢复\n",
            }
            u_cand, _, _ = submission.prepare_submission_from_issue(unyank_issue, repo)
            self.assertEqual(u_cand["action"], "unyank")
            submission.apply_candidate_to_repo(u_cand, repo)
            self.assertTrue((repo / "StudioA" / "GameOne" / "rules.int").is_file())
            self.assertFalse((repo / "yanked.json").exists())
            self.assertEqual(len(submission.parse_local_index(repo)), 1)

    def test_tamper_and_permission_checks(self):
        class FakeApi:
            def __init__(self, perm="read", body="body-v1"):
                self.perm = perm
                self.body = body
                self.blob = build_synthetic_yxs1()

            def request(self, _method, path, payload=None):
                if "/permission" in path:
                    return {"permission": self.perm}
                if "/actions/runs/99/artifacts" in path:
                    return {
                        "artifacts": [{
                            "name": "submission",
                            "expired": False,
                            "archive_download_url": "https://api.github.com/repos/o/r/actions/artifacts/1/zip",
                        }]
                    }
                if "/actions/runs/99" in path:
                    return {
                        "status": "completed",
                        "conclusion": "success",
                        "event": "issues",
                        "path": ".github/workflows/submission.yml",
                    }
                return {}

            def download_artifact_zip(self, _url):
                cand = {
                    "schema_version": 1,
                    "action": "upsert",
                    "issue_number": 5,
                    "issue_body_sha256": submission.body_sha256(self.body),
                    "archive_sha256": hashlib.sha256(self.blob).hexdigest().upper(),
                }
                return make_zip_bytes({
                    "candidate.json": json.dumps(cand).encode("utf-8"),
                    "rules.int": self.blob,
                    "info.txt": b"ok",
                })

        with self.assertRaises(submission.SubmissionError):
            submission.require_maintainer(FakeApi(perm="read"), "o/r", "attacker")
        submission.require_maintainer(FakeApi(perm="admin"), "o/r", "owner")

        api = FakeApi(body="original body")
        edited_issue = {"number": 5, "state": "open", "body": "tampered URL!"}
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.load_approved_artifact(api, "o/r", "99", edited_issue)
        self.assertIn("ISSUE_CHANGED_RECHECK_REQUIRED", str(ctx.exception))

        ok_issue = {"number": 5, "state": "open", "body": "original body"}
        cand, raw, _ = submission.load_approved_artifact(api, "o/r", "99", ok_issue)
        self.assertEqual(cand["issue_number"], 5)
        self.assertEqual(raw, api.blob)


if __name__ == "__main__":
    unittest.main()
