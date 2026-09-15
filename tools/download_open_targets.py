#!/usr/bin/env python3
"""Download the immutable Open Targets archive, preserving its Parquet layout.

Transfers resume from .part files; a local manifest records sizes and SHA256
hashes for later revalidation. HTTP lengths and Parquet framing are checked,
but the archive does not supply a publisher checksum through this helper.
No API credentials or third-party Python packages are required.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

BASE = "https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/25.09/output/"
RELEASE = "25.09"
ATTEMPTS = 5


class DownloadCancelled(Exception):
    pass


class IntegrityError(ValueError):
    pass


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
            or "\x00" in value or path.as_posix() != value.rstrip("/")):
        raise ValueError("Archive path must be a canonical relative path")
    return path


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)


def discover(base: str = BASE) -> list[tuple[str, str]]:
    pending, seen, files = [base], set(), {}
    while pending:
        directory = pending.pop()
        if directory in seen:
            continue
        seen.add(directory)
        with urlopen(directory, timeout=60) as response:
            parser = Links()
            parser.feed(response.read().decode("utf-8"))
        for href in parser.links:
            url = urljoin(directory, href)
            if not url.startswith(base) or urlparse(url).query or urlparse(url).fragment:
                continue
            relative = unquote(url[len(base):])
            try:
                relative_path(relative)
            except ValueError:
                continue
            if url.endswith("/"):
                if url not in seen:
                    pending.append(url)
            elif url.endswith(".parquet"):
                files[relative] = url
    return sorted(files.items())


def checksum(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def validate_parquet(path: Path) -> None:
    size = path.stat().st_size
    if size < 12:
        raise IntegrityError(f"Truncated Parquet file: {path}")
    with path.open("rb") as stream:
        if stream.read(4) != b"PAR1":
            raise IntegrityError(f"Invalid Parquet header: {path}")
        stream.seek(-8, os.SEEK_END)
        footer_size = int.from_bytes(stream.read(4), "little")
        if not 0 < footer_size <= size - 12:
            raise IntegrityError(f"Invalid Parquet metadata length: {path}")
        if stream.read(4) != b"PAR1":
            raise IntegrityError(f"Invalid Parquet footer: {path}")


def metadata_matches(previous: dict | None, url: str) -> bool:
    return (isinstance(previous, dict) and previous.get("url") == url
            and type(previous.get("bytes")) is int and previous["bytes"] >= 12
            and isinstance(previous.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", previous["sha256"]) is not None)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("Refusing a symlink in download metadata")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def download_paths(root: Path, relative: str) -> tuple[Path, Path, Path]:
    path = relative_path(relative)
    if not relative.endswith(".parquet"):
        raise ValueError("Only Parquet files belong in the archive inventory")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = root
    for part in path.parts[:-1]:
        directory /= part
        if directory.is_symlink():
            raise ValueError("Refusing a symlink in the destination path")
        directory.mkdir(exist_ok=True)
    target = directory / path.name
    partial = target.with_name(target.name + ".part")
    resume = partial.with_name(partial.name + ".json")
    for candidate in (target, partial, resume):
        if candidate.is_symlink() or candidate.exists() and not candidate.is_file():
            raise ValueError("Download paths must be regular files")
    return target, partial, resume


def reset_partial(partial: Path, resume: Path) -> None:
    partial.unlink(missing_ok=True)
    resume.unlink(missing_ok=True)


def resume_metadata(partial: Path, resume: Path, url: str) -> dict:
    try:
        metadata = json.loads(resume.read_text())
        validator = metadata.get("validator", "")
        if (metadata.get("url") == url and isinstance(validator, str)
                and len(validator) <= 1024 and not any(c in validator for c in "\r\n")
                and partial.is_file()):
            return metadata
    except (OSError, ValueError, AttributeError):
        pass
    # An old/unmarked partial cannot be tied to this archive object.
    reset_partial(partial, resume)
    return {}


def download(root: Path, relative: str, url: str, previous: dict | None,
             stop: threading.Event | None = None) -> dict:
    target, partial, resume = download_paths(root, relative)
    previous = previous if metadata_matches(previous, url) else None
    if target.exists() and previous and target.stat().st_size == previous["bytes"]:
        if checksum(target) == previous["sha256"]:
            validate_parquet(target)
            return previous
    metadata = resume_metadata(partial, resume, url)
    for attempt in range(ATTEMPTS):
        if stop is not None and stop.is_set():
            raise DownloadCancelled()
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "VirtualBiotech-archive-setup/1.0", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
                if metadata.get("validator"):
                    headers["If-Range"] = metadata["validator"]
            with urlopen(Request(url, headers=headers), timeout=120) as response:
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise IntegrityError("Archive response must not be content-encoded")
                validator = response.headers.get("ETag", "")
                if not validator or validator.startswith("W/"):
                    validator = response.headers.get("Last-Modified", "")
                length = response.headers.get("Content-Length")
                length = int(length) if length is not None else None
                if length is not None and length < 0:
                    raise ValueError("Invalid HTTP content length")
                if response.status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match:
                        raise ValueError("Server returned an invalid byte range")
                    start, end, total = map(int, match.groups())
                    if start != offset or not start <= end < total or length not in (None, end - start + 1):
                        raise ValueError("Server returned an unexpected byte range")
                    length = end - start + 1
                    if validator and metadata.get("validator") not in (None, "", validator):
                        raise IntegrityError("Archive object changed during a resumed transfer")
                elif response.status == 200:
                    offset = 0
                    total = length
                else:
                    raise ValueError(f"Unexpected HTTP status: {response.status}")
                if response.status == 206 and not validator:
                    validator = metadata.get("validator", "")
                metadata = {"url": url, "validator": validator}
                transferred = 0
                with partial.open("ab" if offset else "wb") as output:
                    write_json(resume, metadata)
                    while chunk := response.read(1024 * 1024):
                        if stop is not None and stop.is_set():
                            raise DownloadCancelled()
                        if length is not None and transferred + len(chunk) > length:
                            raise IntegrityError("HTTP response exceeded its declared length")
                        output.write(chunk)
                        transferred += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if length is not None and transferred != length:
                    raise ValueError("Incomplete HTTP transfer")
                if total is not None and offset + transferred != total:
                    raise ValueError("Server returned only part of the requested file")
            validate_parquet(partial)
            result = {"bytes": partial.stat().st_size, "sha256": checksum(partial), "url": url}
            if previous and (result["bytes"], result["sha256"]) != (previous["bytes"], previous["sha256"]):
                raise IntegrityError("Downloaded file does not match its previously verified checksum")
            partial.replace(target)
            resume.unlink(missing_ok=True)
            return result
        except DownloadCancelled:
            raise
        except HTTPError as exc:
            if exc.code == 416:
                # A crash after the last byte can leave a complete .part file.
                # Restart it instead of repeating an unsatisfiable Range forever.
                reset_partial(partial, resume)
                metadata = {}
            if attempt == ATTEMPTS - 1:
                raise
        except IntegrityError:
            reset_partial(partial, resume)
            metadata = {}
            if attempt == ATTEMPTS - 1:
                raise
        except Exception:
            if attempt == ATTEMPTS - 1:
                raise
        # Preserve interrupted, incomplete bodies so the next request resumes.
        delay = min(2 ** attempt, 8)
        if stop is not None:
            if stop.wait(delay):
                raise DownloadCancelled()
        else:
            time.sleep(delay)
    raise RuntimeError("Unreachable")


def load_manifest(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("Refusing a symlink for the download manifest")
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text())
    if (not isinstance(manifest, dict) or manifest.get("release") != RELEASE
            or manifest.get("base") != BASE or not isinstance(manifest.get("files"), dict)):
        raise ValueError("Destination manifest belongs to a different archive or is invalid")
    return manifest["files"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("workers must be between 1 and 16")
    files = discover()
    print(f"Archive inventory: {len(files)} Parquet files", flush=True)
    if not files:
        raise RuntimeError("The archive returned no Parquet files")
    if args.list_only:
        print("\n".join(path for path, _ in files))
        return
    root = args.destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / ".download-manifest.json"
    previous = load_manifest(manifest_path)
    entries = {name: previous[name] for name, url in files if metadata_matches(previous.get(name), url)}
    errors = []
    completed = 0
    total_bytes = 0
    last_report = time.monotonic()

    def save(complete=False):
        write_json(manifest_path, {"release": RELEASE, "base": BASE,
                                  "expected_files": len(files), "complete": complete, "files": entries})

    stop = threading.Event()
    interrupted = False
    save()
    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = {}
    try:
        for name, url in files:
            future = pool.submit(download, root, name, url, entries.get(name), stop)
            futures[future] = name
        for future in as_completed(futures):
            name = futures[future]
            try:
                entries[name] = future.result()
                completed += 1
                total_bytes += entries[name]["bytes"]
            except Exception as exc:
                entries.pop(name, None)
                errors.append(name)
                print(f"FAILED {name}: {exc}", flush=True)
            if completed % 50 == 0 or time.monotonic() - last_report > 20:
                save()
                print(f"Validated {completed}/{len(files)} files ({total_bytes / 1024**3:.2f} GiB)", flush=True)
                last_report = time.monotonic()
    except KeyboardInterrupt:
        interrupted = True
        stop.set()
        for future in futures:
            future.cancel()
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        save(complete=completed == len(files) and not errors and not interrupted)
    if interrupted:
        print("Interrupted; completed and partial files are retained. Rerun to resume.", flush=True)
        raise SystemExit(130)
    if errors:
        raise SystemExit(f"{len(errors)} files failed; rerun to resume")
    print(f"Complete: {completed} files, {total_bytes / 1024**3:.2f} GiB; manifest: {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
