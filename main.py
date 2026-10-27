"""C 语言代码智能排错与学习助手 —— Streamlit 主界面。

技术栈：Streamlit + pycparser + 本地规则引擎（可选接入 LLM）。
运行：streamlit run main.py
"""

import html
import hashlib

import streamlit as st

from utils import (
    c_parser,
    diagnostic,
    diff_utils,
    exercise_gen,
    knowledge_base,
    llm,
    testcase_gen,
)

# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="C 语言智能排错与学习助手",
    page_icon="🐞",
    layout="wide",
    initial_sidebar_state="expanded",
)

EXAMPLE_CODE = """#include <stdio.h>

int main() {
    int arr[5] = {1, 2, 3, 4, 5, 6};
    int i;
    for (i = 0; i <= 5; i++) {
        arr[i] = arr[i] + 1;
    }
    int x;
    printf("x = %d\\n", x);
    int a = 5, b = 0;
    if (a = b) {
        printf("a 等于 b\\n");
    }
    int c = a / b;
    printf("%d", c);
    return 0;
}"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _dedupe(diags):
    """按（行号, 规则, 标题）去重。"""
    seen = set()
    out = []
    for d in diags:
        key = (d.get("line"), d.get("pattern"), d.get("title"))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _render_error_code_html(source, diags):
    """生成带行号、错误行标红的代码 HTML。"""
    lines = c_parser.normalize_source(source).split("\n")
    err_lines = set()
    for d in diags:
        line = d.get("line")
        if isinstance(line, int) and 1 <= line <= len(lines):
            err_lines.add(line)
    parts = [
        "<div style='font-family:Consolas,\"Courier New\",monospace;font-size:13px;"
        "line-height:1.55;background:#fafafa;border:1px solid #e5e7eb;border-radius:6px;"
        "padding:8px 0;overflow:auto;max-height:360px;'>"
    ]
    for i, line in enumerate(lines, 1):
        if i in err_lines:
            row = ("<div style='background:#fee2e2;color:#b91c1c;'>"
                   "<span style='color:#f87171;display:inline-block;width:3.2em;text-align:right;"
                   "margin-right:10px;user-select:none;'>%d</span>%s</div>")
        else:
            row = ("<div>"
                   "<span style='color:#9ca3af;display:inline-block;width:3.2em;text-align:right;"
                   "margin-right:10px;user-select:none;'>%d</span><span style='color:#1f2937;'>%s</span></div>")
        parts.append(row % (i, html.escape(line)))
    parts.append("</div>")
    return "".join(parts)


def _knowledge_badge(kp):
    return "**`%s`**" % kp


# ---------------------------------------------------------------------------
# 分析流水线
# ---------------------------------------------------------------------------

def run_analysis(source, log_text):
    """整合：诊断 -> 讲解 -> 修复 -> 测试用例 -> 习题。"""
    use_llm = st.session_state.get("use_llm", False)

    result = {
        "source": source,
        "parse": None,
        "diags": [],
        "explanations": {},
        "fix": None,
        "cases": [],
        "exercises": [],
        "main_knowledge_point": "其他",
    }

    if not source or not source.strip():
        result["error"] = "请输入或上传 C 语言源码。"
        return result

    parse_result = c_parser.parse_c(source)
    result["parse"] = parse_result

    engine = diagnostic.DiagnosticEngine()
    diags = engine.run(source, parse_result)

    if log_text and log_text.strip():
        diags.extend(diagnostic.parse_gcc_log(log_text))

    diags = _dedupe(diags)
    result["diags"] = diags

    # 每条诊断的讲解
    explanations = {}
    for i, d in enumerate(diags):
        explanations[i] = llm.explain_diagnosis(d, use_llm=use_llm)
    result["explanations"] = explanations

    # 修复
    if diags:
        result["fix"] = llm.fix_code(source, diags, use_llm=use_llm)
    else:
        result["fix"] = {
            "fixed_code": source,
            "notes": ["未发现明显 bug，代码看起来没问题。"],
            "ranges": [],
            "method": "local",
        }

    # 测试用例
    try:
        result["cases"] = testcase_gen.generate_testcases(source, diags, use_llm=use_llm)
    except Exception:
        result["cases"] = []

    # 主知识点（优先逻辑/边界错误的知识点）
    main_kp = "其他"
    for d in diags:
        kp = d.get("knowledge_point", "其他")
        if kp and kp != "其他":
            main_kp = kp
            break
    result["main_knowledge_point"] = main_kp
    try:
        result["exercises"] = exercise_gen.generate_exercises(main_kp, count=3, use_llm=use_llm)
    except Exception:
        result["exercises"] = []

    return result


def save_to_kb(source, analysis):
    """把当前诊断结果保存到错题本，返回 (新增条数, 是否重复)。"""
    kb = knowledge_base.get_kb()
    diags = analysis.get("diags", [])
    if not diags:
        return 0, False

    titles = "|".join(sorted(d.get("title", "") for d in diags))
    sig = hashlib.md5((source + titles).encode("utf-8")).hexdigest()
    if st.session_state.get("saved_sig") == sig:
        return 0, True

    fix = analysis.get("fix") or {}
    for d in diags:
        kb.add({
            "title": d.get("title", ""),
            "code": source,
            "error_type": d.get("category", "logic"),
            "knowledge_point": d.get("knowledge_point", "其他"),
            "explanation": analysis.get("explanations", {}).get(
                diags.index(d), d.get("message", "")),
            "fixed_code": fix.get("fixed_code", ""),
            "line": d.get("line"),
        })
    st.session_state["saved_sig"] = sig
    return len(diags), False


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------

def render_sidebar():
    with st.sidebar:
        st.header("⚙️ 设置与信息")
        use_llm = st.checkbox(
            "启用 LLM 增强", value=False,
            help="配置环境变量 LLM_API_KEY 后可调用大模型，未配置时自动使用本地规则引擎。",
        )
        st.session_state["use_llm"] = use_llm

        if llm.llm_available():
            st.success("LLM 已连接：`%s`" % llm.LLM_MODEL)
        else:
            st.info("未配置 LLM_API_KEY，当前为纯本地模式（无需联网）。")

        st.divider()
        # 知识库统计
        st.subheader("📚 错题本统计")
        kb = knowledge_base.get_kb()
        cats = kb.categories()
        if not cats:
            st.caption("暂无记录")
        else:
            for kp, cnt in cats:
                st.markdown("- %s：**%d** 道" % (kp, cnt))

        st.divider()
        if st.button("🗑️ 清空错题本"):
            kb.clear()
            st.session_state.pop("saved_sig", None)
            st.warning("错题本已清空。")


# ---------------------------------------------------------------------------
# 左侧：输入区
# ---------------------------------------------------------------------------

def render_input(col):
    with col:
        st.subheader("📝 输入区")

        if st.button("载入示例代码", use_container_width=True):
            st.session_state["src_code"] = EXAMPLE_CODE
            st.session_state.pop("log_text", None)

        code = st.text_area(
            "C 语言源码", height=300,
            key="src_code",
            placeholder="在此粘贴 C 语言源码…",
        )

        log_text = st.text_area(
            "GCC 报错日志（可选）", height=120,
            key="log_text",
            placeholder="粘贴编译器的报错/警告日志，如：main.c:5:3: error: expected ';' …",
        )

        uploaded = st.file_uploader("上传 .c 源代码文件", type=["c", "h", "txt"], key="up_file")

        if st.button("🚀 提交分析", type="primary", use_container_width=True):
            source = (code or "").strip()
            if uploaded is not None:
                try:
                    source = uploaded.getvalue().decode("utf-8", errors="ignore")
                except Exception:
                    st.error("文件读取失败，请确认编码为 UTF-8。")
                    source = ""
            if not source:
                st.warning("请先输入或上传 C 代码。")
            else:
                with st.spinner("正在解析与诊断…"):
                    st.session_state["analysis"] = run_analysis(source, log_text)

        if uploaded is not None:
            st.caption("已上传：%s（提交时优先使用该文件内容）" % uploaded.name)


# ---------------------------------------------------------------------------
# 中间：诊断结果 + 讲解 + 修复
# ---------------------------------------------------------------------------

def render_middle(col):
    with col:
        analysis = st.session_state.get("analysis")
        if not analysis:
            st.info("提交代码后，诊断结果、错误讲解与修复代码将在这里展示。")
            return

        if analysis.get("error"):
            st.warning(analysis["error"])
            return

        st.subheader("🔍 诊断结果")
        diags = analysis.get("diags", [])
        source = analysis.get("source", "")

        if not diags:
            st.success("✅ 未发现明显错误，代码可以正常运行。")
            st.code(source, language="c")
        else:
            # 统计数据
            from collections import Counter
            cat_counter = Counter(d.get("category") for d in diags)
            stat = "、".join("%s %d 处" % (llm.CATEGORY_ZH.get(c, c), n)
                             for c, n in cat_counter.items())
            st.markdown("共发现 **%d** 处问题：%s" % (len(diags), stat))

            # 高亮错误代码
            st.markdown("**👇 错误定位（红色行即为出错行）**")
            st.markdown(_render_error_code_html(source, diags), unsafe_allow_html=True)

            # 逐条讲解
            st.markdown("---")
            st.markdown("### 🧠 错误原理讲解")
            explanations = analysis.get("explanations", {})
            for idx, d in enumerate(diags):
                line = d.get("line")
                line_txt = "第 %d 行" % line if isinstance(line, int) else "位置未知"
                cat = llm.CATEGORY_ZH.get(d.get("category", ""), d.get("category", ""))
                kp = d.get("knowledge_point", "其他")
                with st.expander("%s %s｜%s｜%s" % (d.get("title", ""), line_txt, cat, kp), expanded=(idx == 0)):
                    st.markdown(explanations.get(idx, d.get("message", "")))

            # 修复代码
            st.markdown("---")
            st.markdown("### 🛠️ 修复后代码")
            fix = analysis.get("fix") or {}
            st.code(fix.get("fixed_code", source), language="c")
            for note in fix.get("notes", []):
                st.caption("- " + note)

            # 差异对比
            if fix.get("fixed_code", source) != source:
                with st.expander("📊 查看原代码 vs 修复代码 逐行对比"):
                    st.markdown(
                        diff_utils.render_html_diff(source, fix.get("fixed_code", source)),
                        unsafe_allow_html=True,
                    )

            # 保存错题
            st.markdown("---")
            if st.button("💾 保存到错题本", key="save_kb", use_container_width=True):
                added, dup = save_to_kb(source, analysis)
                if dup:
                    st.info("这些错题已经保存过了。")
                elif added:
                    st.success("已保存 %d 条错题到知识库。" % added)


# ---------------------------------------------------------------------------
# 右侧：测试用例 + 习题 + 历史错题
# ---------------------------------------------------------------------------

def render_right(col):
    with col:
        analysis = st.session_state.get("analysis")

        with st.expander("🧪 自动生成测试用例", expanded=False):
            if analysis and analysis.get("cases"):
                for case in analysis["cases"]:
                    st.markdown(
                        "**%s · %s**\n- 输入：`%s`\n- 预期输出：`%s`\n- 说明：%s"
                        % (case.get("category", ""), case.get("name", ""),
                           str(case.get("input", "")).replace("\n", " "),
                           str(case.get("expected", "")),
                           case.get("note", "")),
                        unsafe_allow_html=False,
                    )
                    st.divider()
            else:
                st.caption("提交代码后自动生成测试用例。")

        with st.expander("📖 同类巩固习题", expanded=False):
            if analysis and analysis.get("exercises"):
                st.caption("基于知识点「%s」" % analysis.get("main_knowledge_point", "其他"))
                for i, ex in enumerate(analysis["exercises"], 1):
                    st.markdown("**%d. %s**\n%s" % (i, ex.get("title", ""), ex.get("desc", "")))
                    with st.expander("查看代码框架 / 参考答案 / 提示"):
                        st.code(ex.get("framework", ""), language="c")
                        st.markdown("**参考答案**")
                        st.code(ex.get("answer", ""), language="c")
                        st.caption("💡 " + ex.get("hint", ""))
            else:
                st.caption("提交代码后根据知识点生成同类练习题。")

        st.markdown("---")
        st.subheader("🔎 历史错题检索（RAG）")
        kb = knowledge_base.get_kb()
        cats = kb.categories()
        kp_options = ["全部分类"] + [kp for kp, _ in cats]

        query = st.text_input("搜索错题 / 知识点", key="kb_query")
        col_a, col_b = st.columns([1, 1])
        with col_a:
            sel_kp = st.selectbox("按知识点过滤", kp_options, key="kb_kp")
        with col_b:
            top_k = st.slider("返回条数", 1, 10, 5, key="kb_topk")

        filter_kp = None if sel_kp == "全部分类" else sel_kp
        results = kb.search(query, top_k=top_k, knowledge_point=filter_kp) if query else []

        if not query:
            st.caption("输入关键词检索历史错题，例如“数组越界”“指针”“scanf”。")
        elif not results:
            st.caption("没有匹配的错题记录。")
        else:
            for r in results:
                rec = r["record"]
                score = r["score"]
                kp = rec.get("knowledge_point", "其他")
                with st.expander("%s（%s · 相似度 %.2f）" % (rec.get("title", "无标题"), kp, score)):
                    st.caption("保存时间：%s｜类型：%s"
                               % (rec.get("time", ""),
                                  llm.CATEGORY_ZH.get(rec.get("error_type", ""), rec.get("error_type", ""))))
                    st.markdown(r["highlight"])
                    if rec.get("code"):
                        st.code(rec["code"], language="c")
                    if rec.get("fixed_code"):
                        st.markdown("**修复后**")
                        st.code(rec["fixed_code"], language="c")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    st.title("🐞 C 语言代码智能排错与学习助手")
    st.caption("面向代码智能开发辅助 · Streamlit + pycparser · 本地运行（可选接入 LLM）")

    render_sidebar()

    col_left, col_mid, col_right = st.columns([1.0, 1.25, 1.0], gap="medium")
    render_input(col_left)
    render_middle(col_mid)
    render_right(col_right)


if __name__ == "__main__":
    main()