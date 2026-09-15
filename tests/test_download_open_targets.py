"""Offline archive-downloader tests using tiny files and mocked HTTP responses."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


SPEC = importlib.util.spec_from_file_location(
    "download_open_targets", Path(__file__).resolve().parents[1] / "tools/download_open_targets.py")
archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive)


def parquet_bytes(metadata=b"test-metadata"):
    # Only the framing is needed; the downloader does not parse Thrift metadata.
    return b"PAR1" + metadata + len(metadata).to_bytes(4, "little") + b"PAR1"


class Response:
    def __init__(self, data, status=200, headers=None, fail_after=None):
        self.status = status
        self.headers = {"Content-Length": str(len(data)), "ETag": '"archive-file"'}
        self.headers.update(headers or {})
        self.data = io.BytesIO(data)
        self.fail_after = fail_after

    def read(self, size=-1):
        if self.fail_after is not None:
            if self.data.tell() >= self.fail_after:
                raise URLError("interrupted transfer")
            size = min(size, self.fail_after - self.data.tell())
        return self.data.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.data.close()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.relative = "disease/part-000.parquet"
        self.url = archive.BASE + self.relative
        self.target = self.root / self.relative
        self.data = parquet_bytes()
        self.metadata = {"bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest(), "url": self.url}
        self.pause = patch.object(archive.time, "sleep")
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def partial(self, data, validator='"archive-file"', url=None):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        path = self.target.with_name(self.target.name + ".part")
        path.write_bytes(data)
        path.with_name(path.name + ".json").write_text(json.dumps({
            "url": url or self.url, "validator": validator}))
        return path

    def download(self, previous=None, stop=None):
        return archive.download(self.root, self.relative, self.url, previous, stop)

    def test_completed_file_is_rehashed_without_network(self):
        self.target.parent.mkdir()
        self.target.write_bytes(self.data)
        with patch.object(archive, "urlopen") as request:
            self.assertEqual(self.download(self.metadata), self.metadata)
        request.assert_not_called()

    def test_same_size_corruption_is_repaired_and_revalidated(self):
        self.target.parent.mkdir()
        damaged = bytearray(self.data)
        damaged[6] ^= 1
        self.target.write_bytes(damaged)
        with patch.object(archive, "urlopen", return_value=Response(self.data)) as request:
            self.assertEqual(self.download(self.metadata), self.metadata)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_manifest_entry_from_another_url_is_not_reused(self):
        self.target.parent.mkdir()
        self.target.write_bytes(self.data)
        old = {**self.metadata, "url": "https://other.example/file.parquet"}
        with patch.object(archive, "urlopen", return_value=Response(self.data)) as request:
            self.assertEqual(self.download(old), self.metadata)
        request.assert_called_once()

    def test_partial_resume_checks_range_and_preserves_validator(self):
        partial = self.partial(self.data[:7])
        headers = {"Content-Range": f"bytes 7-{len(self.data)-1}/{len(self.data)}"}
        progress = archive.Progress(1)
        with patch.object(archive, "urlopen", return_value=Response(self.data[7:], 206, headers)) as request:
            self.assertEqual(archive.download(self.root, self.relative, self.url, None, progress=progress), self.metadata)
        self.assertEqual(progress.received, len(self.data) - 7)
        self.assertEqual(progress.active, {})
        sent = request.call_args.args[0]
        self.assertEqual(sent.get_header("Range"), "bytes=7-")
        self.assertEqual(sent.get_header("If-range"), '"archive-file"')
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertFalse(partial.exists())
        self.assertFalse(partial.with_name(partial.name + ".json").exists())

    def test_progress_is_printed_while_a_file_is_still_transferring(self):
        waiting, reported = threading.Event(), threading.Event()
        progress = archive.Progress(1, interval=0.01)
        original_report = progress.report
        output = io.StringIO()

        def report():
            original_report()
            if waiting.is_set():
                reported.set()

        class SlowResponse(Response):
            def read(self, size=-1):
                if self.data.tell() == 1024**2:
                    waiting.set()
                    if not reported.wait(5):
                        raise AssertionError("No progress was reported during the transfer")
                return super().read(size)

        data = parquet_bytes(b"x" * 2 * 1024**2)
        with patch.object(archive, "urlopen", return_value=SlowResponse(data)), \
                patch.object(progress, "report", side_effect=report), patch("sys.stdout", output):
            progress.thread.start()
            try:
                result = archive.download(self.root, self.relative, self.url, None, progress=progress)
            finally:
                progress.close()
        self.assertIn("validated 0/1 files", output.getvalue())
        self.assertIn("1 active files (1.0 MiB so far)", output.getvalue())
        self.assertIn("received 1.0 MiB this run", output.getvalue())
        self.assertEqual(result["bytes"], len(data))
        self.assertEqual(progress.received, len(data))
        self.assertEqual(progress.active, {})
        self.assertFalse(progress.thread.is_alive())

    def test_ignored_range_replaces_instead_of_appending(self):
        self.partial(self.data[:8])
        with patch.object(archive, "urlopen", return_value=Response(self.data)):
            self.assertEqual(self.download(), self.metadata)
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_unmarked_or_other_source_partial_is_restarted(self):
        for marker in (None, "https://other.example/file.parquet"):
            with self.subTest(marker=marker):
                partial = self.partial(b"untrusted-prefix", url=marker)
                if marker is None:
                    partial.with_name(partial.name + ".json").unlink()
                with patch.object(archive, "urlopen", return_value=Response(self.data)) as request:
                    self.download()
                self.assertIsNone(request.call_args.args[0].get_header("Range"))

    def test_complete_partial_recovers_from_http_416(self):
        self.partial(self.data)
        error = HTTPError(self.url, 416, "Range Not Satisfiable", {"Content-Range": f"bytes */{len(self.data)}"}, None)
        with patch.object(archive, "urlopen", side_effect=[error, Response(self.data)]) as request:
            self.assertEqual(self.download(), self.metadata)
        self.assertEqual(request.call_count, 2)
        self.assertIsNone(request.call_args_list[1].args[0].get_header("Range"))

    def test_interruption_keeps_progress_for_the_next_range_request(self):
        first = Response(self.data, fail_after=9)
        last = Response(self.data[9:], 206, {"Content-Range": f"bytes 9-{len(self.data)-1}/{len(self.data)}"})
        with patch.object(archive, "urlopen", side_effect=[first, last]) as request:
            self.assertEqual(self.download(), self.metadata)
        self.assertEqual(request.call_args_list[1].args[0].get_header("Range"), "bytes=9-")

    def test_bad_range_is_rejected_before_touching_partial(self):
        for content_range, content_length in (("bytes 6-24/25", "19"),
                                               ("bytes 7-25/25", "19"),
                                               ("bytes 7-24/25", "17"),
                                               ("bytes 7-24/*", "18")):
            with self.subTest(content_range=content_range, content_length=content_length):
                partial = self.partial(self.data[:7])
                bad = Response(self.data[7:], 206, {"Content-Range": content_range,
                                                  "Content-Length": content_length})
                with patch.object(archive, "ATTEMPTS", 1), patch.object(archive, "urlopen", return_value=bad), \
                     self.assertRaises(ValueError):
                    self.download()
                self.assertEqual(partial.read_bytes(), self.data[:7])
                self.assertFalse(self.target.exists())

    def test_partial_http_range_is_not_mistaken_for_a_complete_file(self):
        headers = {"Content-Range": f"bytes 0-8/{len(self.data)}"}
        first = Response(self.data[:9], 206, headers)
        last = Response(self.data[9:], 206, {"Content-Range": f"bytes 9-{len(self.data)-1}/{len(self.data)}"})
        with patch.object(archive, "urlopen", side_effect=[first, last]):
            self.assertEqual(self.download(), self.metadata)

    def test_changed_etag_on_a_resumed_response_forces_full_restart(self):
        self.partial(self.data[:7])
        changed = Response(self.data[7:], 206, {"Content-Range": f"bytes 7-{len(self.data)-1}/{len(self.data)}",
                                              "ETag": '"different-file"'})
        with patch.object(archive, "urlopen", side_effect=[changed, Response(self.data)]) as request:
            self.download()
        self.assertIsNone(request.call_args_list[1].args[0].get_header("Range"))

    def test_incomplete_http_body_is_retained_but_not_promoted(self):
        with patch.object(archive, "ATTEMPTS", 1), \
             patch.object(archive, "urlopen", return_value=Response(self.data[:8], headers={"Content-Length": str(len(self.data))})), \
             self.assertRaises(ValueError):
            self.download()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.target.with_name(self.target.name + ".part").read_bytes(), self.data[:8])

    def test_invalid_footer_or_previously_verified_checksum_is_not_promoted(self):
        for data, previous in ((self.data[:-4] + b"oops", None),
                               (b"PAR1body" + (1000).to_bytes(4, "little") + b"PAR1", None),
                               (parquet_bytes(b"changed-metadata"), self.metadata)):
            with self.subTest(data=data), patch.object(archive, "ATTEMPTS", 1), \
                 patch.object(archive, "urlopen", return_value=Response(data)), self.assertRaises(archive.IntegrityError):
                self.download(previous)
            self.assertFalse(self.target.exists())
            self.assertFalse(self.target.with_name(self.target.name + ".part").exists())

    def test_unsafe_destination_paths_are_rejected_before_http(self):
        with patch.object(archive, "urlopen") as request:
            for path in ("../escape.parquet", "/absolute.parquet", "disease//x.parquet",
                         "disease/../escape.parquet", "bad\x00.parquet", "C:/escape.parquet",
                         "C:escape.parquet", "disease/file.parquet:stream.parquet"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    archive.download(self.root, path, self.url, None)
        request.assert_not_called()

    def test_symlink_destination_is_rejected_before_http(self):
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable or require additional permissions")
        with patch.object(archive, "urlopen") as request, self.assertRaises(ValueError):
            archive.download(self.root, "linked/x.parquet", self.url, None)
        request.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_cancellation_leaves_partial_for_a_later_run(self):
        partial = self.partial(self.data[:7])
        stop = threading.Event()
        stop.set()
        with patch.object(archive, "urlopen") as request, self.assertRaises(archive.DownloadCancelled):
            self.download(stop=stop)
        request.assert_not_called()
        self.assertEqual(partial.read_bytes(), self.data[:7])


class InventoryAndManifestTests(unittest.TestCase):
    def test_discovery_stays_within_archive_and_rejects_encoded_traversal(self):
        base = archive.BASE
        pages = {
            base: b'<a href="../">parent</a><a href="disease/">data</a><a href="disease/">duplicate</a>'
                  b'<a href="https://other.example/">external</a><a href="?C=N">sort</a>'
                  b'<a href="%2e%2e/escape.parquet">escape</a><a href="%2Fabsolute.parquet">absolute</a>'
                  b'<a href="C%3A/escape.parquet">drive</a><a href="file.parquet%3Astream.parquet">stream</a>'
                  b'<a href="bad%00.parquet">null</a>',
            base + "disease/": b'<a href="../">parent</a><a href="part-000.parquet">part</a>'
                                b'<a href="part-000.parquet#fragment">fragment</a><a href="_SUCCESS">marker</a>',
        }
        def request(url, timeout):
            return Response(pages[url])
        with patch.object(archive, "urlopen", side_effect=request) as get:
            self.assertEqual(archive.discover(base), [("disease/part-000.parquet", base + "disease/part-000.parquet")])
        self.assertEqual(get.call_count, 2)

    def test_wrong_archive_or_invalid_manifest_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for value in ({"release":"24.09", "base":archive.BASE, "files":{}},
                          {"release":archive.RELEASE, "base":"https://other.example/", "files":{}},
                          {"release":archive.RELEASE, "base":archive.BASE, "files":[]}, []):
                path.write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaises(ValueError):
                    archive.load_manifest(path)

    def test_failed_and_stale_entries_are_removed_from_saved_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [(name, archive.BASE + name) for name in ("disease/good.parquet", "disease/bad.parquet")]
            entries = {name:{"bytes":20,"sha256":"a"*64,"url":url} for name,url in files}
            entries["stale.parquet"] = {"bytes":20,"sha256":"a"*64,"url":archive.BASE+"stale.parquet"}
            path = root / ".download-manifest.json"
            path.write_text(json.dumps({"release":archive.RELEASE, "base":archive.BASE, "files":entries}))
            def download(_root, name, *_):
                if name.endswith("bad.parquet"):
                    raise ValueError("checksum mismatch")
                return entries[name]
            with patch.object(sys, "argv", ["download", directory, "--workers", "2"]), \
                 patch.object(archive, "discover", return_value=files), patch.object(archive, "download", side_effect=download), \
                 patch("builtins.print"), self.assertRaises(SystemExit):
                archive.main()
            manifest = json.loads(path.read_text())
            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["expected_files"], 2)
            self.assertEqual(list(manifest["files"]), ["disease/good.parquet"])
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_list_only_does_not_create_a_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "missing"
            files = [("disease/file.parquet", archive.BASE + "disease/file.parquet")]
            with patch.object(sys, "argv", ["download", str(root), "--list-only"]), \
                 patch.object(archive, "discover", return_value=files), patch("builtins.print"):
                archive.main()
            self.assertFalse(root.exists())

    def test_interrupt_cancels_queued_downloads_and_keeps_manifest_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            files = [(f"disease/{number}.parquet", archive.BASE + f"disease/{number}.parquet") for number in range(50)]
            started = []
            def download(_root, _name, _url, _previous, stop, progress=None):
                started.append(stop)
                self.assertTrue(stop.wait(2), "Coordinator did not signal cancellation")
                raise archive.DownloadCancelled()
            with patch.object(sys, "argv", ["download", directory, "--workers", "1"]), \
                 patch.object(archive, "discover", return_value=files), patch.object(archive, "download", side_effect=download), \
                 patch.object(archive, "as_completed", side_effect=KeyboardInterrupt), patch("builtins.print"), \
                 self.assertRaises(SystemExit) as stopped:
                archive.main()
            self.assertEqual(stopped.exception.code, 130)
            self.assertLess(len(started), len(files))
            self.assertTrue(all(stop.is_set() for stop in started))
            manifest = json.loads((Path(directory) / ".download-manifest.json").read_text())
            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["expected_files"], 50)
            self.assertEqual(manifest["files"], {})

    def test_worker_finishing_during_interrupt_is_reused_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ("disease/ready.parquet", "disease/pending.parquet")
            files = [(name, archive.BASE + name) for name in names]
            data = parquet_bytes()
            checksum_started = threading.Event()
            context = {}
            original_download, original_checksum = archive.download, archive.checksum

            def download(root, name, url, previous, stop, progress=None):
                context["stop"] = stop
                return original_download(root, name, url, previous, stop)

            def checksum(path):
                if path.name == "pending.parquet.part":
                    checksum_started.set()
                    self.assertTrue(context["stop"].wait(5), "Coordinator did not signal cancellation")
                return original_checksum(path)

            def interrupt_during_checksum(futures):
                ready = next(future for future, name in futures.items() if name == names[0])
                ready.result(timeout=5)
                yield ready
                self.assertTrue(checksum_started.wait(5), "Worker did not reach checksum validation")
                raise KeyboardInterrupt

            with patch.object(sys, "argv", ["download", directory, "--workers", "2"]), \
                 patch.object(archive, "discover", return_value=files), \
                 patch.object(archive, "urlopen", side_effect=lambda *_args, **_kwargs: Response(data)), \
                 patch.object(archive, "download", side_effect=download), patch.object(archive, "checksum", side_effect=checksum), \
                 patch.object(archive, "as_completed", side_effect=interrupt_during_checksum), patch("builtins.print"), \
                 self.assertRaises(SystemExit) as stopped:
                archive.main()
            self.assertEqual(stopped.exception.code, 130)
            manifest_path = root / ".download-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            self.assertFalse(manifest["complete"])
            self.assertEqual(set(manifest["files"]), set(names))
            for name in names:
                self.assertEqual((root / name).read_bytes(), data)

            with patch.object(sys, "argv", ["download", directory, "--workers", "2"]), \
                 patch.object(archive, "discover", return_value=files), patch.object(archive, "urlopen") as request, \
                 patch("builtins.print") as output:
                archive.main()
            request.assert_not_called()
            self.assertTrue(json.loads(manifest_path.read_text())["complete"])
            self.assertTrue(any(call.args[0].startswith("Complete: 2 files,") for call in output.call_args_list))

    def test_failed_unprocessed_future_is_not_verified_during_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            name = "disease/failed.parquet"
            url = archive.BASE + name
            manifest_path = Path(directory) / ".download-manifest.json"
            manifest_path.write_text(json.dumps({"release":archive.RELEASE, "base":archive.BASE,
                "files":{name:{"bytes":20, "sha256":"a"*64, "url":url}}}))
            failed = threading.Event()

            def download(*_args):
                failed.set()
                raise ValueError("checksum mismatch")

            def interrupt_after_failure(_futures):
                self.assertTrue(failed.wait(5), "Worker did not reach its failure")
                raise KeyboardInterrupt

            with patch.object(sys, "argv", ["download", directory, "--workers", "1"]), \
                 patch.object(archive, "discover", return_value=[(name, url)]), \
                 patch.object(archive, "download", side_effect=download), \
                 patch.object(archive, "as_completed", side_effect=interrupt_after_failure), patch("builtins.print"), \
                 self.assertRaises(SystemExit):
                archive.main()
            manifest = json.loads(manifest_path.read_text())
            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["files"], {})

    def test_worker_count_is_bounded_before_discovery(self):
        for workers in ("0", "17", "-1"):
            with self.subTest(workers=workers), patch.object(sys, "argv", ["download", "unused", "--workers", workers]), \
                 patch.object(archive, "discover") as discover, patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                archive.main()
            discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
