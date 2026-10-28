"""C 语言语法解析模块。

基于 pycparser 对 C 源码做语法级解析：
1. 归一化源码（统一换行、去除 BOM）；
2. 删除预处理指令（#include / #define 等），但保留空行占位以维持行号一致；
3. 调用 pycparser.CParser 解析出抽象语法树（AST）；
4. 捕获解析错误并尽量定位到出错行。

pycparser 只负责"语法结构是否正确"，逻辑 / 边界错误交给 diagnostic 模块处理。
"""

import re

from pycparser import CParser
from pycparser.plyparser import ParseError

# 匹配行首（可含空白）的预处理指令
_PREPROCESS_RE = re.compile(r"^\s*#")


def normalize_source(source):
    """归一化源码：去除 BOM、统一换行为 \\n。"""
    if source is None:
        return ""
    text = source.lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def strip_preprocessor(source):
    """删除预处理指令行（#include / #define / #if 等），但用空行占位以维持行号。"""
    lines = source.split("\n")
    kept = []
    for line in lines:
        if _PREPROCESS_RE.match(line):
            kept.append("")  # 空行占位，保证后续行号与原文件一致
        else:
            kept.append(line)
    return "\n".join(kept)


def _extract_coord_line(exc):
    """从 ParseError 中提取出错行号（1-based），失败返回 None。"""
    coord = getattr(exc, "coord", None)
    if coord is not None:
        line = getattr(coord, "line", None)
        if isinstance(line, int) and line >= 1:
            return line
    msg = str(exc)
    m = re.search(r":(\d+):(\d+)", msg)
    if m:
        return int(m.group(1))
    m = re.search(r"line\s+(\d+)", msg, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def parse_c(source):
    """解析 C 源码。

    返回 dict：
        ok       : bool，解析是否成功
        ast      : 成功时为 c_ast 根节点，失败为 None
        line     : 失败时的出错行号（可能为 None）
        message  : 失败时的错误信息
    """
    result = {"ok": False, "ast": None, "line": None, "message": None}
    if not source or not source.strip():
        result["message"] = "源码为空，无法解析。"
        return result

    text = normalize_source(source)
    text = strip_preprocessor(text)

    if not text.strip():
        result["message"] = "预处理后无有效代码（可能只包含 #include 等指令）。"
        return result

    try:
        parser = CParser()
        ast = parser.parse(text, filename="<user_code>")
        result["ok"] = True
        result["ast"] = ast
        return result
    except ParseError as exc:
        result["line"] = _extract_coord_line(exc)
        result["message"] = _normalize_error_message(str(exc))
        return result
    except Exception as exc:  # 兜底：任何其它异常也不应令程序崩溃
        result["line"] = None
        result["message"] = "解析时发生未知异常：%s" % exc
        return result


def _normalize_error_message(msg):
    """精简 pycparser 错误信息，去掉过于冗长的内部信息。"""
    if not msg:
        return "未知语法错误。"
    # 去掉 Compiling 之类前缀（parse 直接调用时一般没有）
    text = msg.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 400:
        text = text[:400] + " ..."
    return text


def count_lines(source):
    """返回源码行数。"""
    return normalize_source(source).count("\n") + 1


def get_line_text(source, lineno):
    """按 1-based 行号取源码单行内容（越界返回空串）。"""
    lines = normalize_source(source).split("\n")
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return ""


def extract_error_snippet(source, lineno, radius=2):
    """截取出错行附近代码片段，用于界面高亮展示。

    返回 (snippet, display_start_line)：
      snippet 为带行号的文本块，display_start_line 为块内第一行的真实行号。
    """
    lines = normalize_source(source).split("\n")
    if lineno is None:
        start = 0
    else:
        start = max(0, lineno - 1 - radius)
    end = min(len(lines), start + radius * 2 + 1)
    block = lines[start:end]
    snippet = "\n".join("%4d | %s" % (i + start + 1, block[i - start])
                        for i in range(start, end))
    return snippet, start + 1