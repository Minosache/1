"""LLM 调用接口 + 本地规则讲解 / 修复引擎。

设计原则：
- 默认 **纯本地运行**（不依赖任何云资源）：使用内置规则模板完成讲解与代码修复；
- 可选接入 LLM：设置环境变量 LLM_API_KEY 后，讲解 / 修复 / 习题 / 测试用例
  会优先调用 OpenAI 兼容的 Chat Completions 接口，失败时自动降级回本地模板。

环境变量：
    LLM_API_KEY   API 密钥（不设置则使用本地规则，完全离线）
    LLM_BASE_URL  接口地址，默认 https://api.openai.com/v1
    LLM_MODEL     模型名，默认 gpt-3.5-turbo
"""

import json
import os
import re
import urllib.error
import urllib.request

from . import diff_utils

# ---------------------------------------------------------------------------
# 类别 / 知识点中文映射
# ---------------------------------------------------------------------------

CATEGORY_ZH = {
    "syntax": "语法错误",
    "logic": "逻辑错误",
    "boundary": "边界用例错误",
    "other": "其它",
}

# pattern -> 通俗讲解（大学生视角）
EXPLAIN_DICT = {
    "div_by_zero": "这就像分数不能除以 0。程序中拿 0 当除数，除法结果在数学上是“无穷大”，"
                   "整数类型根本无法表示它，于是计算机只能报错崩溃。",
    "assign_in_condition": "if 里写一个等号 = 是“赋值”，两个等号 == 才是“比较”。"
                           "写成 = 时，a = b 这个表达式的值等于 b，只要 b 不为 0，"
                           "条件就永远成立，跟你想判断“a 是否等于 b”完全是两码事。",
    "scanf_missing_address": "scanf 要把键盘输入写到你的变量里，它必须先知道变量“住在内存的哪个地址”。"
                             "直接写变量名，给到 scanf 的是“变量的值”而不是地址；"
                             "在变量前加一个 &（取地址），才是把它家的门牌号告诉 scanf。",
    "array_oob_constant": "数组下标从 0 开始数。int a[10] 的合法下标是 0~9。"
                          "用 a[10] 相当于去拿第 11 个格子，这块内存不属于你，读出来是乱码、写进去会破坏别的数据。",
    "array_oob_loop": "经典“下标差一”问题：长度 n 的数组最后一个合法下标是 n-1，"
                      "循环条件却写成 i <= n，导致 i 等于 n 时多走一次，访问不存在的元素。",
    "array_init_too_many": "给数组的初值个数超过了它声明的大小，多余的初值没有地方放，"
                           "编译器会报错或把它写到数组边界之外。",
    "string_compare_eq": "C 里的字符串其实是字符数组。用 == 比较两个数组，"
                         "比较的是它们在内存中的首地址，而不是里面的内容；内容相同但地址不同，结果永远是“不相等”。",
    "switch_fallthrough": "switch 中每个 case 只是“跳转入口”标记，并不是“执行完就退出”的边界。"
                          "某个 case 末尾缺 break，程序会继续执行下一个 case 的代码（贯穿），产生隐蔽 bug。",
    "dead_loop": "循环条件恒为真，且循环体里没有 break / return 之类的跳出语句，"
                 "程序就会一直转圈，后面的代码永远执行不到，表现为“卡死”。",
    "uninitialized_variable": "局部变量声明后，它占据的那块内存还是上一段程序留下的旧数据，是随机的垃圾值。"
                              "不赋初值就直接用，结果就像开盲盒，每次运行都可能不一样。",
    "null_pointer_deref": "指针变量保存的是一个内存地址，NULL 表示“还没指向任何有效内存”。"
                          "对空指针做 *p 或 p->x，相当于读写一片不存在的地方，程序立即段错误崩溃。",
    "float_equality": "小数在计算机里用二进制近似存储，0.1 这类十进制小数无法精确表示，"
                      "运算会积累微小误差，直接用 == 判断相等往往失效。",
    "scanf_arg_mismatch": "scanf 格式串里每个 %d、%f 都要对应一个接收变量，格式符和变量的个数、类型必须一一对应。",
    "syntax_error": "编译/解析器在读取你的代码时卡住了，说明存在语法层面问题（缺分号、括号不配对、关键字写错等）。"
                    "编译器会尽量告诉你它大约在第几行发现了问题。",
    "gcc_error": "GCC 编译阶段报错，说明代码无法通过编译，需要按错误提示修正后才能生成可执行程序。",
    "gcc_warning": "GCC 给出警告，代码虽然能编译通过，但存在潜在风险（如未初始化、类型隐式转换等），建议重视并消除。",
}


def _zh_category(category):
    return CATEGORY_ZH.get(category, category)


