#!/usr/bin/env python3
"""Validate the main lead table and coverage of an ip-lead-scout Markdown output."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


FIELDS = {
    "organization": ("organization", "机构", "机构名称", "组织"),
    "buyer segment": ("buyer segment", "买家类别", "买方类别", "客户类别", "类别"),
    "specific signal": ("specific signal", "specific signal and decision stage", "具体信号", "具体信号及阶段", "具体信号与阶段", "信号"),
    "event date": ("event date", "事件日期", "事件时间"),
    "source date": ("source date", "来源日期", "发布日期", "来源发布时间"),
    "verification date": ("verification date", "核验日期", "验证日期", "核查日期"),
    "public evidence URL": ("public evidence url", "direct public evidence url", "公开证据url", "公开证据链接", "公开证据", "证据链接", "证据"),
    "IP service angle": ("ip service angle", "plausible ip-service angle", "ip服务切入点", "知识产权服务切入点", "服务切入点"),
    "next action": ("next action", "下一步动作", "后续动作"),
}

HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
SEPARATOR = re.compile(r"^:?-{3,}:?$")
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
RAW_URL = re.compile(r"(?<![\w/])https?://[^\s<>|)]+", re.IGNORECASE)
COMPLETED = re.compile(
    r"已签约|已签署|正式签署|签约完成|完成签约|"
    r"已完成.{0,8}(?:转让|许可|交易)|(?:转让|许可|交易)已完成|"
    r"(?:completed|signed)\s+(?:transfer|licen[cs]e|transaction|agreement|deal|contract)",
    re.IGNORECASE,
)


def normalize(value: str) -> str:
    value = re.sub(r"\*\*|__|`", "", value).casefold().strip()
    value = re.sub(r"（[^）]*）|\([^)]*\)", "", value)
    return re.sub(r"[\s_\-:：/]+", "", value)


ALIASES = {name: {normalize(alias) for alias in aliases} for name, aliases in FIELDS.items()}


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.replace(r"\|", "|").strip() for cell in re.split(r"(?<!\\)\|", line)]


def field_positions(headers: list[str]) -> dict[str, int]:
    return {
        name: index
        for index, header in enumerate(headers)
        for name, aliases in ALIASES.items()
        if normalize(header) in aliases
    }


def evidence_error(cell: str) -> str | None:
    if not cell.strip():
        return "missing field: public evidence URL"
    links = MARKDOWN_LINK.findall(cell)
    if links:
        if all(link.strip().lower().startswith(("http://", "https://")) for link in links):
            return None
        return "invalid public evidence URL (must start with http)"
    if RAW_URL.search(cell):
        return None
    return "invalid public evidence URL (must start with http)"


def validate(content: str) -> list[str]:
    lines = content.splitlines()
    errors: list[str] = []
    tables: list[tuple[int, list[str], list[tuple[int, list[str]]], bool]] = []
    coverage_found = False
    context_level: int | None = None
    inside_context = [False] * len(lines)

    for index, line in enumerate(lines):
        heading = HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).casefold()
            if context_level is not None and level <= context_level:
                context_level = None
            if "context" in title or "背景情报" in title:
                context_level = level
            if "coverage note" in title or "覆盖说明" in title:
                coverage_found = True
        inside_context[index] = context_level is not None

    if not coverage_found:
        errors.append("Missing section: Coverage note / 覆盖说明")

    index = 0
    while index + 1 < len(lines):
        if "|" not in lines[index]:
            index += 1
            continue
        header = split_row(lines[index])
        separator = split_row(lines[index + 1])
        if len(header) < 2 or len(separator) != len(header) or not all(SEPARATOR.fullmatch(cell) for cell in separator):
            index += 1
            continue
        rows: list[tuple[int, list[str]]] = []
        cursor = index + 2
        while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
            rows.append((cursor + 1, split_row(lines[cursor])))
            cursor += 1
        tables.append((index + 1, header, rows, inside_context[index]))
        index = cursor

    non_context = [table for table in tables if not table[3]]
    if not non_context:
        errors.append("Missing section: lead table")
        return errors

    best_score = max(len(field_positions(table[1])) for table in non_context)
    lead_tables = [table for table in non_context if len(field_positions(table[1])) == best_score]
    if best_score < len(FIELDS):
        present = field_positions(lead_tables[0][1])
        errors.extend(f"Missing field: {name}" for name in FIELDS if name not in present)
        return errors

    for _, headers, rows, _ in lead_tables:
        positions = field_positions(headers)
        for line_number, cells in rows:
            row = cells + [""] * (len(headers) - len(cells))
            for name, column in positions.items():
                if not row[column].strip():
                    errors.append(f"Lead row at line {line_number}: missing field: {name}")
            evidence = row[positions["public evidence URL"]]
            if evidence.strip():
                problem = evidence_error(evidence)
                if problem:
                    errors.append(f"Lead row at line {line_number}: {problem}")
            signal = row[positions["specific signal"]]
            if COMPLETED.search(signal):
                errors.append(
                    f"Lead row at line {line_number}: completed transaction must be in context / 背景情报 section"
                )

    lead_row_lines = {line_number for _, _, rows, _ in lead_tables for line_number, _ in rows}
    for index, line in enumerate(lines):
        if inside_context[index] or index + 1 in lead_row_lines or HEADING.match(line):
            continue
        if COMPLETED.search(line) and not re.search(r"没有|不计入|不能|不应|排除|not a lead", line, re.IGNORECASE):
            errors.append(f"Completed transaction at line {index + 1} must be in section: context / 背景情报")

    return errors


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown_file", type=Path, help="Path to the Markdown output")
    args = parser.parse_args()
    try:
        content = args.markdown_file.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"Missing input file or unreadable file: {args.markdown_file} ({exc})")
        return 1
    errors = validate(content)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
