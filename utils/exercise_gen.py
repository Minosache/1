"""同类巩固习题生成。

根据当前 bug 的知识点，从内置题库挑选 2~3 道同类型练习题，每道题包含：
- title      题目名称
- desc       题目描述（要求）
- framework  代码框架（留空/待补充）
- answer     参考答案
- hint       提示

配置了 LLM 时可选调用 LLM 生成，失败自动降级为内置题库。
"""


import json

from . import llm

# 内置题库：知识点 -> 题目列表
EXERCISES = {
    "指针": [
        {
            "title": "用指针交换两个整数",
            "desc": "编写函数 swap(int *a, int *b)，交换 a、b 所指向的两个整数，并在 main 中调用验证。",
            "framework": "void swap(int *a, int *b) {\n    int t = *a;\n    /* 补齐交换逻辑 */\n    ______\n}",
            "answer": "void swap(int *a, int *b) {\n    int t = *a;\n    *a = *b;\n    *b = t;\n}",
            "hint": "交换的是指针指向的内容（*a、*b），而不是指针本身。",
        },
        {
            "title": "用指针遍历数组求和",
            "desc": "给定 int arr[5] = {1,2,3,4,5}; 用指针 p 从 arr 开始遍历，计算所有元素之和。",
            "framework": "int sum = 0;\nint *p = arr;\nfor (int i = 0; i < 5; i++) {\n    sum += ______;\n}",
            "answer": "int sum = 0;\nint *p = arr;\nfor (int i = 0; i < 5; i++) {\n    sum += *(p + i);\n}",
            "hint": "*(p+i) 等价于 arr[i]，指针偏移 i 个单位再解引用。",
        },
        {
            "title": "空指针判断",
            "desc": "编写函数 print_len(char *s)，打印字符串长度，但 s 为 NULL 时不能崩溃，应直接返回。",
            "framework": "void print_len(char *s) {\n    if (______) return;\n    printf(\"%d\\n\", strlen(s));\n}",
            "answer": "void print_len(char *s) {\n    if (s == NULL) return;\n    printf(\"%d\\n\", strlen(s));\n}",
            "hint": "解引用前先判空，是防止段错误的关键习惯。",
        },
    ],
    "循环": [
        {
            "title": "计算 1 到 n 的累加和",
            "desc": "输入正整数 n，用循环计算 1+2+...+n 并输出。",
            "framework": "int sum = 0;\nfor (int i = 1; i ______ n; i++) {\n    sum += i;\n}",
            "answer": "int sum = 0;\nfor (int i = 1; i <= n; i++) {\n    sum += i;\n}",
            "hint": "包含 n 本身要用 <=；若写成 < 会漏掉最后一项。",
        },
        {
            "title": "打印 n 以内所有偶数",
            "desc": "输入 n，从小到大打印 [2, n] 范围内的所有偶数。",
            "framework": "for (int i = 2; i <= n; i += ______) {\n    printf(\"%d \", i);\n}",
            "answer": "for (int i = 2; i <= n; i += 2) {\n    printf(\"%d \", i);\n}",
            "hint": "步长设为 2 可以只落在偶数上，避免每个数都判断。",
        },
        {
            "title": "死循环排查",
            "desc": "下面的循环会陷入死循环，找出问题并修改，使程序能正常结束。",
            "framework": "int i = 0;\nwhile (i < 10) {\n    printf(\"%d\", i);\n    /* 缺少什么？ */\n}",
            "answer": "int i = 0;\nwhile (i < 10) {\n    printf(\"%d\", i);\n    i++;\n}",
            "hint": "循环变量必须在循环体内被更新，否则条件永远不变。",
        },
    ],
    "数组": [
        {
            "title": "数组逆序输出",
            "desc": "输入 5 个整数存入数组，按逆序输出。",
            "framework": "int a[5];\nfor (int i = 0; i < 5; i++) scanf(\"%d\", ______);\nfor (int i = ______; i >= 0; i--) printf(\"%d \", a[i]);",
            "answer": "int a[5];\nfor (int i = 0; i < 5; i++) scanf(\"%d\", &a[i]);\nfor (int i = 4; i >= 0; i--) printf(\"%d \", a[i]);",
            "hint": "数组要传地址 &a[i]；逆序从下标 4 到 0。",
        },
        {
            "title": "找数组最大值",
            "desc": "输入 n 个数，找出其中的最大值并输出。注意区分错误的 max 初值写法。",
            "framework": "int max = a[0];\nfor (int i = 1; i < n; i++) {\n    if (a[i] ______ max) max = a[i];\n}",
            "answer": "int max = a[0];\nfor (int i = 1; i < n; i++) {\n    if (a[i] > max) max = a[i];\n}",
            "hint": "max 应初始化为第一个元素，而不是 0（避免全负数时出错）。",
        },
        {
            "title": "数组越界检查",
            "desc": "下面代码访问了越界元素，指出问题行并修正。",
            "framework": "int a[10];\nfor (int i = 0; i ______ 10; i++) a[i] = i;\nprintf(\"%d\", a[10]);",
            "answer": "int a[10];\nfor (int i = 0; i < 10; i++) a[i] = i;\nprintf(\"%d\", a[9]);",
            "hint": "下标范围是 0~9，a[10] 是第 11 个元素。",
        },
    ],
    "结构体": [
        {
            "title": "学生结构体求平均分",
            "desc": "定义结构体 Student{name, score[3]}，输入一名学生的 3 门成绩，输出平均分。",
            "framework": "struct Student { char name[20]; int score[3]; };\nstruct Student s;\nfloat avg = (s.score[0] + s.score[1] + s.score[2]) / ______;\nprintf(\"%.2f\", avg);",
            "answer": "struct Student { char name[20]; int score[3]; };\nstruct Student s;\nfloat avg = (s.score[0] + s.score[1] + s.score[2]) / 3.0;\nprintf(\"%.2f\", avg);",
            "hint": "除以 3.0 会做浮点除法；除以 3 会先整数截断。",
        },
        {
            "title": "结构体指针访问成员",
            "desc": "用结构体指针 p 访问成员，完成对 age 的加 1 操作。",
            "framework": "struct Person { int age; };\nstruct Person x = {20};\nstruct Person *p = &x;\np______age += 1;",
            "answer": "struct Person { int age; };\nstruct Person x = {20};\nstruct Person *p = &x;\np->age += 1;",
            "hint": "指针访问成员用 ->（等价于 (*p).age）。",
        },
    ],
    "字符串": [
        {
            "title": "手写字符串长度",
            "desc": "不使用 strlen，遍历字符数组直到遇到 '\\0'，统计字符串长度。",
            "framework": "int len = 0;\nwhile (s[len] ______ '\\0') {\n    len++;\n}",
            "answer": "int len = 0;\nwhile (s[len] != '\\0') {\n    len++;\n}",
            "hint": "C 字符串以 '\\0'（空字符）结尾，遇到它即结束。",
        },
        {
            "title": "判断字符串内容相等",
            "desc": "判断两个字符串是否内容相同（不能用 ==），用标准库函数实现。",
            "framework": "if (______(s1, s2) == 0) {\n    printf(\"相同\");\n}",
            "answer": "if (strcmp(s1, s2) == 0) {\n    printf(\"相同\");\n}",
            "hint": "strcmp 返回 0 表示内容相等，需 #include <string.h>。",
        },
        {
            "title": "反转字符串",
            "desc": "将字符串原地反转（首尾对调），例如 \"abc\" -> \"cba\"。",
            "framework": "int n = strlen(s);\nfor (int i = 0; i < n / 2; i++) {\n    char tmp = s[i];\n    s[i] = s[______];\n    s[______] = tmp;\n}",
            "answer": "int n = strlen(s);\nfor (int i = 0; i < n / 2; i++) {\n    char tmp = s[i];\n    s[i] = s[n - 1 - i];\n    s[n - 1 - i] = tmp;\n}",
            "hint": "第 i 个字符与第 n-1-i 个字符交换，只交换前一半。",
        },
    ],
    "运算符": [
        {
            "title": "判断奇偶",
            "desc": "输入整数，用位运算或取模判断它是否为偶数。",
            "framework": "if (n ______ 2 == 0) printf(\"偶数\");",
            "answer": "if (n % 2 == 0) printf(\"偶数\");",
            "hint": "% 是取余运算符，n%2 结果为 0 说明是偶数。",
        },
        {
            "title": "= 与 == 陷阱",
            "desc": "修正下面的错误：判断 a 是否等于 b。",
            "framework": "if (a ______ b) {\n    printf(\"相等\");\n}",
            "answer": "if (a == b) {\n    printf(\"相等\");\n}",
            "hint": "一个等号是赋值，两个等号才是比较。",
        },
        {
            "title": "运算符优先级",
            "desc": "读懂表达式 y = x * 2 + (x % 3)，当 x=5 时 y 的值是？并用 printf 验证。",
            "framework": "int x = 5;\nint y = x * 2 + (x % 3);\n// y 的值应为 ______",
            "answer": "int x = 5;\nint y = x * 2 + (x % 3);\n// y 的值应为 12（10 + 2）",
            "hint": "*、% 优先级高于 +，先算 5*2=10 和 5%3=2，再相加。",
        },
    ],
    "函数": [
        {
            "title": "递归求阶乘",
            "desc": "编写递归函数 fac(n)，返回 n 的阶乘，注意边界条件 n==0 返回 1。",
            "framework": "int fac(int n) {\n    if (n == 0) return 1;\n    return ______;\n}",
            "answer": "int fac(int n) {\n    if (n == 0) return 1;\n    return n * fac(n - 1);\n}",
            "hint": "递归必须要有终止条件（base case），否则无限递归导致栈溢出。",
        },
        {
            "title": "函数返回值",
            "desc": "编写函数 max2(a,b) 返回两者较大值，并说明 void 函数无法用 return 返回值的原因。",
            "framework": "int max2(int a, int b) {\n    return a > b ? ______ : ______;\n}",
            "answer": "int max2(int a, int b) {\n    return a > b ? a : b;\n}",
            "hint": "三元运算符 ? : 是简洁的 if-else。",
        },
        {
            "title": "值传递还是地址传递",
            "desc": "下面的 swap1 不能交换 main 里的 a、b，请改为正确的指针版本。",
            "framework": "void swap1(int x, int y) { int t = x; x = y; y = t; }\n// 为什么调用 swap1(a, b) 后 a、b 不变？改用 ______ 传递。",
            "answer": "void swap1(int x, int y) { int t = x; x = y; y = t; }\n// C 语言函数参数是值传递，调用 swap1(a, b) 只改了形参副本；\n// 需要改为指针传递：void swap2(int *x, int *y){ int t=*x; *x=*y; *y=t; }",
            "hint": "C 函数参数默认按值复制，要修改实参必须传地址（指针）。",
        },
    ],
    "语法": [
        {
            "title": "补全缺失的分号",
            "desc": "下面的代码缺少分号导致编译错误，补全它。",
            "framework": "int a = 10    \nprintf(\"%d\", a);",
            "answer": "int a = 10;\nprintf(\"%d\", a);",
            "hint": "C 语句以分号 ; 结尾。",
        },
        {
            "title": "括号匹配",
            "desc": "指出下列代码中括号不匹配的地方并修正。",
            "framework": "if (a > b) {\n    printf(\"a 更大\");\n",
            "answer": "if (a > b) {\n    printf(\"a 更大\");\n}\n",
            "hint": "每个 { 都要有对应的 }。",
        },
        {
            "title": "变量先声明后使用",
            "desc": "下述代码在 C89 中会报错，调整使它在更严格标准下也通过。",
            "framework": "printf(\"%d\", x);\nint x = 5;",
            "answer": "int x = 5;\nprintf(\"%d\", x);",
            "hint": "变量必须在使用之前声明并赋值。",
        },
    ],
    "内存": [
        {
            "title": "malloc 与 free 配对",
            "desc": "用 malloc 申请一个 int 数组并释放，补齐缺失的 free 调用。",
            "framework": "int *p = (int*)malloc(10 * sizeof(int));\n/* 使用 p ... */\n______(p);\np = NULL;",
            "answer": "int *p = (int*)malloc(10 * sizeof(int));\n/* 使用 p ... */\nfree(p);\np = NULL;",
            "hint": "malloc 的内存用完必须 free，否则内存泄漏；释放后把指针置 NULL 更安全。",
        },
        {
            "title": "识别内存泄漏",
            "desc": "下面循环里反复 malloc 却从不 free，会导致什么问题？请补充修复。",
            "framework": "for (int i = 0; i < 100; i++) {\n    int *p = malloc(1024);\n    /* ______ 缺少什么 */\n}",
            "answer": "for (int i = 0; i < 100; i++) {\n    int *p = malloc(1024);\n    /* 使用 p */\n    free(p);\n}",
            "hint": "每次循环都申请内存而不释放，可用内存会不断减少，称为内存泄漏。",
        },
    ],
    "其他": [
        {
            "title": "switch 补全 break",
            "desc": "下面 switch 缺少 break，导致结果错误，请补全。",
            "framework": "switch (x) {\n    case 1: printf(\"one\"); ______\n    case 2: printf(\"two\"); break;\n    default: printf(\"other\");\n}",
            "answer": "switch (x) {\n    case 1: printf(\"one\"); break;\n    case 2: printf(\"two\"); break;\n    default: printf(\"other\");\n}",
            "hint": "每个 case 末尾加 break 防止贯穿执行下一个分支。",
        },
        {
            "title": "整数除法与类型转换",
            "desc": "计算 5 / 2 的浮点结果，指出整数除法的小数截断问题并改正。",
            "framework": "float r = 5 / 2;   // r 实际是 ______\nfloat r2 = 5.0 / 2; // 正确写法",
            "answer": "float r = 5 / 2;   // r 实际是 2.0（先整数相除得 2，再转 float）\nfloat r2 = 5.0 / 2; // 正确，得到 2.5",
            "hint": "两个整数相除得整数，要得到浮点结果需至少一个操作数是浮点。",
        },
    ],
}

