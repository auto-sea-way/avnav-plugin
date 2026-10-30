"""Checks and downloads needed before asw can run on this system."""
import contextlib
import http.client
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


def preflight(data_dir, system, machine, mem_total, free, graph_present=False):
    """system is sys.platform, machine is platform.machine()."""
    errors, warnings = [], []
    if not system.startswith("linux") or not binary_asset(machine):
        errors.append("This system is not supported (%s %s). asw needs 64-bit Linux on arm64 or x86_64."
                      % (system, machine))
    if not graph_present and free < NEED_FREE_BYTES:  # the binary alone is a few MB
        errors.append("Not enough free space: about %.1f GB needed in %s." % (NEED_FREE_BYTES / 1e9, data_dir))
    if 0 < mem_total < WARN_RAM_BYTES:  # 0 = unknown
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
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
        except (OSError, ValueError, http.client.HTTPException) as e:
            raise RuntimeError("Download failed. Check the internet connection and try again.") from e
        try:
            os.chmod(part, 0o755)
            subprocess.run([part, "--version"], check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError("this system cannot run asw") from e
        os.replace(part, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise
