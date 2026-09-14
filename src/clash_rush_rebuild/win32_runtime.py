"""Protected mutex and suspended Job/process Win32 runtime primitives."""

from __future__ import annotations

import ctypes
import ntpath
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from .lifecycle import CreatedProcess

DEFAULT_MUTEX_NAME = "Global\\ClashRushRebuildLifecycle-v1"
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
MUTEX_ALL_ACCESS = 0x001F0001
ERROR_ALREADY_EXISTS = 183
SDDL_REVISION_1 = 1
DACL_SECURITY_INFORMATION = 0x00000004
SE_KERNEL_OBJECT = 6
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class Win32RuntimeError(RuntimeError):
    """A Win32 lifecycle operation failed closed."""


class MutexTimeoutError(Win32RuntimeError):
    """The lifecycle mutex was not acquired within its exact timeout."""


class MutexAbandonedError(Win32RuntimeError):
    """Reserved for callers that reject an abandoned mutex lease."""


class RuntimeApi(Protocol):
    def current_operator_sid(self) -> str: ...
    def create_mutex_with_sddl(
        self, name: str, sddl: str
    ) -> tuple[object | None, bool]: ...
    def mutex_dacl_matches_sddl(self, handle: object, sddl: str) -> bool: ...
    def wait_for_single_object(self, handle: object, milliseconds: int) -> int: ...
    def get_last_error(self) -> int: ...
    def release_mutex(self, handle: object) -> bool: ...
    def close_handle(self, handle: object) -> bool: ...
    def create_job_object(self) -> object | None: ...
    def set_job_limit_flags(self, job: object, flags: int) -> bool: ...
    def create_process_w(
        self,
        executable: str,
        command_line: ctypes.Array[ctypes.c_wchar],
        inherit_handles: bool,
        creation_flags: int,
    ) -> CreatedProcess: ...
    def assign_process_to_job(self, job: object, process: object) -> bool: ...
    def is_process_in_job(self, process: object, job: object) -> bool: ...
    def resume_thread(self, thread: object) -> int: ...
    def terminate_job_object(self, job: object, exit_code: int) -> bool: ...
    def job_active_process_count(self, job: object) -> int: ...


