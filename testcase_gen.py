"""自动生成测试用例。

针对给定的 C 程序，生成多组测试用例，每组包含：
- 类别：正常输入 / 边界输入 / 异常输入
- 输入：按程序 scanf 顺序给出
- 预期输出：根据识别的程序意图推导（无法识别时给出逻辑说明）

程序意图识别基于常见算法模式的关键词与结构特征；配置了 LLM 时可选调用 LLM 生成
更贴合代码的用例，失败自动降级为本地模板。
"""

import re

from . import llm


def parse_inputs(source):
    """解析 scanf 的输入变量：返回 [{spec, name}, ...]。"""
    inputs = []
    for m in re.finditer(r"\b(?:scanf|fscanf|sscanf)\s*\((.*?)\)\s*;", source, re.DOTALL):
        call = m.group(1)
        fm = re.search(r'"([^"]*)"', call)
        if not fm:
            continue
        fmt = fm.group(1)
        fmt_plain = fmt.replace("%%", "")
        specs = re.findall(r"%[-+0-9.*hlLjzt]*([diuoxXfFeEgGaAcspn])", fmt_plain)
        rest = call[fm.end():]
        var_list = [v.strip() for v in rest.split(",") if v.strip()]
        for i, sp in enumerate(specs):
            name = ""
            if i < len(var_list):
                name = re.sub(r"^[&*]+", "", var_list[i])
                name = re.match(r"[A-Za-z_]\w*", name)
                name = name.group(0) if name else ""
            inputs.append({"spec": sp, "name": name})
    return inputs


def _mode_from_spec(char):
    return "int" if char in "diuoxXc" else "float"


def detect_mode(source, inputs):
    """识别程序意图，返回模式字符串（无匹配返回 generic）。"""
    low = source.lower()

    def has(*keys):
        return any(k in low for k in keys)

    if has("factorial", "fact", "阶乘") or re.search(r"\*\s*=\s*i", source):
        return "factorial"
    if has("fib", "fibonacci", "斐波那契"):
        return "fibonacci"
    if has("prime", "素数", "质数", "isprime") or ("%" in source and has("质", "素")):
        return "is_prime"
    if has("gcd", "最大公约", "公约", "辗转"):
        return "gcd"
    if has("sort", "排序", "冒泡", "bubble", "选择排序", "插入排序"):
        return "sort"
    if has("reverse", "反转", "逆序", "倒序"):
        return "reverse"
    if has("maximum", "max", "最大", "maxx") and not has("最小", "min"):
        return "max"
    if has("minimum", "min", "最小"):
        return "min"
    if has("sum", "求和", "累加", "total", "平均数", "avg", "average"):
        return "sum"
    return "generic"


