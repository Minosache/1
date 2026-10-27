# 🐞 C 语言代码智能排错与学习助手

面向 **华为开发者大赛 · 方向⑤ 代码智能开发辅助赛道** 的 C 语言智能排错与学习 Demo。
全部使用 Python，前端采用 Streamlit 网页，默认**纯本地运行**（无需 GPU、无需云资源），
可选接入 LLM 增强讲解与修复效果。

---

## ✨ 功能特性

| 模块 | 说明 |
| --- | --- |
| 📝 输入区 | 粘贴 C 源码 / 粘贴 GCC 报错日志 / 上传 `.c` 文件 |
| 🔍 智能诊断 | 基于 pycparser 解析语法错误，基于规则识别逻辑错误、边界用例错误，并定位行号 |
| 🧠 错误讲解 | 用大学生易懂的语言解释 bug 的底层原理（本地规则模板 + 可选 LLM） |
| 🛠️ 代码修复 | 输出修复后的完整 C 代码，逐行对比原代码并标注修改位置 |
| 🧪 测试用例 | 自动生成正常 / 边界 / 异常输入用例与预期输出 |
| 📖 同类习题 | 根据错题知识点生成 2~3 道巩固练习题（题目 + 框架 + 答案 + 提示） |
| 📚 错题知识库 | 自动保存历史错题，本地 TF-IDF 向量检索，按知识点（指针/循环/数组等）分类 |

---

## 📁 项目结构

```
c_debug_assistant/
├── main.py                  # Streamlit 主界面（整合全部模块）
├── requirements.txt         # 依赖清单
├── README.md                # 本文档
├── .streamlit/
│   └── config.toml          # 页面主题与运行配置
├── utils/
│   ├── __init__.py          # 工具包定义
│   ├── c_parser.py          # pycparser C 语法解析
│   ├── diagnostic.py        # 语法/逻辑/边界错误诊断引擎 + GCC 日志解析
│   ├── llm.py               # LLM 调用接口 + 本地规则讲解/修复引擎
│   ├── testcase_gen.py      # 测试用例自动生成
│   ├── exercise_gen.py      # 同类巩固习题生成（内置题库）
│   ├── knowledge_base.py    # 错题知识库（本地 TF-IDF 向量检索）
│   └── diff_utils.py        # 代码差异对比与高亮
└── data/
    └── knowledge/           # 错题数据（运行时自动创建 mistakes.json）
```

---

## 🧩 技术组件

- **Python 3.9+**——运行环境
- **Streamlit**——前端网页界面
- **pycparser**——C 语言语法解析（生成 AST）
- **本地规则引擎**——默认完成讲解、修复、习题生成（完全离线）
- **可选 LLM**——设置 `LLM_API_KEY` 后调用 OpenAI 兼容接口增强效果
- **本地向量检索**——TF-IDF + 余弦相似度，无需 GPU

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.9 及以上（建议 3.10 / 3.11）
- 不需要 GPU、不需要额外云服务

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

（核心依赖仅 `streamlit` 和 `pycparser` 两个。）

### 3. 启动

```bash
streamlit run main.py
```

启动后浏览器会自动打开网页 Demo（默认地址 `http://localhost:8501`）。

### 一键安装并启动

```bash
pip install -r requirements.txt && streamlit run main.py
```

> Windows PowerShell 下请使用 `;` 代替 `&&`：
> ```powershell
> pip install -r requirements.txt; streamlit run main.py
> ```

---

## 🧪 快速体验

1. 打开网页后，点击左侧 **「载入示例代码」**；
2. 点击 **「🚀 提交分析」**；
3. 中间面板会高亮出错行、逐条讲解 bug 原理并给出修复代码；
4. 右侧面板自动生成测试用例、同类习题，并可检索历史错题；
5. 点击 **「💾 保存到错题本」**，即可在右侧「历史错题检索」中按知识点检索。

---

## 🔌 可选：接入 LLM（默认关闭，纯本地可运行）

设置环境变量后，讲解 / 修复 / 习题 / 测试用例会优先调用大模型，调用失败自动降级为本地规则。

**Windows PowerShell：**

```powershell
$env:LLM_API_KEY = "你的密钥"
$env:LLM_BASE_URL = "https://api.openai.com/v1"   # 或你的兼容接口
$env:LLM_MODEL = "gpt-3.5-turbo"
streamlit run main.py
```

**Linux / macOS：**

```bash
export LLM_API_KEY="你的密钥"
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_MODEL="gpt-3.5-turbo"
streamlit run main.py
```

> 不设置 `LLM_API_KEY` 时，程序完全离线运行，所有功能仍可用。

---

## 🧠 诊断规则说明

内置诊断引擎覆盖以下常见 C 语言错误（含行号定位与知识点分类）：

- 语法错误：pycparser 解析失败 + GCC 日志解析
- 逻辑错误：除零、条件中 `=` 与 `==` 混淆、scanf 缺 `&`、字符串 `==` 比较、
  变量未初始化、空指针解引用、浮点判等、switch 缺少 break、死循环
- 边界用例错误：数组下标越界、循环 off-by-one、初始化列表超长等

---

## ⚠️ 说明

- 诊断基于静态分析，存在一定误报可能，置信度 `medium` 的结果建议人工确认；
- 本地规则修复覆盖部分确定性错误，其余给出明确修改建议（接入 LLM 后可更智能地修复）；
- 错题数据保存在本机 `data/knowledge/mistakes.json`，可随时清空。

---

## 📄 许可证

本项目仅用于学习与竞赛演示。