# ---------------------------------------------------------------------------
# LLM 调用（可选）
# ---------------------------------------------------------------------------

def _env(name, default=""):
    return os.environ.get(name, default)


LLM_API_KEY = _env("LLM_API_KEY")
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = _env("LLM_MODEL", "gpt-3.5-turbo")


def llm_available():
    """是否配好了 LLM 密钥。"""
    return bool(LLM_API_KEY)


def call_llm_chat(messages, temperature=0.3, max_tokens=2000, timeout=60):
    """调用 OpenAI 兼容 Chat Completions 接口，返回文本；失败抛异常。"""
    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + LLM_API_KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError("LLM 接口返回 %s：%s" % (e.code, detail[:300])) from e
    return body["choices"][0]["message"]["content"].strip()


def _try_llm(messages, temperature=0.3, max_tokens=2000):
    """尝试调用 LLM，失败返回 None（由调用方降级到本地模板）。"""
    if not llm_available():
        return None
    try:
        return call_llm_chat(messages, temperature=temperature, max_tokens=max_tokens)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 讲解引擎
# ---------------------------------------------------------------------------

class LocalExplainer:
    """本地规则讲解：把诊断结果整理成通俗、结构化的讲解文本。"""

    def explain(self, diag):
        pattern = diag.get("pattern", "")
        title = diag.get("title", "未知问题")
        category = _zh_category(diag.get("category", "other"))
        kp = diag.get("knowledge_point", "其他")
        message = diag.get("message", "")
        fix_hint = diag.get("fix_hint", "")
        confidence = diag.get("confidence", "high")

        plain = EXPLAIN_DICT.get(pattern)
        if not plain:
            plain = message

        conf_txt = "确定" if confidence == "high" else "疑似（建议人工确认）"

        parts = []
        parts.append("**【%s】%s**  （知识点：%s · 置信度：%s）" % (category, title, kp, conf_txt))
        parts.append("")
        parts.append("**通俗讲讲发生了什么：**")
        parts.append(plain)
        if message and message != plain:
            parts.append("")
            parts.append("**更具体一点：**")
            parts.append(message)
        if fix_hint:
            parts.append("")
            parts.append("**怎么改：**")
            parts.append(fix_hint)
        return "\n".join(parts)


def explain_diagnosis(diag, use_llm=False):
    """生成单条诊断的讲解文本。use_llm 且配好密钥时优先用 LLM。"""
    if use_llm:
        prompt = (
            "你是一位耐心的 C 语言教师。请用大学生容易理解的中文，分三小段讲解下面这个 bug：\n"
            "第一段【错误现象】、第二段【底层原理】、第三段【怎么修改】。语言通俗、不要大段术语。\n\n"
            "bug 信息：\n"
            "类型：%s\n知识点：%s\n标题：%s\n描述：%s\n修复提示：%s\n"
            % (_zh_category(diag.get("category", "")), diag.get("knowledge_point", ""),
               diag.get("title", ""), diag.get("message", ""), diag.get("fix_hint", ""))
        )
        llm_text = _try_llm([{"role": "user", "content": prompt}])
        if llm_text:
            return llm_text
    return LocalExplainer().explain(diag)


# ---------------------------------------------------------------------------
# 修复引擎
# ---------------------------------------------------------------------------

LOCAL_FIXABLE = {
    "assign_in_condition",
    "scanf_missing_address",
    "array_oob_constant",
    "array_oob_loop",
    "uninitialized_variable",
}


