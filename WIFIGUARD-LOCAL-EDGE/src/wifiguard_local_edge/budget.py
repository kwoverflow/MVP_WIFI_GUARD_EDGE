"""Enforced CPU affinity and Windows committed-memory budget, not an ARM emulator."""

import ctypes
from ctypes import wintypes
import os
import struct

import psutil

_job_handle = None


def windows_cpu_topology():
    if os.name != "nt":
        raise RuntimeError("Windows CPU-set discovery requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = kernel.GetSystemCpuSetInformation
    fn.argtypes = [ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), wintypes.HANDLE, wintypes.ULONG]
    fn.restype = wintypes.BOOL
    size = wintypes.ULONG()
    fn(None, 0, ctypes.byref(size), None, 0)
    buffer = ctypes.create_string_buffer(size.value)
    if not fn(buffer, size.value, ctypes.byref(size), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    rows, offset = [], 0
    while offset < size.value:
        row_size, kind = struct.unpack_from("II", buffer, offset)
        if row_size < 8:
            raise RuntimeError("invalid CPU-set structure size")
        if kind == 0:
            _, group, logical, core, _, _, efficiency, _ = struct.unpack_from("IH6B", buffer, offset + 8)
            rows.append({"group": group, "logical": logical, "core": core, "efficiency_class": efficiency})
        offset += row_size
    return rows


def efficiency_cpu_ids(count=4):
    rows = windows_cpu_topology()
    allowed = set(psutil.Process().cpu_affinity())
    rows = [r for r in rows if r["group"] == 0 and r["logical"] in allowed]
    minimum = min(r["efficiency_class"] for r in rows)
    seen, selected = set(), []
    for row in rows:
        if row["efficiency_class"] == minimum and row["core"] not in seen:
            selected.append(row["logical"])
            seen.add(row["core"])
    if len(selected) < count:
        raise RuntimeError("not enough distinct efficiency cores")
    return selected[:count], rows


def apply_budget(cpu_affinity=None, memory_limit_mib=None):
    global _job_handle
    process = psutil.Process()
    if cpu_affinity:
        process.cpu_affinity(list(cpu_affinity))
        if set(process.cpu_affinity()) != set(cpu_affinity):
            raise RuntimeError("CPU affinity did not apply")
    if memory_limit_mib:
        if memory_limit_mib <= 0:
            raise ValueError("positive memory limit required")
        if os.name != "nt":
            raise RuntimeError("use systemd MemoryMax on Linux; Windows Job Object cap is Windows-only")
        class Basic(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                        ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("Priority", wintypes.DWORD),
                        ("Scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("IO", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t),
                        ("PeakJobMemory", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        _job_handle = kernel.CreateJobObjectW(None, None)
        if not _job_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.Basic.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.ProcessMemoryLimit = memory_limit_mib * 2**20
        if not kernel.SetInformationJobObject(_job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(_job_handle, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
        verified = Extended()
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        if not kernel.QueryInformationJobObject(_job_handle, 9, ctypes.byref(verified), ctypes.sizeof(verified), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if verified.ProcessMemoryLimit != limits.ProcessMemoryLimit or not verified.Basic.LimitFlags & 0x100:
            raise RuntimeError("memory cap verification failed")
        # Keep the handle alive until process exit. No kill-on-close flag.
    return {"cpu_affinity": process.cpu_affinity(), "process_commit_limit_mib": memory_limit_mib,
            "memory_limit_verified": bool(memory_limit_mib),
            "scope": "resource-budget proxy, NOT Raspberry Pi ISA/frequency emulation"}