@dataclass(slots=True)
class ProtectedMutexLease:
    api: RuntimeApi
    handle: object
    name: str
    abandoned: bool
    _released: bool = False
    _closed: bool = False

    def require_usable(self) -> None:
        if self._closed:
            raise Win32RuntimeError("mutex lease already closed")
        if self.abandoned:
            raise MutexAbandonedError(
                "abandoned mutex requires operator reconciliation"
            )

    def release(self) -> None:
        if self._closed:
            raise Win32RuntimeError("mutex lease already closed")
        if not self._released:
            if self.api.release_mutex(self.handle) is not True:
                raise Win32RuntimeError("ReleaseMutex failed")
            self._released = True
        if self.api.close_handle(self.handle) is not True:
            raise Win32RuntimeError("mutex handle close failed")
        self._closed = True


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class NativeWin32Api:
    """ctypes adapter for the exact native operations used by :class:`Win32Runtime`."""

    def __init__(self) -> None:
        if not hasattr(ctypes, "WinDLL"):
            raise Win32RuntimeError("native Win32 APIs are unavailable")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        k32 = self._kernel32
        adv = self._advapi32
        handle = wintypes.HANDLE
        void_pointer_pointer = ctypes.POINTER(ctypes.c_void_p)

        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = handle
        k32.CreateMutexW.argtypes = [
            ctypes.POINTER(SECURITY_ATTRIBUTES),
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        k32.CreateMutexW.restype = handle
        k32.WaitForSingleObject.argtypes = [handle, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.ReleaseMutex.argtypes = [handle]
        k32.ReleaseMutex.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [handle]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = handle
        k32.SetInformationJobObject.argtypes = [
            handle,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.QueryInformationJobObject.argtypes = [
            handle,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        k32.CreateProcessW.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [handle, handle]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.IsProcessInJob.argtypes = [
            handle,
            handle,
            ctypes.POINTER(wintypes.BOOL),
        ]
        k32.IsProcessInJob.restype = wintypes.BOOL
        k32.ResumeThread.argtypes = [handle]
        k32.ResumeThread.restype = wintypes.DWORD
        k32.TerminateJobObject.argtypes = [handle, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL

        adv.OpenProcessToken.argtypes = [
            handle,
            wintypes.DWORD,
            ctypes.POINTER(handle),
        ]
        adv.OpenProcessToken.restype = wintypes.BOOL
        adv.GetTokenInformation.argtypes = [
            handle,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        adv.GetTokenInformation.restype = wintypes.BOOL
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, void_pointer_pointer]
        adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            void_pointer_pointer,
            ctypes.POINTER(wintypes.DWORD),
        ]
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        adv.GetSecurityInfo.argtypes = [
            handle,
            ctypes.c_int,
            wintypes.DWORD,
            void_pointer_pointer,
            void_pointer_pointer,
            void_pointer_pointer,
            void_pointer_pointer,
            void_pointer_pointer,
        ]
        adv.GetSecurityInfo.restype = wintypes.DWORD
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            void_pointer_pointer,
            ctypes.POINTER(wintypes.DWORD),
        ]
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL

    def get_last_error(self) -> int:
        return int(ctypes.get_last_error())

    def current_operator_sid(self) -> str:
        token = wintypes.HANDLE()
        if not self._advapi32.OpenProcessToken(
            self._kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
        ):
            raise Win32RuntimeError("OpenProcessToken failed")
        try:
            required = wintypes.DWORD()
            self._advapi32.GetTokenInformation(
                token, TOKEN_USER, None, 0, ctypes.byref(required)
            )
            if required.value == 0:
                raise Win32RuntimeError("GetTokenInformation size query failed")
            buffer = ctypes.create_string_buffer(required.value)
            if not self._advapi32.GetTokenInformation(
                token,
                TOKEN_USER,
                buffer,
                required,
                ctypes.byref(required),
            ):
                raise Win32RuntimeError("GetTokenInformation failed")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
            text = ctypes.c_void_p()
            if not self._advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise Win32RuntimeError("ConvertSidToStringSidW failed")
            try:
                return ctypes.wstring_at(text)
            finally:
                self._kernel32.LocalFree(text)
        finally:
            self._kernel32.CloseHandle(token)

    def create_mutex_with_sddl(
        self, name: str, sddl: str
    ) -> tuple[object | None, bool]:
        descriptor = ctypes.c_void_p()
        if not self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise Win32RuntimeError("security descriptor conversion failed")
        try:
            attributes = SECURITY_ATTRIBUTES(
                ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False
            )
            ctypes.set_last_error(0)
            handle = self._kernel32.CreateMutexW(ctypes.byref(attributes), False, name)
            error = ctypes.get_last_error()
            return handle, error == ERROR_ALREADY_EXISTS
        finally:
            self._kernel32.LocalFree(descriptor)

    def mutex_dacl_matches_sddl(self, handle: object, sddl: str) -> bool:
        descriptor = ctypes.c_void_p()
        result = self._advapi32.GetSecurityInfo(
            handle,
            SE_KERNEL_OBJECT,
            DACL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if result != 0:
            raise Win32RuntimeError(f"GetSecurityInfo failed with error {result}")
        try:
            text = ctypes.c_void_p()
            if not self._advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor,
                SDDL_REVISION_1,
                DACL_SECURITY_INFORMATION,
                ctypes.byref(text),
                None,
            ):
                raise Win32RuntimeError("security descriptor formatting failed")
            try:
                expanded_mutex_all_access = sddl.replace(
                    ";;GA;;;", f";;0x{MUTEX_ALL_ACCESS:x};;;"
                )
                return ctypes.wstring_at(text) == expanded_mutex_all_access
            finally:
                self._kernel32.LocalFree(text)
        finally:
            self._kernel32.LocalFree(descriptor)

    def wait_for_single_object(self, handle: object, milliseconds: int) -> int:
        return int(self._kernel32.WaitForSingleObject(handle, milliseconds))

    def release_mutex(self, handle: object) -> bool:
        return bool(self._kernel32.ReleaseMutex(handle))

    def close_handle(self, handle: object) -> bool:
        return bool(self._kernel32.CloseHandle(handle))

    def create_job_object(self) -> object | None:
        return self._kernel32.CreateJobObjectW(None, None)

    def set_job_limit_flags(self, job: object, flags: int) -> bool:
        information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        information.BasicLimitInformation.LimitFlags = flags
        return bool(
            self._kernel32.SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(information),
                ctypes.sizeof(information),
            )
        )

    def create_process_w(
        self,
        executable: str,
        command_line: ctypes.Array[ctypes.c_wchar],
        inherit_handles: bool,
        creation_flags: int,
    ) -> CreatedProcess:
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(STARTUPINFOW)
        information = PROCESS_INFORMATION()
        if not self._kernel32.CreateProcessW(
            executable,
            command_line,
            None,
            None,
            inherit_handles,
            creation_flags,
            None,
            None,
            ctypes.byref(startup),
            ctypes.byref(information),
        ):
            raise Win32RuntimeError(
                f"CreateProcessW failed with error {self.get_last_error()}"
            )
        return CreatedProcess(
            information.hProcess, information.hThread, int(information.dwProcessId)
        )

    def assign_process_to_job(self, job: object, process: object) -> bool:
        return bool(self._kernel32.AssignProcessToJobObject(job, process))

    def is_process_in_job(self, process: object, job: object) -> bool:
        result = wintypes.BOOL()
        if not self._kernel32.IsProcessInJob(process, job, ctypes.byref(result)):
            raise Win32RuntimeError(
                f"IsProcessInJob failed with error {self.get_last_error()}"
            )
        return bool(result.value)

    def resume_thread(self, thread: object) -> int:
        return int(self._kernel32.ResumeThread(thread))

    def terminate_job_object(self, job: object, exit_code: int) -> bool:
        return bool(self._kernel32.TerminateJobObject(job, exit_code))

    def job_active_process_count(self, job: object) -> int:
        information = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not self._kernel32.QueryInformationJobObject(
            job,
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            raise Win32RuntimeError(
                f"QueryInformationJobObject failed with error {self.get_last_error()}"
            )
        return int(information.ActiveProcesses)


class Win32Runtime:
    def __init__(self, api: RuntimeApi) -> None:
        self._api = api

    def create_job(self) -> object:
        job = self._api.create_job_object()
        if job is None or job == 0:
            raise Win32RuntimeError("CreateJobObjectW failed")
        return job

    def set_kill_on_close(self, job: object) -> None:
        if job is None:
            raise Win32RuntimeError("valid Job handle required")
        if (
            self._api.set_job_limit_flags(job, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            is not True
        ):
            raise Win32RuntimeError("SetInformationJobObject failed")

    def create_suspended(self, executable: str, command_line: str) -> CreatedProcess:
        if (
            type(executable) is not str
            or not ntpath.isabs(executable)
            or type(command_line) is not str
            or not command_line
            or "\x00" in executable
            or "\x00" in command_line
        ):
            raise Win32RuntimeError(
                "absolute executable and exact command line required"
            )
        mutable_command_line = ctypes.create_unicode_buffer(command_line)
        created = self._api.create_process_w(
            executable, mutable_command_line, False, CREATE_SUSPENDED
        )
        if (
            type(created) is not CreatedProcess
            or created.process_handle == 0
            or created.thread_handle == 0
        ):
            close_failed = False
            if isinstance(created, CreatedProcess):
                for handle in (created.thread_handle, created.process_handle):
                    if handle is not None and handle != 0:
                        close_failed = (
                            self._api.close_handle(handle) is not True or close_failed
                        )
            if close_failed:
                raise Win32RuntimeError("invalid process record handle cleanup failed")
            raise Win32RuntimeError("CreateProcessW returned an invalid process record")
        return created

    def assign_to_job(self, job: object, process: object) -> None:
        if self._api.assign_process_to_job(job, process) is not True:
            raise Win32RuntimeError("AssignProcessToJobObject failed")

    def is_process_in_job(self, process: object, job: object) -> bool:
        result = self._api.is_process_in_job(process, job)
        if type(result) is not bool:
            raise Win32RuntimeError("IsProcessInJob returned an invalid result")
        return result

    def resume_thread(self, thread: object) -> int:
        result = self._api.resume_thread(thread)
        if type(result) is not int or not 0 <= result <= 0xFFFFFFFF:
            raise Win32RuntimeError("ResumeThread returned an invalid result")
        if result == 0xFFFFFFFF:
            raise Win32RuntimeError(
                f"ResumeThread failed with error {self._api.get_last_error()}"
            )
        return result

    def close_thread(self, thread: object) -> None:
        self.close_handle(thread)

    def close_handle(self, handle: object) -> None:
        if self._api.close_handle(handle) is not True:
            raise Win32RuntimeError("CloseHandle failed")

    def terminate_job(self, job: object) -> None:
        if self._api.terminate_job_object(job, 1) is not True:
            raise Win32RuntimeError("TerminateJobObject failed")

    def wait_process(self, process: object, milliseconds: int) -> bool:
        if type(milliseconds) is not int or milliseconds < 0:
            raise Win32RuntimeError("valid process wait timeout required")
        result = self._api.wait_for_single_object(process, milliseconds)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            raise Win32RuntimeError(
                f"WaitForSingleObject failed with error {self._api.get_last_error()}"
            )
        raise Win32RuntimeError(f"unexpected process wait result {result}")

    def job_active_count(self, job: object) -> int:
        result = self._api.job_active_process_count(job)
        if type(result) is not int or result < 0:
            raise Win32RuntimeError("invalid Job active-process count")
        return result

    def acquire_mutex(
        self, *, name: str = DEFAULT_MUTEX_NAME, timeout_ms: int = 0
    ) -> ProtectedMutexLease:
        if (
            type(name) is not str
            or not name
            or type(timeout_ms) is not int
            or timeout_ms < 0
        ):
            raise Win32RuntimeError("valid mutex name and timeout required")
        sid = self._api.current_operator_sid()
        sid_parts = sid.split("-") if type(sid) is str else []
        if (
            len(sid_parts) < 3
            or sid_parts[0] != "S"
            or any(not part.isdecimal() for part in sid_parts[1:])
        ):
            raise Win32RuntimeError("valid current operator SID required")
        sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{sid})"
        handle, already_exists = self._api.create_mutex_with_sddl(name, sddl)
        if handle is None:
            raise Win32RuntimeError("CreateMutexW failed")
        if already_exists:
            try:
                dacl_matches = self._api.mutex_dacl_matches_sddl(handle, sddl)
            except BaseException:
                if self._api.close_handle(handle) is not True:
                    raise Win32RuntimeError("mutex handle close failed") from None
                raise
            if dacl_matches is not True:
                if self._api.close_handle(handle) is not True:
                    raise Win32RuntimeError("mutex handle close failed")
                raise Win32RuntimeError("existing mutex DACL mismatch")
        try:
            result = self._api.wait_for_single_object(handle, timeout_ms)
        except BaseException:
            if self._api.close_handle(handle) is not True:
                raise Win32RuntimeError("mutex handle close failed") from None
            raise
        if result == WAIT_OBJECT_0:
            return ProtectedMutexLease(self._api, handle, name, False)
        if result == WAIT_ABANDONED:
            return ProtectedMutexLease(self._api, handle, name, True)
        if self._api.close_handle(handle) is not True:
            raise Win32RuntimeError("mutex handle close failed")
        if result == WAIT_TIMEOUT:
            raise MutexTimeoutError("lifecycle mutex wait timed out")
        if result == WAIT_FAILED:
            raise Win32RuntimeError(
                f"WaitForSingleObject failed with error {self._api.get_last_error()}"
            )
        raise Win32RuntimeError(f"unexpected mutex wait result {result}")
