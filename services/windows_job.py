"""Run an optional service inside a fail-closed Windows resource-limited job.

No dependencies outside the standard library. Copy into each independent artifact.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import subprocess
import sys


class Basic(c.Structure):
    _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64),
                ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                ('processes', w.DWORD), ('affinity', c.c_size_t),
                ('priority', w.DWORD), ('scheduling', w.DWORD)]


class Extended(c.Structure):
    _fields_ = [('basic', Basic), ('io', c.c_uint64 * 6),
                ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]


class Cpu(c.Structure):
    _fields_ = [('flags', w.DWORD), ('rate', w.DWORD)]


def enter_job(memory_mb, cpu_percent, processes):
    if sys.platform != 'win32':
        raise RuntimeError('Windows job limits require Windows')
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise c.WinError(c.get_last_error())
    limits = Extended()
    limits.basic.flags = 0x2000 | 0x200 | 0x8  # kill-on-close, job memory, process count
    limits.basic.processes = processes
    limits.job_memory = memory_mb * 1024 * 1024
    cpu = Cpu(0x1 | 0x4, cpu_percent * 100)  # enabled hard cap
    for kind, value in ((9, limits), (15, cpu)):
        if not kernel.SetInformationJobObject(job, kind, c.byref(value), c.sizeof(value)):
            raise c.WinError(c.get_last_error())
        actual = type(value)()
        if not kernel.QueryInformationJobObject(job, kind, c.byref(actual), c.sizeof(actual), None):
            raise c.WinError(c.get_last_error())
        if kind == 9 and (actual.basic.flags != limits.basic.flags or actual.job_memory != limits.job_memory or actual.basic.processes != processes):
            raise RuntimeError('Job memory/process limits were not applied')
        if kind == 15 and (actual.flags != cpu.flags or actual.rate != cpu.rate):
            raise RuntimeError('Job CPU limit was not applied')
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise c.WinError(c.get_last_error())
    # Keep the non-inheritable handle alive until this supervisor exits. Children
    # inherit job membership, not the handle; stopping the supervisor kills them.
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-mb', type=int, default=512)
    parser.add_argument('--cpu-percent', type=int, default=15)
    parser.add_argument('--processes', type=int, default=6)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 128 <= args.memory_mb <= 4096 or not 1 <= args.cpu_percent <= 50 or not 2 <= args.processes <= 16:
        parser.error('Resource limits outside allowed range')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('A child command is required')
    job = enter_job(args.memory_mb, args.cpu_percent, args.processes)
    print(f'Windows job enforced: {args.memory_mb} MiB, {args.cpu_percent}% CPU, {args.processes} processes', flush=True)
    return subprocess.call(command)


if __name__ == '__main__':
    sys.exit(main())
