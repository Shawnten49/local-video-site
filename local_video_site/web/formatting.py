from __future__ import annotations


def format_duration(value) -> str:
    if value is None:
        return "--:--"
    total = max(0, int(round(float(value))))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, seconds)
    return "{:02d}:{:02d}".format(minutes, seconds)


def format_elapsed_duration(value) -> str:
    if value is None:
        return "—"
    total = max(0, int(value))
    days, remainder = divmod(total, 86400)
    if days:
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        return "{}天 {:02d}:{:02d}:{:02d}".format(
            days,
            hours,
            minutes,
            seconds,
        )
    return format_duration(total)


def format_bytes(value) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return "{:.1f} TiB".format(size)


def format_resolution(width, height) -> str:
    if width and height:
        if height >= 2160:
            label = "4K"
        else:
            label = "{}p".format(height)
        return "{}×{} · {}".format(width, height, label)
    return "分辨率未知"
