"""代码智能诊断引擎。

职责：
1. 语法错误诊断（基于 c_parser 的解析结果）；
2. GCC 报错日志解析（提取错误 / 警告行号与类型）；
3. 基于 AST 的规则诊断，识别逻辑错误与边界错误，并定位行号、区分类型、映射知识点。

每条诊断（dict）字段：
    category        'syntax' | 'logic' | 'boundary'   错误类型
    title           简短标题
    message         详细说明（错误原理）
    line            出错行号（可能为 None）
    knowledge_point 知识点分类（指针/循环/数组/结构体/字符串/运算符/函数/语法/内存/其他）
    fix_hint        修复提示
    confidence      'high' | 'medium'
    pattern         内部规则标识（供修复模块使用）
"""

import re

from pycparser import c_ast

# 知识点分类
KNOWLEDGE_POINTS = [
    "语法", "指针", "循环", "数组", "结构体",
    "字符串", "运算符", "函数", "内存", "其他",
]

# C 语言常用关键字（用于 GCC 日志 -> 知识点映射的辅助）
_KEYWORD_KNOWLEDGE = {
    "pointer": "指针", "dereference": "指针", "null": "指针",
    "uninitialized": "函数", "uninitialised": "函数",
    "overflow": "数组", "array": "数组", "bounds": "数组",
    "implicit": "语法", "declaration": "语法", "expected": "语法",
    "loop": "循环", "string": "字符串", "strcmp": "字符串",
    "division": "运算符", "by zero": "运算符",
}


def _coord_line(node):
    coord = getattr(node, "coord", None)
    if coord is not None:
        return getattr(coord, "line", None)
    return None


def _make_diag(category, title, message, line, knowledge_point,
               fix_hint="", confidence="high", pattern="", meta=None):
    return {
        "category": category,
        "title": title,
        "message": message,
        "line": line,
        "knowledge_point": knowledge_point,
        "fix_hint": fix_hint,
        "confidence": confidence,
        "pattern": pattern,
        "meta": meta or {},
    }


# ---------------------------------------------------------------------------
# GCC 报错日志解析
# ---------------------------------------------------------------------------

_GCC_LINE_RE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+):\s*"
    r"(?P<kind>error|warning|note|fatal error):\s*(?P<msg>.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_gcc_log(log_text):
    """解析 GCC 编译日志，返回诊断列表。"""
    diags = []
    if not log_text or not log_text.strip():
        return diags
    for m in _GCC_LINE_RE.finditer(log_text):
        kind = m.group("kind").lower()
        msg = m.group("msg").strip()
        line = int(m.group("line"))
        if kind == "note":
            continue  # note 是辅助信息，不单独列为诊断
        kp = "其他"
        for key, value in _KEYWORD_KNOWLEDGE.items():
            if key in msg.lower():
                kp = value
                break
        if kind in ("warning",):
            category = "logic"
            title = "GCC 警告"
        else:
            category = "syntax"
            title = "GCC 编译错误"
        diags.append(_make_diag(
            category=category,
            title=title,
            message=msg,
            line=line,
            knowledge_point=kp,
            fix_hint="请参考 GCC 提示定位并修正对应代码行。",
            confidence="high",
            pattern="gcc_error" if category == "syntax" else "gcc_warning",
        ))
    return diags


# ---------------------------------------------------------------------------
# AST 辅助
# ---------------------------------------------------------------------------

def _is_zero(node):
    return isinstance(node, c_ast.Constant) and str(node.value).strip() == "0"


def _is_string(node):
    return isinstance(node, c_ast.Constant) and getattr(node, "type", "") == "string"


def _is_float_const(node):
    if isinstance(node, c_ast.Constant):
        return "." in str(node.value) or "e" in str(node.value).lower()
    # 强类型浮点常量
    return getattr(node, "type", "") in ("float", "double")


