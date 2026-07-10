from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable

import psutil


def _run_powershell_json(script: str, timeout: int = 25):
    if os.name != "nt":
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=flags,
        errors="replace",
    )
    if process.returncode != 0 or not process.stdout.strip():
        return None
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError:
        return None


def _pending_reboot_markers() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    markers = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending", "CBS RebootPending"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", "Windows Update RebootRequired"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager", "PendingFileRenameOperations"),
    ]
    found: list[str] = []
    for hive, path, label in markers:
        try:
            with winreg.OpenKey(hive, path) as key:
                if label == "PendingFileRenameOperations":
                    try:
                        value, _ = winreg.QueryValueEx(key, label)
                        if value:
                            found.append(label)
                    except OSError:
                        pass
                else:
                    found.append(label)
        except OSError:
            continue
    return found


def collect_system_snapshot(
    *,
    cancel_event: Event | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    chunk_callback=None,
) -> dict[str, object]:
    del chunk_callback
    cancel_event = cancel_event or Event()
    progress_callback = progress_callback or (lambda _v, _t: None)
    status_callback = status_callback or (lambda _s: None)
    total_steps = 6
    progress_callback(0, total_steps)

    status_callback("Collecting operating-system and hardware context…")
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).astimezone()
    now = datetime.now(timezone.utc).astimezone()
    uptime = now - boot
    virtual_memory = psutil.virtual_memory()
    data: dict[str, object] = {
        "collected": now.isoformat(timespec="seconds"),
        "computer": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.machine(),
        "python_architecture": platform.architecture()[0],
        "boot_time": boot.isoformat(timespec="seconds"),
        "uptime": str(uptime).split(".")[0],
        "cpu": platform.processor() or "Unknown",
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "memory_total_gib": round(virtual_memory.total / 1024**3, 2),
        "memory_available_gib": round(virtual_memory.available / 1024**3, 2),
        "memory_used_percent": virtual_memory.percent,
    }
    progress_callback(1, total_steps)
    if cancel_event.is_set():
        raise RuntimeError("Operation cancelled.")

    status_callback("Collecting disk usage…")
    disks: list[dict[str, object]] = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append(
            {
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "filesystem": partition.fstype,
                "total_gib": round(usage.total / 1024**3, 2),
                "free_gib": round(usage.free / 1024**3, 2),
                "used_percent": usage.percent,
            }
        )
    data["disks"] = disks
    progress_callback(2, total_steps)

    status_callback("Collecting network interface summary…")
    interfaces: list[dict[str, object]] = []
    for name, addresses in psutil.net_if_addrs().items():
        values = []
        for address in addresses:
            if address.family in (socket.AF_INET, socket.AF_INET6):
                values.append(address.address.split("%")[0])
        if values:
            interfaces.append({"name": name, "addresses": values})
    data["network_interfaces"] = interfaces
    progress_callback(3, total_steps)

    status_callback("Checking reboot-pending indicators…")
    data["pending_reboot_markers"] = _pending_reboot_markers()
    progress_callback(4, total_steps)

    status_callback("Checking automatic services that are not running…")
    services = _run_powershell_json(
        "Get-CimInstance Win32_Service | Where-Object { $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' } | "
        "Select-Object Name,DisplayName,State,StartMode,ExitCode | ConvertTo-Json -Depth 3 -Compress",
        timeout=30,
    )
    if isinstance(services, dict):
        services = [services]
    data["automatic_services_not_running"] = services or []
    progress_callback(5, total_steps)

    status_callback("Collecting recent Windows hotfixes…")
    hotfixes = _run_powershell_json(
        "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 20 HotFixID,Description,InstalledOn | ConvertTo-Json -Depth 3 -Compress",
        timeout=30,
    )
    if isinstance(hotfixes, dict):
        hotfixes = [hotfixes]
    data["recent_hotfixes"] = hotfixes or []
    progress_callback(6, total_steps)

    lines = [
        "### Basic system context",
        "",
        f"- Computer: `{data['computer']}`",
        f"- Platform: {data['platform']}",
        f"- Architecture: {data['architecture']}",
        f"- Boot time: {data['boot_time']}",
        f"- Uptime: {data['uptime']}",
        f"- CPU: {data['cpu']} ({data['physical_cpu_count']} physical / {data['logical_cpu_count']} logical cores)",
        f"- Memory: {data['memory_available_gib']} GiB available of {data['memory_total_gib']} GiB ({data['memory_used_percent']}% used)",
        "",
        "### Disks",
        "",
        "| Device | Mount point | File system | Total GiB | Free GiB | Used |",
        "|---|---|---|---:|---:|---:|",
    ]
    for disk in disks:
        lines.append(
            f"| {disk['device']} | {disk['mountpoint']} | {disk['filesystem']} | {disk['total_gib']} | {disk['free_gib']} | {disk['used_percent']}% |"
        )
    lines.extend(["", "### Network interfaces", ""])
    for interface in interfaces:
        lines.append(f"- **{interface['name']}**: {', '.join(interface['addresses'])}")
    lines.extend(["", "### Pending reboot indicators", ""])
    markers = data["pending_reboot_markers"]
    lines.append("- " + (", ".join(markers) if markers else "None detected by the checked registry markers."))
    lines.extend(["", "### Automatic services not running", ""])
    service_list = data["automatic_services_not_running"]
    if service_list:
        lines.extend(["| Service | Display name | State | Exit code |", "|---|---|---|---:|"])
        for service in service_list[:100]:
            lines.append(
                f"| {service.get('Name','')} | {service.get('DisplayName','')} | {service.get('State','')} | {service.get('ExitCode','')} |"
            )
    else:
        lines.append("- None returned, or the query was unavailable.")
    lines.extend(["", "### Recent hotfixes", ""])
    hotfix_list = data["recent_hotfixes"]
    if hotfix_list:
        lines.extend(["| Hotfix | Description | Installed on |", "|---|---|---|"])
        for hotfix in hotfix_list:
            lines.append(
                f"| {hotfix.get('HotFixID','')} | {hotfix.get('Description','')} | {hotfix.get('InstalledOn','')} |"
            )
    else:
        lines.append("- No hotfix data returned.")
    markdown = "\n".join(lines)
    return {"markdown": markdown, "data": data}
