from __future__ import annotations

import ipaddress
import os
import subprocess
from typing import List, Optional


def normalize_client_ip(value: str) -> Optional[ipaddress._BaseAddress]:
    raw = (value or "").strip()
    if raw.startswith("::ffff:"):
        raw = raw[7:]
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


def is_lan_address(value: str) -> bool:
    address = normalize_client_ip(value)
    if address is None:
        return False
    if address.is_unspecified or address.is_multicast:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def _private_ipv4(value: str) -> bool:
    address = normalize_client_ip(value)
    return bool(
        address
        and address.version == 4
        and address.is_private
        and not address.is_loopback
    )


def _interface_address(interface: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value if _private_ipv4(value) else None


def _default_interface() -> Optional[str]:
    try:
        completed = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in completed.stdout.splitlines():
        if line.strip().startswith("interface:"):
            return line.split(":", 1)[1].strip()
    return None


def _ifconfig_addresses() -> dict:
    try:
        completed = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    addresses = {}
    interface = ""
    for line in completed.stdout.splitlines():
        if line and not line[0].isspace() and ":" in line:
            interface = line.split(":", 1)[0]
            continue
        fields = line.split()
        if (
            interface
            and len(fields) >= 2
            and fields[0] == "inet"
            and _private_ipv4(fields[1])
        ):
            addresses.setdefault(interface, fields[1])
    return addresses


def find_lan_ip() -> Optional[str]:
    override = os.environ.get("LOCAL_VIDEO_LAN_IP", "").strip()
    if override:
        return override if _private_ipv4(override) else None

    interfaces = [
        item.strip()
        for item in os.environ.get(
            "LOCAL_VIDEO_WIFI_INTERFACES",
            "en0,en1",
        ).split(",")
        if item.strip()
    ]
    default_interface = _default_interface()
    if default_interface and default_interface not in interfaces:
        interfaces.append(default_interface)

    for interface in interfaces:
        address = _interface_address(interface)
        if address:
            return address
    fallback = _ifconfig_addresses()
    for interface in interfaces:
        address = fallback.get(interface)
        if address:
            return address
    for interface in sorted(fallback):
        if not interface.startswith(("lo", "utun")):
            return fallback[interface]
    return None