def _array_base_name(node):
    """取数组引用的最外层名字（可能为嵌套 ArrayRef）。"""
    seen = 0
    while isinstance(node, c_ast.ArrayRef) and seen < 8:
        node = node.name
        seen += 1
    if isinstance(node, c_ast.ID):
        return node.name
    return None


def _const_int(node):
    """尝试把节点解析为整数（用于数组下标 / 数组大小比较）。"""
    if isinstance(node, c_ast.Constant):
        try:
            return int(node.value, 0)
        except (ValueError, TypeError):
            return None
    return None


def _declared_array_size(decl):
    """返回数组声明的第一维大小（int 或 None）。"""
    t = decl.type
    if isinstance(t, c_ast.ArrayDecl):
        return _const_int(t.dim)
    return None


# ---------------------------------------------------------------------------
# 数组大小收集器
# ---------------------------------------------------------------------------

class _ArraySizeCollector(c_ast.NodeVisitor):
    """收集所有数组声明：name -> (size 或 None)。"""

    def __init__(self):
        self.sizes = {}

    def visit_Decl(self, node):
        size = _declared_array_size(node)
        if size is not None:
            self.sizes[node.name] = size
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# 规则分析器（除零 / 条件赋值 / 数组越界 / switch / 死循环 / 字符串比较等）
# ---------------------------------------------------------------------------