_DEFAULT_POINTS = ["指针", "循环", "数组"]


def generate_exercises(knowledge_point, count=3, use_llm=False):
    """根据知识点生成 count 道练习题。"""
    count = max(1, min(count, 3))  # 限制 1~3 道
    pool = EXERCISES.get(knowledge_point, [])
    # 不足时从其它知识点补齐
    if len(pool) < count:
        for kp in _DEFAULT_POINTS:
            if kp != knowledge_point:
                for ex in EXERCISES.get(kp, []):
                    if ex not in pool:
                        pool.append(ex)
                    if len(pool) >= count:
                        break
            if len(pool) >= count:
                break
    if not pool:
        pool = EXERCISES.get("数组", [])

    chosen = pool[:count]

    if use_llm and llm.llm_available():
        prompt = (
            "请围绕 C 语言知识点“%s”生成 %d 道巩固练习题。用 JSON 数组返回，每项字段为 "
            "title/desc/framework/answer/hint（代码用字符串，含换行）。不要输出其它内容。"
            % (knowledge_point, count)
        )
        result = llm._try_llm([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=1500)
        if result:
            parsed = _parse_llm_exercises(result)
            if parsed:
                return parsed

    return chosen


def _parse_llm_exercises(text):
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
                        "title": str(d.get("title", "")),
                        "desc": str(d.get("desc", "")),
                        "framework": str(d.get("framework", "")),
                        "answer": str(d.get("answer", "")),
                        "hint": str(d.get("hint", "")),
                    })
            return out
    except Exception:
        pass
    return []


def list_knowledge_points():
    """返回带题目数量的知识点列表。"""
    return [(kp, len(v)) for kp, v in EXERCISES.items()]
