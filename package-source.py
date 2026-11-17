#!/usr/bin/env python3
"""Package authored sources and reports; never read user profiles or copy app binaries."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent


def select_files():
    selected = set()
    groups = (
        (ROOT, ("*.swift", "*.mjs")),
        (ROOT / "experiments", ("*.py", "*.txt", "*.mmd", "*.json")),
        (ROOT / "experiments/minimal-cdm-trial", ("*.cpp", "*.h", "*.py", "*.txt", "*.json")),
        (ROOT / "experiments/nhk-auth-trial", ("*.py", "*.txt", "*.json")),
    )
    for directory, patterns in groups:
        for pattern in patterns:
            selected.update(directory.glob(pattern))
    for name in (
        "README.txt", "SOURCE_INDEX.txt", "build.sh", "package-source.py",
        "experiments/decoded-video-probe.js", "experiments/webcodecs-probe.js",
        "experiments/minimal-cdm-trial/build.sh",
        "experiments/minimal-cdm-trial/include/content_decryption_module.h",
        "experiments/minimal-cdm-trial/include/content_decryption_module_export.h",
        "experiments/minimal-cdm-trial/include/LICENSE",
        "experiments/nhk-auth-trial/nhk-init-data.bin",
    ):
        selected.add(ROOT / name)
    return sorted(selected)


def main():
    payloads = []
    for source in select_files():
        if source.is_symlink() or not source.is_file():
            raise SystemExit("Missing or linked source: " + str(source.relative_to(ROOT)))
        relative = source.relative_to(ROOT).as_posix()
        data = source.read_bytes()
        # Reject likely real JWTs or private keys without printing their contents.
        # Synthetic credentials in tests use a literal 'synthetic' header.
        if re.search(rb"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", data):
            raise SystemExit("Possible credential; review file: " + relative)
        if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", data):
            raise SystemExit("Possible private key; review file: " + relative)
        payloads.append((relative, data, source.stat().st_mode))
    if not payloads:
        raise SystemExit("No source files selected")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Source snapshot; NHK programme MP4 export remains unimplemented",
        "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data, _ in payloads],
    }
    destination = ROOT / "exports"
    destination.mkdir(exist_ok=True)
    name = "video-saver-source-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".zip"
    output = destination / name
    try:
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, data, mode in payloads:
                entry = zipfile.ZipInfo("video-saver/" + relative)
                entry.create_system = 3
                entry.external_attr = (0o100755 if mode & 0o111 else 0o100644) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data)
            archive.writestr("video-saver/SOURCE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        with zipfile.ZipFile(output) as archive:
            if archive.testzip() is not None:
                raise ValueError("Archive CRC verification failed")
            for relative, data, _ in payloads:
                if archive.read("video-saver/" + relative) != data:
                    raise ValueError("Archive content verification failed")
    except BaseException:
        if output.exists():
            output.unlink()
        raise
    print(json.dumps({"archive": str(output), "files": len(payloads) + 1,
                      "bytes": output.stat().st_size,
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