class _RuleVisitor(c_ast.NodeVisitor):

    def __init__(self, array_sizes):
        self.array_sizes = array_sizes
        self.diags = []
        # 空指针检测辅助
        self.deref_pointers = {}   # name -> line
        self.nullchecked = set()
        # 浮点比较辅助：收集声明为 float/double 的变量
        self.float_vars = set()


    # ---- 除零 / 字符串比较 / 浮点相等 / 空指针比较 ----
    def visit_BinaryOp(self, node):
        if node.op in ("/", "%") and _is_zero(node.right):
            self.diags.append(_make_diag(
                category="logic",
                title="除零 / 取模零错误",
                message="运行时对 0 做除法或取模会导致程序崩溃（SIGFPE，浮点除法除以 0）。"
                        "分母为 0 时结果无定义，必须在使用前判断分母是否为 0。",
                line=_coord_line(node),
                knowledge_point="运算符",
                fix_hint="在除法/取模前加上 if (除数 != 0) 的判断，或保证除数不可能为 0。",
                confidence="high",
                pattern="div_by_zero",
            ))

        if node.op in ("==", "!="):
            if _is_string(node.left) or _is_string(node.right):
                self.diags.append(_make_diag(
                    category="logic",
                    title="字符串用 == 比较错误",
                    message="用 == 比较两个字符串，比较的是数组的首地址（指针值），"
                            "而不是字符串内容。只要两块内存地址不同，比较结果就永远是“不相等”。",
                    line=_coord_line(node),
                    knowledge_point="字符串",
                    fix_hint="改用 strcmp(a, b) == 0 比较内容，并确认已 #include <string.h>。",
                    confidence="high",
                    pattern="string_compare_eq",
                ))
            if _is_float_const(node.left) or _is_float_const(node.right) \
                    or (isinstance(node.left, c_ast.ID) and node.left.name in self.float_vars) \
                    or (isinstance(node.right, c_ast.ID) and node.right.name in self.float_vars):
                self.diags.append(_make_diag(
                    category="logic",
                    title="浮点数用 == 判等不可靠",
                    message="浮点数在计算机中是近似存储的，直接 == 比较很可能因精度误差而失效"
                            "（例如 0.1 + 0.2 不等于 0.3）。",
                    line=_coord_line(node),
                    knowledge_point="运算符",
                    fix_hint="改用 fabs(a - b) < 1e-6 这种“误差小于阈值”的方式判断相等。",
                    confidence="medium",
                    pattern="float_equality",
                ))

        # 空指针比较：p == NULL / p != NULL
        op_ok = node.op in ("==", "!=")
        if op_ok:
            for side in (node.left, node.right):
                if isinstance(side, c_ast.ID) and side.name in ("NULL", "nullptr"):
                    other = node.right if side is node.left else node.left
                    if isinstance(other, c_ast.ID):
                        self.nullchecked.add(other.name)

        self.generic_visit(node)

    # ---- 条件中赋值（= 与 == 混淆） ----
    def visit_If(self, node):
        self._check_assign_in_cond(node.cond)
        self.generic_visit(node)

    def visit_While(self, node):
        self._check_assign_in_cond(node.cond)
        self._check_dead_loop(node.cond, node.stmt)
        self.generic_visit(node)

    def visit_DoWhile(self, node):
        self._check_assign_in_cond(node.cond)
        self.generic_visit(node)

    def visit_For(self, node):
        if node.cond is not None:
            self._check_assign_in_cond(node.cond)
            self._check_off_by_one(node)
        self.generic_visit(node)

    def _check_assign_in_cond(self, cond):
        if isinstance(cond, c_ast.Assignment):
            self.diags.append(_make_diag(
                category="logic",
                title="条件里误用了赋值 =",
                message="判断条件里写成了单个等号 =（赋值），而本意通常是 ==（比较）。"
                        "赋值表达式的值恒等于被赋的值，非 0 时条件永远成立，导致程序逻辑错误。",
                line=_coord_line(cond),
                knowledge_point="运算符",
                fix_hint="把条件中的单个 = 改成 ==（若确实想做赋值，请加外括号明确意图）。",
                confidence="high",
                pattern="assign_in_condition",
            ))

    def _check_dead_loop(self, cond, stmt):
        if isinstance(cond, c_ast.Constant) and str(cond.value).strip() not in ("0",):
            if not self._contains_break_or_return(stmt):
                self.diags.append(_make_diag(
                    category="logic",
                    title="疑似死循环",
                    message="循环条件恒为真，且循环体中没有 break / return 等跳出语句，程序可能无限循环。",
                    line=_coord_line(cond),
                    knowledge_point="循环",
                    fix_hint="在循环体内满足某个条件时用 break 跳出，或让循环条件可变。",
                    confidence="medium",
                    pattern="dead_loop",
                ))

    def _contains_break_or_return(self, node):
        found = {"v": False}

        class _Finder(c_ast.NodeVisitor):
            def visit_Break(self, n):
                found["v"] = True

            def visit_Return(self, n):
                found["v"] = True

        # 避免把嵌套循环的 break 误判为本层跳出——这里做粗略判断即可
        _Finder().visit(node)
        return found["v"]

    # ---- 数组越界 ----
    def visit_ArrayRef(self, node):
        base = _array_base_name(node)
        idx = _const_int(node.subscript)
        size = self.array_sizes.get(base)
        if base is not None and size is not None and idx is not None and idx >= size:
            self.diags.append(_make_diag(
                category="boundary",
                title="数组下标越界",
                message="数组 %s 声明了 %d 个元素（下标范围 0 ~ %d），"
                        "但访问了下标 %d，属于越界访问，会读写到非法内存。"
                        % (base, size, size - 1, idx),
                line=_coord_line(node),
                knowledge_point="数组",
                fix_hint="把下标限制在 0 ~ %d 范围内，或适当扩大数组长度。" % (size - 1),
                confidence="high",
                pattern="array_oob_constant",
                meta={"base": base, "size": size, "idx": idx},
            ))
        self.generic_visit(node)

    def _check_off_by_one(self, node):
        cond = node.cond
        if not isinstance(cond, c_ast.BinaryOp) or cond.op not in ("<=",):
            return
        # 循环变量 i <= 常量 N，且循环体内有 arr[i]
        if not isinstance(cond.left, c_ast.ID):
            return
        loop_var = cond.left.name
        limit = _const_int(cond.right)
        if limit is None:
            return
        # 找循环体内 arr[loop_var] 访问
        loop_body = node.stmt

        class _Find(c_ast.NodeVisitor):
            def __init__(self):
                self.hit = None

            def visit_ArrayRef(self, n):
                if isinstance(n.subscript, c_ast.ID) and n.subscript.name == loop_var:
                    self.hit = _array_base_name(n)

        f = _Find()
        f.visit(loop_body)
        if f.hit and limit > 0:
            arr = f.hit
            arr_size = self.array_sizes.get(arr)
            if arr_size is not None and arr_size <= limit:
                self.diags.append(_make_diag(
                    category="boundary",
                    title="循环边界差一（Off-by-one）导致越界",
                    message="循环条件是 %s <= %d，当 %s 等于 %d 时仍会执行循环体，"
                            "而数组 %s 只有 %d 个元素（最大下标 %d），此时 %s[%d] 越界。"
                            % (loop_var, limit, loop_var, limit, arr, arr_size, arr_size - 1, arr, limit),
                    line=_coord_line(cond),
                    knowledge_point="循环",
                    fix_hint="把 <= 改成 <；或把循环范围从 0 开始改为 1 开始；或扩大数组长度。",
                    confidence="high",
                    pattern="array_oob_loop",
                    meta={"loop_var": loop_var, "limit": limit, "arr": arr},
                ))

    # ---- 数组初始化元素过多 ----
    def visit_Decl(self, node):
        if isinstance(node.type, c_ast.ArrayDecl):
            size = _const_int(node.type.dim)
            if size is not None and isinstance(node.init, c_ast.InitList):
                if len(node.init.exprs) > size:
                    self.diags.append(_make_diag(
                        category="boundary",
                        title="初始化列表超长",
                        message="数组 %s 声明了 %d 个元素，但初始化时提供了 %d 个值，"
                                "多出的值会导致编译报错或越界写入。"
                                % (node.name, size, len(node.init.exprs)),
                        line=_coord_line(node),
                        knowledge_point="数组",
                        fix_hint="删掉多余的初始化值，或增大数组长度以容纳所有初值。",
                        confidence="high",
                        pattern="array_init_too_many",
                    ))
        if isinstance(node.type, c_ast.TypeDecl):
            tname = getattr(node.type.type, "names", None)
            if tname and tname[0] in ("float", "double"):
                self.float_vars.add(node.name)
        self.generic_visit(node)

    # ---- switch 缺少 break ----
    def visit_Switch(self, node):
        cases = self._collect_cases(node.stmt)
        for i, case in enumerate(cases):
            if i == len(cases) - 1:
                continue
            if not self._case_has_break(case):
                self.diags.append(_make_diag(
                    category="logic",
                    title="switch 缺少 break 导致贯穿（fall-through）",
                    message="某个 case 分支末尾缺少 break，程序会继续执行下一个 case 的代码，"
                            "这可能不是你的本意，容易产生难以发现的逻辑 bug。",
                    line=_coord_line(case),
                    knowledge_point="函数",
                    fix_hint="在每个 case 分支末尾加上 break;（除非你确实需要贯穿效果）。",
                    confidence="medium",
                    pattern="switch_fallthrough",
                ))

    def _collect_cases(self, node):
        cases = []

        class _C(c_ast.NodeVisitor):
            def visit_Case(self, n):
                cases.append(n)

            def visit_Default(self, n):
                cases.append(n)

        _C().visit(node)
        return cases

    def _case_has_break(self, case):
        found = {"v": False}

        class _B(c_ast.NodeVisitor):
            def visit_Break(self, n):
                found["v"] = True
            # 内层 switch / 循环的 break 不算本 case 的跳出，做粗略处理即可

        _B().visit(case)
        return found["v"]

    # ---- scanf 缺少取地址符 & ----
    def visit_FuncCall(self, node):
        name = node.name.name if isinstance(node.name, c_ast.ID) else ""
        if name in ("scanf", "fscanf", "sscanf"):
            self._check_scanf_address(node)
        self.generic_visit(node)

    def _check_scanf_address(self, node):
        args = node.args.exprs if node.args is not None else []
        if not args or not _is_string(args[0]):
            return
        fmt = args[0].value
        # 统计需要写入地址的格式符数量（去掉 %% ）
        fmt_plain = fmt.replace("%%", "")
        conv_count = len(re.findall(r"%[-+0-9.*hlLjzt]*[diuoxXfFeEgGaAcspn]", fmt_plain))
        targets = args[1:]
        if conv_count == 0:
            return
        # 只检查传入的普通变量（缺少 &）
        for t in targets:
            if isinstance(t, c_ast.ID):
                self.diags.append(_make_diag(
                    category="logic",
                    title="scanf 缺少取地址符 &",
                    message="scanf 需要把读到的值写到变量所在的内存里，因此必须传入变量的地址。"
                            "写 %s 传的是变量的值（而不是地址），scanf 会把它当成地址，"
                            "导致崩溃或写入错误内存。" % t.name,
                    line=_coord_line(t),
                    knowledge_point="指针",
                    fix_hint="在变量前加上 & 取地址，例如 scanf(\"%d\", &%s);。" % t.name,
                    confidence="high",
                    pattern="scanf_missing_address",
                    meta={"var": t.name},
                ))
        # 参数个数不足
        if len(targets) < conv_count:
            self.diags.append(_make_diag(
                category="logic",
                title="scanf 参数个数不匹配",
                message="格式串里有 %d 个格式符，但只提供了 %d 个接收变量，参数不够。"
                        % (conv_count, len(targets)),
                line=_coord_line(node),
                knowledge_point="函数",
                fix_hint="为每个格式符都提供一个对应的接收变量（注意类型匹配）。",
                confidence="high",
                pattern="scanf_arg_mismatch",
            ))

    # ---- 空指针解引用 ----
    def visit_UnaryOp(self, node):
        if node.op == "*" and isinstance(node.expr, c_ast.ID):
            self.deref_pointers[node.expr.name] = _coord_line(node)
        self.generic_visit(node)

    def visit_StructRef(self, node):
        if node.type == "->" and isinstance(node.name, c_ast.ID):
            self.deref_pointers[node.name.name] = _coord_line(node)
        self.generic_visit(node)

    def flush_pointer_diags(self):
        for name, line in self.deref_pointers.items():
            if name not in self.nullchecked:
                self.diags.append(_make_diag(
                    category="logic",
                    title="可能空指针解引用",
                    message="对指针 %s 解引用前没有看到对它的判空（%s == NULL）检查，"
                            "如果 %s 为空指针，解引用会触发段错误（Segmentation Fault）。"
                            % (name, name, name),
                    line=line,
                    knowledge_point="指针",
                    fix_hint="解引用前先判断 if (%s != NULL)，或保证调用方不会传入空指针。" % name,
                    confidence="medium",
                    pattern="null_pointer_deref",
                ))


