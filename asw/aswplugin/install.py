"""Checks and downloads needed before asw can run on this system."""
import contextlib
import os
import subprocess
import urllib.request

NEED_FREE_BYTES = 2_100_000_000
WARN_RAM_BYTES = 2_000_000_000
ASSETS = {"aarch64": "asw-linux-arm64", "arm64": "asw-linux-arm64",
          "x86_64": "asw-linux-amd64", "amd64": "asw-linux-amd64"}


def binary_asset(machine):
    return ASSETS.get(machine)


def release_url(asw_version, asset):
    return "https://github.com/auto-sea-way/asw/releases/download/v%s/%s" % (asw_version, asset)


def preflight(data_dir, machine, mem_total, free):
    errors, warnings = [], []
    if not binary_asset(machine):
        errors.append("This system is not supported (%s). asw needs 64-bit Linux on arm64 or x86_64." % machine)
    if free < NEED_FREE_BYTES:
        errors.append("Not enough free space: about %.1f GB needed in %s." % (NEED_FREE_BYTES / 1e9, data_dir))
    if mem_total < WARN_RAM_BYTES:
        warnings.append("Less than 2 GB of RAM: routing will be slow.")
    return errors, warnings


def mem_total():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def cleanup_stale(graph_path):
    with contextlib.suppress(OSError):
        os.unlink(graph_path + ".tmp")


def download_binary(url, dest):
    part = dest + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        os.chmod(part, 0o755)
        subprocess.run([part, "--version"], check=True, capture_output=True, timeout=10)
        os.replace(part, dest)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise RuntimeError("this system cannot run asw") from e