class LocalFixer:
    """本地规则修复：对可自动修复的错误模式做精确的源码修改。"""

    def fix(self, source, diags):
        lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        notes = []
        applied = set()

        actionable = [d for d in diags if d.get("pattern") in LOCAL_FIXABLE]
        actionable.sort(key=lambda d: (d.get("line") or 0))

        for d in actionable:
            pattern = d["pattern"]
            meta = d.get("meta", {})
            target_line = d.get("line")
            if pattern == "uninitialized_variable":
                target_line = meta.get("decl_line") or d.get("line")
            if not target_line or target_line < 1 or target_line > len(lines):
                continue
            key = (target_line, pattern, str(meta))
            if key in applied:
                continue
            applied.add(key)

            idx = target_line - 1
            new_line, note = self._apply(pattern, meta, lines[idx])
            if new_line != lines[idx]:
                lines[idx] = new_line
                if note:
                    notes.append("第 %d 行：%s" % (target_line, note))

        # 对无法自动修复的错误，给出明确提示
        for d in diags:
            if d.get("pattern") in LOCAL_FIXABLE:
                continue
            notes.append(
                "【%s】%s —— 建议手动调整：%s"
                % (d.get("title", ""), d.get("message", ""), d.get("fix_hint", ""))
            )

        return "\n".join(lines), notes

    def _apply(self, pattern, meta, line):
        """对单行应用修复，返回 (新行, 说明)。"""
        if pattern == "assign_in_condition":
            new = re.sub(r"(?<![<>=!])=(?!=)", "==", line, count=1)
            if new != line:
                return new, "把条件中的赋值 = 改为比较 =="
            return line, ""

        if pattern == "scanf_missing_address":
            new = self._fix_scanf_line(line)
            if new != line:
                return new, "给 scanf 参数补上加取地址符 &"
            return line, ""

        if pattern == "array_oob_constant":
            base = meta.get("base")
            size = meta.get("size")
            idx = meta.get("idx")
            if base and size is not None and idx is not None:
                pat = r"\b%s\s*\[\s*%d\s*\]" % (re.escape(base), idx)
                repl = "%s[%d]" % (base, size - 1)
                new = re.sub(pat, repl, line)
                if new != line:
                    return new, "把 %s[%d] 改为 %s[%d]（合法范围内）" % (base, idx, base, size - 1)
            return line, ""

        if pattern == "array_oob_loop":
            new = re.sub(r"<=", "<", line, count=1)
            if new != line:
                return new, "把循环边界 <= 改为 <"
            return line, ""

        if pattern == "uninitialized_variable":
            var = meta.get("var")
            if var:
                pat = r"(\b%s\b)(\s*;)" % re.escape(var)
                new = re.sub(pat, r"\1 = 0\2", line)
                if new != line:
                    return new, "变量 %s 声明时初始化为 0" % var
            return line, ""

        return line, ""

    @staticmethod
    def _fix_scanf_line(line):
        """处理 scanf 行：把缺 & 的普通变量补上 &. 返回值改动后的整行。"""
        m = re.search(r"(scanf\s*\(\s*)(.*?)(\)\s*(?:;|\{))", line)
        if not m:
            return line
        prefix = m.group(1)
        args_str = m.group(2)
        tail = m.group(3)

        args = LocalFixer._split_args(args_str)
        if not args:
            return line
        fixed = []
        for i, a in enumerate(args):
            s = a.strip()
            if i == 0:
                fixed.append(a)  # 格式串
                continue
            if s.startswith("&") or s.startswith("*") or s == "NULL":
                fixed.append(a)
            elif re.match(r"^[A-Za-z_]\w*$", s):
                fixed.append("&" + s)
            else:
                fixed.append(a)
        return prefix + ", ".join(fixed) + tail

    @staticmethod
    def _split_args(s):
        """按逗号分割参数，忽略字符串字面量内部的逗号。"""
        parts = []
        cur = []
        in_str = None
        i = 0
        while i < len(s):
            c = s[i]
            if in_str:
                cur.append(c)
                if c == "\\":
                    if i + 1 < len(s):
                        cur.append(s[i + 1])
                        i += 1
                elif c == in_str:
                    in_str = None
            else:
                if c == '"' or c == "'":
                    in_str = c
                    cur.append(c)
                elif c == ",":
                    parts.append("".join(cur))
                    cur = []
                else:
                    cur.append(c)
            i += 1
        if cur:
            parts.append("".join(cur))
        return parts


def fix_code(source, diags, use_llm=False):
    """修复代码，返回 dict：
        fixed_code    修复后的完整代码
        notes         修复过程说明
        ranges        修改行号范围 [(start, end), ...]
        method        'llm' | 'local'
    """
    # 优先 LLM
    if use_llm and llm_available():
        problems = "\n".join(
            "- [第%s行] %s：%s" % (d.get("line") or "?", d.get("title", ""), d.get("message", ""))
            for d in diags
        )
        prompt = (
            "下面是存在 bug 的 C 代码，以及诊断出的问题。请输出修复后的完整 C 代码，"
            "要求：只输出代码本身，不要任何解释，不要 markdown 代码围栏，保持其余逻辑不变。\n\n"
            "【C 代码】\n%s\n\n【诊断问题】\n%s\n" % (source, problems)
        )
        llm_code = _try_llm([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=2500)
        if llm_code:
            fixed = _strip_code_fence(llm_code)
            return {
                "fixed_code": fixed,
                "notes": ["已通过 LLM 生成修复代码。"],
                "ranges": diff_utils.summarize_changes(source, fixed),
                "method": "llm",
            }

    # 本地规则修复
    fixes = LocalFixer()
    fixed_code, notes = fixes.fix(source, diags)
    return {
        "fixed_code": fixed_code,
        "notes": notes,
        "ranges": diff_utils.summarize_changes(source, fixed_code),
        "method": "local",
    }


def _strip_code_fence(text):
    """去掉 LLM 输出可能包裹的 markdown 代码围栏。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()