# ---------------------------------------------------------------------------
# 未初始化变量检测（函数级，启发式）
# ---------------------------------------------------------------------------

class _UninitializedVisitor(c_ast.NodeVisitor):

    def __init__(self):
        self.diags = []
        self.in_func = False
        self.candidates = set()
        self.assigned = set()
        self.reported = set()
        self.decl_lines = {}

    def visit_FuncDef(self, node):
        old_in = self.in_func
        old_candidates = self.candidates
        old_assigned = self.assigned
        old_reported = self.reported
        old_decl_lines = self.decl_lines

        self.in_func = True
        self.candidates = set()
        self.assigned = set()
        self.reported = set()
        self.decl_lines = {}
        for p in _func_params(node):
            self.assigned.add(p)

        self.generic_visit(node)
        self.in_func = old_in
        self.candidates = old_candidates
        self.assigned = old_assigned
        self.reported = old_reported
        self.decl_lines = old_decl_lines

    def visit_Decl(self, node):
        if self.in_func and not isinstance(node.type, (c_ast.ArrayDecl, c_ast.FuncDecl)):
            if node.init is None:
                self.candidates.add(node.name)
                self.decl_lines[node.name] = _coord_line(node)
            else:
                self.assigned.add(node.name)
        self.generic_visit(node)

    def visit_Assignment(self, node):
        self._mark_assigned(node.lvalue)
        self.generic_visit(node)

    def visit_FuncCall(self, node):
        name = node.name.name if isinstance(node.name, c_ast.ID) else ""
        if name in ("scanf", "sscanf", "fscanf") and node.args is not None:
            for arg in node.args.exprs[1:]:
                if isinstance(arg, c_ast.UnaryOp) and arg.op == "&" and isinstance(arg.expr, c_ast.ID):
                    self.assigned.add(arg.expr.name)
        self.generic_visit(node)

    def visit_ID(self, node):
        if self.in_func and node.name in self.candidates \
                and node.name not in self.assigned \
                and node.name not in self.reported:
            self.reported.add(node.name)
            self.diags.append(_make_diag(
                category="logic",
                title="变量可能未初始化就被使用",
                message="局部变量 %s 声明后没有赋初值，就在判断/计算中被使用，"
                        "它的值是内存里的随机垃圾值，结果不可预测。" % node.name,
                line=_coord_line(node),
                knowledge_point="函数",
                fix_hint="声明时就初始化，例如 int %s = 0;（或用 scanf/赋值先写入再使用）。" % node.name,
                confidence="medium",
                pattern="uninitialized_variable",
                meta={"var": node.name, "decl_line": self.decl_lines.get(node.name)},
            ))

    def _mark_assigned(self, lvalue):
        if isinstance(lvalue, c_ast.ID):
            self.assigned.add(lvalue.name)
        elif isinstance(lvalue, c_ast.ArrayRef):
            self._mark_assigned(lvalue.name)


