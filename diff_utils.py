"""代码差异对比与高亮工具。

为"修复前后代码对比"提供支持：
- make_unified_diff   : 生成标准 unified diff 文本
- render_html_diff    : 生成左右并排、带颜色标注的 HTML 对比表
- summarize_changes   : 汇总本次修改涉及的起止行号
"""

import difflib
import html


def _split_lines(text):
    if text is None:
        return []
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def make_unified_diff(original, fixed, file_a="原代码", file_b="修复后"):
    """生成 unified diff（字符串），便于命令行式查看差异。"""
    a = _split_lines(original)
    b = _split_lines(fixed)
    diff = difflib.unified_diff(a, b, fromfile=file_a, tofile=file_b, lineterm="")
    return "\n".join(diff)


def _escape(text):
    return html.escape(text, quote=False)


def render_html_diff(original, fixed):
    """生成左右并排的 HTML 对比表。

    左侧为原代码（删除/修改行标红），右侧为修复后代码（新增/修改行标绿）。
    兼容 Streamlit 的 st.markdown(..., unsafe_allow_html=True)。
    """
    a = _split_lines(original)
    b = _split_lines(fixed)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)

    left_rows = []
    right_rows = []

    def add_pair(l_line, l_no, l_cls, r_line, r_no, r_cls):
        left_rows.append(
            '<tr><td class="ln">%s</td><td class="%s">%s</td></tr>'
            % (l_no if l_no else "", l_cls, _escape(l_line) if l_line is not None else "")
        )
        right_rows.append(
            '<tr><td class="ln">%s</td><td class="%s">%s</td></tr>'
            % (r_no if r_no else "", r_cls, _escape(r_line) if r_line is not None else "")
        )

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                add_pair(a[i], i + 1, "same", b[j], j + 1, "same")
        elif tag == "delete":
            for i in range(i1, i2):
                add_pair(a[i], i + 1, "del", None, None, "same")
        elif tag == "insert":
            for j in range(j1, j2):
                add_pair(None, None, "same", b[j], j + 1, "add")
        elif tag == "replace":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                add_pair(a[i], i + 1, "del", b[j], j + 1, "add")
            # 长度不一致时补齐
            for i in range(i1 + (j2 - j1), i2):
                add_pair(a[i], i + 1, "del", None, None, "same")
            for j in range(j1 + (i2 - i1), j2):
                add_pair(None, None, "same", b[j], j + 1, "add")

    css = (
        "<style>"
        ".diff-table{border-collapse:collapse;width:100%;font-family:'Consolas','Courier New',monospace;"
        "font-size:13px;}"
        ".diff-table td{border:1px solid #e5e7eb;padding:1px 8px;white-space:pre;vertical-align:top;}"
        ".diff-table .ln{color:#9ca3af;background:#f9fafb;text-align:right;user-select:none;width:2.5em;}"
        ".diff-table .same{background:#ffffff;color:#1f2937;}"
        ".diff-table .del{background:#fee2e2;color:#b91c1c;}"
        ".diff-table .add{background:#d1fae5;color:#065f46;}"
        "</style>"
    )
    left_html = "<table class='diff-table'><colgroup><col><col></colgroup>" + "".join(left_rows) + "</table>"
    right_html = "<table class='diff-table'><colgroup><col><col></colgroup>" + "".join(right_rows) + "</table>"

    wrapper = (
        "<div style='display:flex;gap:12px;'>"
        "<div style='flex:1;overflow:auto;'><b>🔴 原代码</b>{}</div>"
        "<div style='flex:1;overflow:auto;'><b>🟢 修复后代码</b>{}</div>"
        "</div>"
    ).format(left_html, right_html)
    return css + wrapper


def summarize_changes(original, fixed):
    """返回本次修改涉及的行号范围列表 [(start, end), ...]，用于"修改位置"标注。"""
    a = _split_lines(original)
    b = _split_lines(fixed)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ranges = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            ranges.append((i1 + 1, i2))
    return ranges