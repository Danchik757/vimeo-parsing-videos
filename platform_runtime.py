"""Platform-aware runtime helpers for Linux and Windows."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import signal
import subprocess
import time
from pathlib import Path


WINDOWS_BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def current_platform():
    return platform.system().lower()


def is_linux():
    return current_platform() == "linux"


def is_windows():
    return current_platform() == "windows"


def supports_xvfb():
    return is_linux()


def supports_interface_bound_downloads():
    return is_linux()


def find_browser_command():
    for command in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        resolved = shutil.which(command)
        if resolved:
            return resolved

    if is_windows():
        for candidate in WINDOWS_BROWSER_PATHS:
            if Path(candidate).exists():
                return candidate

    return None


def read_sockstat_file(path):
    path = Path(path)
    if not path.exists():
        return {}

    stats = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or ":" not in line:
                    continue
                family, tail = line.split(":", 1)
                parts = tail.strip().split()
                metrics = {}
                for index in range(0, len(parts) - 1, 2):
                    key = parts[index]
                    value = parts[index + 1]
                    try:
                        metrics[key] = int(value)
                    except ValueError:
                        continue
                stats[family.lower()] = metrics
    except Exception:
        return {}

    return stats


def load_socket_usage(logger=None):
    if not is_linux():
        return None

    sockstat = read_sockstat_file("/proc/net/sockstat")
    sockstat6 = read_sockstat_file("/proc/net/sockstat6")

    try:
        tcp_inuse = int(sockstat.get("tcp", {}).get("inuse", 0)) + int(
            sockstat6.get("tcp6", {}).get("inuse", 0)
        )
        tcp_timewait = int(sockstat.get("tcp", {}).get("tw", 0))
        tcp_orphan = int(sockstat.get("tcp", {}).get("orphan", 0))
        tcp_alloc = int(sockstat.get("tcp", {}).get("alloc", 0))
        udp_inuse = int(sockstat.get("udp", {}).get("inuse", 0)) + int(
            sockstat6.get("udp6", {}).get("inuse", 0)
        )
        raw_inuse = int(sockstat.get("raw", {}).get("inuse", 0)) + int(
            sockstat6.get("raw6", {}).get("inuse", 0)
        )
        sockets_used = int(sockstat.get("sockets", {}).get("used", 0))
    except Exception as exc:
        if logger is not None:
            logger.warning("Failed to parse socket counters: %s", exc)
        return None

    return {
        "sockets_used": sockets_used,
        "tcp_inuse": tcp_inuse,
        "tcp_timewait": tcp_timewait,
        "tcp_orphan": tcp_orphan,
        "tcp_alloc": tcp_alloc,
        "udp_inuse": udp_inuse,
        "raw_inuse": raw_inuse,
    }


def available_memory_gb():
    if is_linux():
        meminfo = Path("/proc/meminfo")
        if not meminfo.exists():
            return None
        values = {}
        with open(meminfo, "r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, payload = line.split(":", 1)
                values[key.strip()] = payload.strip()
        available = values.get("MemAvailable")
        if not available:
            return None
        parts = available.split()
        if not parts:
            return None
        try:
            kb = int(parts[0])
        except ValueError:
            return None
        return kb / 1024 / 1024

    if is_windows():
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        try:
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
        except Exception:
            return None
        return status.ullAvailPhys / 1024 / 1024 / 1024

    return None


def worker_popen_kwargs():
    if is_windows():
        return {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        }
    return {"start_new_session": True}


def _taskkill_process_tree(pid, force, logger=None, label="process"):
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
    except Exception as exc:
        if logger is not None:
            logger.debug("taskkill failed for %s (%s): %s", label, pid, exc)
        return False


def _windows_process_children_map():
    if not is_windows():
        return {}

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_char * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return {}

    children = {}
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    try:
        has_entry = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while has_entry:
            parent_pid = int(entry.th32ParentProcessID)
            child_pid = int(entry.th32ProcessID)
            children.setdefault(parent_pid, []).append(child_pid)
            has_entry = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    return children


def terminate_child_process_trees_for_restart(logger=None, label="current-process"):
    if not is_windows():
        return

    current_pid = os.getpid()
    child_pids = _windows_process_children_map().get(current_pid, [])
    if not child_pids:
        return

    if logger is not None:
        logger.warning(
            "Terminating %d child process tree(s) before controlled restart for %s",
            len(child_pids),
            label,
        )

    seen = set()
    for child_pid in child_pids:
        if child_pid in seen or child_pid <= 0:
            continue
        seen.add(child_pid)
        _taskkill_process_tree(
            int(child_pid),
            force=True,
            logger=logger,
            label=f"{label}-child-{child_pid}",
        )


def terminate_process_tree(process, logger, label, timeout_seconds=10):
    if is_windows():
        pid = getattr(process, "pid", None)
        if pid is None:
            try:
                process.terminate()
            except Exception:
                return
        else:
            _taskkill_process_tree(pid, force=False, logger=logger, label=label)

        deadline = time.time() + max(1, int(timeout_seconds))
        while time.time() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.5)

        logger.warning("Force-killing %s after terminate timeout", label)
        if pid is not None and _taskkill_process_tree(pid, force=True, logger=logger, label=label):
            return
        try:
            process.kill()
        except Exception:
            pass
        return

    pgid = None
    if getattr(process, "pid", None):
        pgid = int(process.pid)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pgid = None
        except Exception:
            pgid = None

    if pgid is None:
        try:
            process.terminate()
        except Exception:
            return

    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.5)

    logger.warning("Force-killing %s after terminate timeout", label)
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except Exception:
            pass
    try:
        process.kill()
    except Exception:
        pass


def cleanup_process_tree_after_exit(process, logger, label, timeout_seconds=5):
    if not getattr(process, "pid", None):
        return

    if is_windows():
        _taskkill_process_tree(int(process.pid), force=True, logger=logger, label=label)
        return

    try:
        os.killpg(int(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception as exc:
        logger.debug("Could not clean process group for %s: %s", label, exc)
        return

    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        try:
            os.killpg(int(process.pid), 0)
        except ProcessLookupError:
            return
        except Exception:
            return
        time.sleep(0.2)

    logger.warning("Force-killing leftover process group for %s", label)
    try:
        os.killpg(int(process.pid), signal.SIGKILL)
    except Exception:
        pass