def _func_params(node):
    """提取函数形参名列表。"""
    params = []
    decl = node.decl
    if decl and isinstance(decl.type, c_ast.FuncDecl):
        args = decl.type.args
        if args is not None:
            items = getattr(args, "params", None) or getattr(args, "exprs", []) or []
            for p in items:
                if isinstance(p, c_ast.Decl):
                    params.append(p.name)
                elif isinstance(p, c_ast.ID):
                    params.append(p.name)
    return params


# ---------------------------------------------------------------------------
# 诊断引擎入口
# ---------------------------------------------------------------------------

class DiagnosticEngine:
    """统一诊断入口：语法 + 规则。"""

    def run(self, source, parse_result):
        diags = []

        if not parse_result.get("ok"):
            diags.append(_make_diag(
                category="syntax",
                title="语法错误",
                message=parse_result.get("message") or "代码无法通过语法解析。",
                line=parse_result.get("line"),
                knowledge_point="语法",
                fix_hint="根据错误提示修正语法（如缺少分号、括号不匹配、类型错误等）。",
                confidence="high",
                pattern="syntax_error",
            ))
            return diags

        ast = parse_result.get("ast")
        if ast is None:
            return diags

        try:
            collector = _ArraySizeCollector()
            collector.visit(ast)

            rule_visitor = _RuleVisitor(collector.sizes)
            rule_visitor.visit(ast)
            rule_visitor.flush_pointer_diags()
            diags.extend(rule_visitor.diags)

            uninit = _UninitializedVisitor()
            try:
                uninit.visit(ast)
                diags.extend(uninit.diags)
            except Exception:
                pass  # 未初始化检测失败不影响整体结果
        except Exception as exc:
            diags.append(_make_diag(
                category="other",
                title="诊断过程异常",
                message="分析规则执行时发生异常：%s" % exc,
                line=None,
                knowledge_point="其他",
                confidence="medium",
                pattern="internal_error",
            ))
        return diags


# 便捷入口：对源码做完整诊断
def diagnose(source):
    """对源码做完整诊断，返回 (parse_result, diags)。"""
    from . import c_parser
    parse_result = c_parser.parse_c(source)
    engine = DiagnosticEngine()
    diags = engine.run(source, parse_result)
    return parse_result, diags