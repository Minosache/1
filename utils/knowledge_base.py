"""错题知识库（本地轻量向量检索，无需 GPU）。

实现要点：
- 记录以 JSON 文件持久化（data/knowledge/mistakes.json），完全本地；
- 检索使用 TF-IDF（字 / 词粒度） + 余弦相似度，数据量小，检索时实时构建索引即可；
- 支持按知识点分类统计、按知识点过滤、关键词高亮。

知识点（与诊断模块一致）：语法 / 指针 / 循环 / 数组 / 结构体 / 字符串 /
运算符 / 函数 / 内存 / 其他。
"""

import json
import math
import os
import re
import threading
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "knowledge")
DB_PATH = os.path.join(DATA_DIR, "mistakes.json")

_ELEMENT_RE = re.compile(r"[a-z0-9_]+")
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 分词 + TF-IDF 向量化
# ---------------------------------------------------------------------------

def tokenize(text):
    """把文本切分为特征 token：英文/数字词 + 中文单字与相邻双字。"""
    text = (text or "").lower()
    tokens = []
    tokens.extend(_ELEMENT_RE.findall(text))
    chinese = _CHINESE_RE.findall(text)
    for i, ch in enumerate(chinese):
        tokens.append(ch)
        if i + 1 < len(chinese):
            tokens.append(ch + chinese[i + 1])
    return tokens


class _TfidfIndex:
    """基于内存的 TF-IDF 向量化器。"""

    def __init__(self, documents):
        self.vocab = {}
        ordered = []
        for tokens in documents:
            for t in set(tokens):
                self.vocab[t] = self.vocab.get(t, 0) + 1
            ordered.append(self._tf(tokens))
        n = len(documents) or 1
        self.idf = {}
        for term, df in self.vocab.items():
            self.idf[term] = math.log((1 + n) / (1 + df)) + 1.0
        self.doc_vectors = [self._tfidf(vec) for vec in ordered]

    def _tf(self, tokens):
        d = {}
        for t in tokens:
            d[t] = d.get(t, 0) + 1
        return d

    def _tfidf(self, tf):
        vec = {}
        for t, c in tf.items():
            if t in self.idf:
                vec[t] = c * self.idf[t]
        return vec

    def query_vector(self, tokens):
        tf = self._tf(tokens)
        return self._tfidf(tf)

    def cosine(self, q):
        qnorm = math.sqrt(sum(v * v for v in q.values())) or 1.0
        sims = []
        for i, dv in enumerate(self.doc_vectors):
            dot = 0.0
            dnorm = math.sqrt(sum(v * v for v in dv.values())) or 1.0
            for t, qv in q.items():
                if t in dv:
                    dot += qv * dv[t]
            sims.append(dot / (qnorm * dnorm) if dnorm else 0.0)
        return sims


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """错题知识库：增、查、分类统计。"""

    def __init__(self, path=None):
        self.path = path or DB_PATH

    # ---- 存储 ----
    def _load(self):
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def _save(self, records):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def add(self, record):
        """新增一条错题。record 使用默认字段补齐。返回完整记录。"""
        with _LOCK:
            records = self._load()
            item = {
                "id": uuid.uuid4().hex[:12],
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "title": record.get("title", ""),
                "code": record.get("code", ""),
                "error_type": record.get("error_type", "logic"),
                "knowledge_point": record.get("knowledge_point", "其他"),
                "explanation": record.get("explanation", ""),
                "fixed_code": record.get("fixed_code", ""),
                "line": record.get("line"),
            }
            records.append(item)
            self._save(records)
            return item

    def all(self):
        return self._load()

    def clear(self):
        """清空错题本。"""
        with _LOCK:
            self._save([])

    def delete(self, record_id):
        """按 id 删除一条记录，返回是否成功。"""
        with _LOCK:
            records = self._load()
            new_records = [r for r in records if r.get("id") != record_id]
            if len(new_records) == len(records):
                return False
            self._save(new_records)
            return True

    # ---- 检索 ----
    def _doc_text(self, record):
        return " ".join([
            record.get("title", ""),
            record.get("knowledge_point", ""),
            _zh_error_type(record.get("error_type", "")),
            record.get("explanation", ""),
            record.get("code", ""),
        ])

    def search(self, query, top_k=5, knowledge_point=None):
        """检索最相关错题。knowledge_point 可过滤分类。返回 [{record, score, highlight}]。"""
        records = self._load()
        if knowledge_point:
            records = [r for r in records if r.get("knowledge_point") == knowledge_point]
        if not records:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            ranked = list(records)
            scores = [0.0] * len(records)
        else:
            docs = [tokenize(self._doc_text(r)) for r in records]
            index = _TfidfIndex(docs)
            qv = index.query_vector(query_tokens)
            scores = index.cosine(qv)
            pairs = sorted(zip(records, scores), key=lambda x: x[1], reverse=True)
            records = [p[0] for p in pairs]
            scores = [p[1] for p in pairs]

        results = []
        for i, r in enumerate(records[:top_k]):
            results.append({
                "record": r,
                "score": round(float(scores[i]), 4) if i < len(scores) else 0.0,
                "highlight": self._highlight(self._doc_text(r), query),
            })
        return results

    def _highlight(self, text, query):
        """对检索命中的关键词做 markdown 高亮。"""
        if not query:
            return text
        terms = sorted(set(tokenize(query)), key=len, reverse=True)
        for t in terms:
            if len(t) >= 1:
                text = re.sub(
                    "(%s)" % re.escape(t),
                    r"**\1**",
                    text,
                    flags=re.IGNORECASE,
                )
        return text

    def categories(self):
        """按知识点分类统计，返回 [(知识点, 数量), ...]，按数量降序。"""
        records = self._load()
        counter = {}
        for r in records:
            kp = r.get("knowledge_point", "其他")
            counter[kp] = counter.get(kp, 0) + 1
        return sorted(counter.items(), key=lambda x: x[1], reverse=True)


def _zh_error_type(etype):
    return {"syntax": "语法错误", "logic": "逻辑错误", "boundary": "边界用例错误"}.get(etype, etype)


_KB_INSTANCE = None


def get_kb():
    """知识库单例。"""
    global _KB_INSTANCE
    if _KB_INSTANCE is None:
        _KB_INSTANCE = KnowledgeBase()
    return _KB_INSTANCE
