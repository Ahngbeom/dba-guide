#!/usr/bin/env python3
"""Filter Markdown content down to a single DBMS view.

Reads a file and strips out `<!-- dbms:X --> ... <!-- /dbms:X -->` blocks
that don't match the target DBMS. Blocks matching the target DBMS keep
their content but lose the marker comment lines. Lines outside any marker
pass through untouched, so files with no markers are unchanged byte-for-byte.
"""
import re
import sys

OPEN_RE = re.compile(r"^\s*<!--\s*dbms:(\w+)\s*-->\s*$")
CLOSE_RE = re.compile(r"^\s*<!--\s*/dbms:(\w+)\s*-->\s*$")
FENCE_RE = re.compile(r"^\s*```")


def filter_lines(lines, target):
    out = []
    stack = []  # names of currently open dbms blocks, outermost first
    in_fence = False  # markers inside ``` code fences are illustrative text, not real directives
    for line in lines:
        is_fence_toggle = bool(FENCE_RE.match(line))
        if not in_fence and not is_fence_toggle:
            open_match = OPEN_RE.match(line)
            if open_match:
                stack.append(open_match.group(1))
                continue
            close_match = CLOSE_RE.match(line)
            if close_match:
                name = close_match.group(1)
                if not stack or stack[-1] != name:
                    raise ValueError(f"Unbalanced dbms marker: {line!r}")
                stack.pop()
                continue
        if is_fence_toggle:
            in_fence = not in_fence
        if any(name != target for name in stack):
            continue
        out.append(line)
    if stack:
        raise ValueError(f"Unclosed dbms marker(s): {stack}")
    if in_fence:
        raise ValueError("Unclosed code fence")
    return out


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <target-dbms> <file>", file=sys.stderr)
        return 2
    target, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    try:
        filtered = filter_lines(lines, target)
    except ValueError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(filtered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
