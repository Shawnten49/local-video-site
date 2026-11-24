#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class SplitResult:
    original_path: Path
    original_line_count: int
    unique_urls: List[str]
    duplicate_count: int
    output_paths: List[Path]


def read_unique_urls(path: Path) -> tuple:
    """Read non-empty lines and keep only the first occurrence of each URL."""

    seen = set()
    urls: List[str] = []
    original_line_count = 0
    duplicate_count = 0

    with path.open("r", encoding="utf-8-sig") as stream:
        for raw_line in stream:
            url = raw_line.strip()
            if not url:
                continue
            original_line_count += 1
            if url in seen:
                duplicate_count += 1
                continue
            seen.add(url)
            urls.append(url)

    return urls, original_line_count, duplicate_count


def chunk_urls(urls: Iterable[str], chunk_size: int = 15) -> List[List[str]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    values = list(urls)
    if not values:
        return [[]]
    return [
        values[index : index + chunk_size]
        for index in range(0, len(values), chunk_size)
    ]


def output_paths_for(input_path: Path, count: int, output_dir: Path) -> List[Path]:
    stem = input_path.stem
    return [
        output_dir / "{}-{}{}".format(stem, index, input_path.suffix or ".txt")
        for index in range(1, count + 1)
    ]


def split_url_file(
    input_path: Path,
    output_dir: Optional[Path] = None,
    chunk_size: int = 15,
    force: bool = False,
) -> SplitResult:
    input_path = input_path.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError("Input TXT file not found: {}".format(input_path))

    output_dir = (
        output_dir.expanduser().resolve()
        if output_dir is not None
        else input_path.parent
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    urls, original_line_count, duplicate_count = read_unique_urls(input_path)
    chunks = chunk_urls(urls, chunk_size=chunk_size)
    output_paths = output_paths_for(input_path, len(chunks), output_dir)

    existing = [path for path in output_paths if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "Output file(s) already exist; use --force to overwrite: {}".format(
                ", ".join(str(path) for path in existing)
            )
        )

    for output_path, chunk in zip(output_paths, chunks):
        content = "\n".join(chunk)
        if content:
            content += "\n"
        output_path.write_text(content, encoding="utf-8")

    return SplitResult(
        original_path=input_path,
        original_line_count=original_line_count,
        unique_urls=urls,
        duplicate_count=duplicate_count,
        output_paths=output_paths,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deduplicate a URL TXT file and split it into chunks of 15."
    )
    parser.add_argument("txt_file", type=Path, help="Original URL TXT file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (defaults to the original TXT directory)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=15,
        help="URLs per output file (default: 15)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing split files",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = split_url_file(
            input_path=args.txt_file,
            output_dir=args.output_dir,
            chunk_size=args.chunk_size,
            force=args.force,
        )
    except (OSError, ValueError) as exc:
        print("处理失败：{}".format(exc))
        return 1

    effective_line_count = len(result.unique_urls)
    print("原始文件（未修改）：{}".format(result.original_path))
    print("有效 URL 行数：{}".format(result.original_line_count))
    print("重复 URL 删除数：{}".format(result.duplicate_count))
    print("去重后 URL 数：{}".format(effective_line_count))
    print("输出文件数：{}".format(len(result.output_paths)))
    for path in result.output_paths:
        print("  {}".format(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
