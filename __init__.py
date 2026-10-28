"""C语言智能排错与学习助手 —— 工具包。

本包内各模块职责：
- c_parser      : 基于 pycparser 的 C 语法解析
- diagnostic    : 语法/逻辑/边界错误诊断引擎
- llm           : LLM 调用接口 + 本地规则讲解引擎（无 LLM 时自动降级）
- testcase_gen  : 自动生成测试用例
- exercise_gen  : 同类巩固习题生成
- knowledge_base: 错题知识库（本地轻量向量检索，无需 GPU）
- diff_utils    : 代码差异对比与高亮
"""

__all__ = [
    "c_parser",
    "diagnostic",
    "llm",
    "testcase_gen",
    "exercise_gen",
    "knowledge_base",
    "diff_utils",
]