def _build_cases(mode, inputs):
    """按模式生成测试用例列表。"""
    cases = []

    # 输入变量的“一个完整样例”拼接
    def inp_txt(*vals):
        return "\n".join(str(v) for v in vals)

    n = len(inputs)

    if mode == "sum":
        cases += [
            {"category": "正常输入", "name": "正常求和",
             "input": "5\n1 2 3 4 5", "expected": "15",
             "note": "5 个数 1~5 的和为 15。"},
            {"category": "边界输入", "name": "元素个数为 0",
             "input": "0", "expected": "0",
             "note": "没有元素时和应为 0，需注意循环不会越界。"},
            {"category": "边界输入", "name": "单个元素",
             "input": "1\n999", "expected": "999",
             "note": "只有一个数时直接输出该数。"},
            {"category": "异常输入", "name": "输入非数字",
             "input": "abc", "expected": "程序应能提示输入非法而非崩溃",
             "note": "scanf 的返回值可用于判断是否成功读取。"},
        ]

    elif mode == "max":
        cases += [
            {"category": "正常输入", "name": "正常求最大",
             "input": "5\n3 9 2 9 7", "expected": "9",
             "note": "最大值 9，注意相等元素不影响结果。"},
            {"category": "边界输入", "name": "全为负数",
             "input": "3\n-1 -5 -3", "expected": "-1",
             "note": "若 max 初值设为 0 会得到错误结果 0，应初始化为第一个元素或极小值。"},
            {"category": "边界输入", "name": "单个元素",
             "input": "1\n42", "expected": "42", "note": "只有 1 个数直接输出。"},
            {"category": "异常输入", "name": "空数组 / 非法输入",
             "input": "0", "expected": "应友好处理，不访问越界元素",
             "note": "个数为 0 时不应访问数组第一个元素。"},
        ]

    elif mode == "min":
        cases += [
            {"category": "正常输入", "name": "正常求最小",
             "input": "4\n10 2 8 5", "expected": "2", "note": "最小值 2。"},
            {"category": "边界输入", "name": "全为正数",
             "input": "3\n100 200 50", "expected": "50", "note": "最小值 50。"},
            {"category": "异常输入", "name": "非数字输入",
             "input": "xyz", "expected": "应提示非法输入", "note": "检查 scanf 返回值。"},
        ]

    elif mode == "factorial":
        cases += [
            {"category": "正常输入", "name": "阶乘常规值",
             "input": "5", "expected": "120", "note": "5! = 120。"},
            {"category": "边界输入", "name": "0 的阶乘",
             "input": "0", "expected": "1", "note": "数学上 0! = 1，注意边界处理。"},
            {"category": "边界输入", "name": "较大值 / 溢出",
             "input": "20", "expected": "2432902008176640000（int 放不下，需用 long long）",
             "note": "20! 超过 32 位 int 上限，注意溢出的整数使用。"},
            {"category": "异常输入", "name": "负数",
             "input": "-3", "expected": "应拒绝或提示，阶乘对负数无定义",
             "note": "负数无阶乘定义，需加判断。"},
        ]

    elif mode == "fibonacci":
        cases += [
            {"category": "正常输入", "name": "第 6 项",
             "input": "6", "expected": "8", "note": "斐波那契：1,1,2,3,5,8。"},
            {"category": "边界输入", "name": "第 1 / 2 项",
             "input": "1", "expected": "1", "note": "前两项为基准情况，注意单独处理。"},
            {"category": "异常输入", "name": "非正整数",
             "input": "0", "expected": "应友好处理或按定义输出 0", "note": "注意边界约定。"},
        ]

    elif mode == "is_prime":
        cases += [
            {"category": "正常输入", "name": "素数",
             "input": "7", "expected": "是素数（prime）", "note": "7 只有 1 和 7 两个因子。"},
            {"category": "边界输入", "name": "1 不是素数",
             "input": "1", "expected": "不是素数", "note": "1 既非素数也非合数，须单独判断。"},
            {"category": "边界输入", "name": "2 是最小素数",
             "input": "2", "expected": "是素数", "note": "2 是唯一的偶数素数。"},
            {"category": "异常输入", "name": "负数",
             "input": "-5", "expected": "不是素数", "note": "负数不能判定为素数。"},
        ]

    elif mode == "gcd":
        cases += [
            {"category": "正常输入", "name": "常规公约数",
             "input": "12 18", "expected": "6", "note": "gcd(12,18)=6。"},
            {"category": "边界输入", "name": "互质",
             "input": "8 15", "expected": "1", "note": "互质时最大公约数为 1。"},
            {"category": "异常输入", "name": "含 0",
             "input": "0 5", "expected": "5（gcd(0,5)=5）或按约定处理",
             "note": "0 与一个数的公约数是该数本身，注意约定。"},
        ]

    elif mode == "sort":
        cases += [
            {"category": "正常输入", "name": "乱序数组",
             "input": "5\n4 1 3 9 2", "expected": "1 2 3 4 9", "note": "升序排列结果。"},
            {"category": "边界输入", "name": "已有序 / 全相同",
             "input": "4\n5 5 5 5", "expected": "5 5 5 5", "note": "相同元素稳定输出。"},
            {"category": "异常输入", "name": "单个元素",
             "input": "1\n8", "expected": "8", "note": "单个元素无需比较。"},
        ]

    elif mode == "reverse":
        cases += [
            {"category": "正常输入", "name": "反转常规数组",
             "input": "5\n1 2 3 4 5", "expected": "5 4 3 2 1", "note": "首尾对调。"},
            {"category": "边界输入", "name": "单个 / 空",
             "input": "1\n7", "expected": "7", "note": "边界无需交换。"},
            {"category": "异常输入", "name": "非数字输入",
             "input": "abc", "expected": "应提示非法输入", "note": "检查 scanf 返回值。"},
        ]

    else:  # generic
        var_txt = ", ".join(i["name"] or ("变量%d" % (k + 1)) for k, i in enumerate(inputs))
        sample = "\n".join(str(k + 1) for k in range(n)) if n else "（无输入）"
        cases += [
            {"category": "正常输入", "name": "一组使程序正常运行的输入",
             "input": sample if n else "（无需输入）",
             "expected": "根据程序逻辑推导并记录",
             "note": "观察 %s 的输出是否符合预期。" % (var_txt or "程序")},
            {"category": "边界输入", "name": "最小 / 边界值",
             "input": "\n".join(["0"] * n) if n else "（无需输入）",
             "expected": "重点观察循环边界、数组下标",
             "note": "用 0、1 等边界值测试条件分支。"},
            {"category": "异常输入", "name": "非法 / 越界输入",
             "input": "\n".join(["-1" if _mode_from_spec(i["spec"]) == "int" else "abc"
                                 for i in inputs]) if n else "（无需输入）",
             "expected": "程序不应崩溃，应能处理或提示",
             "note": "负数、非数字、超范围是常见的健壮性测试。"},
        ]

    return cases


def generate_testcases(source, diags=None, use_llm=False):
    """生成测试用例主入口。返回 list[case]。"""
    inputs = parse_inputs(source)
    mode = detect_mode(source, inputs)

    if use_llm and llm.llm_available():
        prompt = (
            "为下面的 C 程序设计 3 组测试用例：正常输入、边界输入、异常输入各一组，"
            "并给出每组输入与预期输出。用 JSON 数组返回，每项字段为 "
            "category/name/input/expected/note。不要输出代码围栏以外的内容。\n\n"
            "C 代码：\n%s" % source
        )
        result = llm._try_llm([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=1200)
        if result:
            parsed = _parse_llm_cases(result)
            if parsed:
                return parsed

    return _build_cases(mode, inputs)


def _parse_llm_cases(text):
    """尝试把 LLM 返回解析为用例列表，失败返回 []。"""
    import json
    try:
        text = text.strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            data = json.loads(text[start:end + 1])
        else:
            data = json.loads(text)
        if isinstance(data, list):
            out = []
            for d in data:
                if isinstance(d, dict):
                    out.append({
                        "category": str(d.get("category", "正常输入")),
                        "name": str(d.get("name", "")),
                        "input": str(d.get("input", "")),
                        "expected": str(d.get("expected", "")),
                        "note": str(d.get("note", "")),
                    })
            return out
    except Exception:
        pass
    return []