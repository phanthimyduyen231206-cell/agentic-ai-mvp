"""Chỉ mục và tìm kiếm tài liệu quy định của tiệm (RAG – phần retrieval).

Chủ file: Phương Anh (PA4). Giao ước G4 (docs/contracts.md Mục 14):
    build_index(policy_dir="data/policies") -> int            # số đoạn
    search(query: str, k: int = 3) -> list[{"doc", "title", "text", "score", "retriever"}]

- Mỗi file .md trong data/policies/ (bỏ file tên bắt đầu bằng "_") được cắt theo mục "## ".
  Một đoạn = tiêu đề file + tiêu đề mục + nội dung mục.
- Có GEMINI_API_KEY: embedding bằng GEMINI_EMBED_MODEL (RETRIEVAL_DOCUMENT cho đoạn,
  RETRIEVAL_QUERY cho câu hỏi), tìm bằng cosine, retriever = "embedding".
- Không có key hoặc lỗi mạng: BM25 tự viết (không thêm thư viện), retriever = "bm25".
  Câu hỏi có dấu so với chữ có dấu; câu hỏi không dấu so với chữ đã bỏ dấu.
- Đoạn dưới ngưỡng bị bỏ; query rỗng hoặc không đoạn nào qua ngưỡng thì trả [].
- Không ghi DB, không gọi LLM sinh văn bản. Chỉ mục lưu ở POLICY_INDEX_PATH (không commit).

Chạy bằng lệnh (đứng ở thư mục gốc repo):
    python -m services.policy_index --build
    python -m services.policy_index --query "kem tươi bảo quản thế nào"
    python -m services.policy_index --eval          # chấm 11 câu trong data/policies/_cau_hoi_mau.md
    thêm --bm25 để ép dùng BM25 (không gọi mạng)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_DIR = "data/policies"
DEFAULT_INDEX_PATH = "data/policy_index.json"
DEFAULT_EMBED_MODEL = "gemini-embedding-001"
SAMPLE_FILE = "_cau_hoi_mau.md"

# Ngưỡng, chọn bằng 11 câu trong _cau_hoi_mau.md (python -m services.policy_index --eval):
# 8 câu có trong tài liệu ra đúng file trong top 3, 3 câu không có trả rỗng.
MIN_SCORE_BM25 = 6.0         # điểm BM25 tối thiểu của một đoạn
MIN_COVERAGE_BM25 = 0.6      # và đoạn phải chứa ít nhất 60% từ khóa của câu hỏi
MIN_SCORE_EMBEDDING = 0.70   # cosine tối thiểu khi tìm bằng embedding

BM25_K1 = 1.5
BM25_B = 0.75
EMBED_BATCH = 100

# Từ hư, từ hỏi: không dùng làm từ khóa (dạng có dấu). Dạng không dấu suy ra bên dưới.
_STOPWORDS = set("""
thì là gì có không nào thế như và của cho được để khi ra sao bao nhiêu mấy những một các này đó kia
hay với ở trong từ đến hoặc phải cần quy định tiệm rồi lúc làm việc về tôi mình bạn em anh chị ạ nhé
theo vậy nên đã đang sẽ bị cái ai đâu hả chứ vẫn còn nữa thôi luôn hãy xin cho biết hỏi
""".split())
# Không bỏ "bao" ở dạng không dấu vì trùng "bảo" (bảo quản).
_STOPWORDS_FOLDED_KEEP = {"bao"}

# Viết tắt hay gặp -> cụm đầy đủ (áp dụng cho câu hỏi).
_ABBREV = {
    "hsd": "hạn dùng", "sl": "số lượng", "nl": "nguyên liệu", "bq": "bảo quản",
    "xk": "xuất kho", "nh": "nhận hàng", "dc": "được", "đc": "được", "ko": "không", "k": "không",
    "tg": "thời gian", "nvl": "nguyên liệu",
}

_INDEX_CACHE: dict | None = None


# ---------------------------------------------------------------------------
# Tách từ
# ---------------------------------------------------------------------------
def _fold(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'Kem tươi đặt' -> 'kem tuoi dat'."""
    text = text.lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


_STOPWORDS_FOLDED = {_fold(w) for w in _STOPWORDS} - _STOPWORDS_FOLDED_KEEP


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    return re.findall(r"[0-9a-zà-ỹđ]+", text)


def _has_diacritics(text: str) -> bool:
    low = unicodedata.normalize("NFC", text.lower())
    return _fold(low) != low


def _expand_abbrev(words: list[str]) -> list[str]:
    out: list[str] = []
    for w in words:
        out.extend(_ABBREV.get(w, w).split())
    return out


def _terms(words: list[str], folded: bool) -> list[str]:
    """Từ khóa = từ đơn không phải từ hư + cặp hai từ liền nhau (ít nhất 1 từ có nghĩa)."""
    if folded:
        words = [_fold(w) for w in words]
        stop = _STOPWORDS_FOLDED
    else:
        stop = _STOPWORDS
    uni = [w for w in words if w not in stop]
    bi = [f"{a}_{b}" for a, b in zip(words, words[1:]) if not (a in stop and b in stop)]
    return uni + bi


def _content_words(words: list[str], folded: bool) -> set[str]:
    if folded:
        return {_fold(w) for w in words} - _STOPWORDS_FOLDED
    return set(words) - _STOPWORDS


# ---------------------------------------------------------------------------
# Đọc tài liệu và cắt đoạn
# ---------------------------------------------------------------------------
def _resolve(path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _index_path() -> Path:
    return _resolve(os.environ.get("POLICY_INDEX_PATH", "").strip() or DEFAULT_INDEX_PATH)


def _policy_files(policy_dir) -> list[Path]:
    d = _resolve(policy_dir)
    return sorted(p for p in d.glob("*.md") if not p.name.startswith("_"))


def load_chunks(policy_dir=DEFAULT_POLICY_DIR) -> list[dict]:
    """Cắt các file .md thành đoạn: [{"doc", "title", "text"}]."""
    chunks = []
    for path in _policy_files(policy_dir):
        raw = path.read_text(encoding="utf-8")
        raw = re.sub(r"(?m)^Cập nhật:.*$", "", raw)
        m = re.search(r"(?m)^# (.+)$", raw)
        doc_title = m.group(1).strip() if m else path.stem
        parts = re.split(r"(?m)^## ", raw)
        for part in parts[1:]:
            head, _, body = part.partition("\n")
            head, body = head.strip(), " ".join(body.split())
            if not body:
                continue
            title = f"{doc_title} – {head}"
            chunks.append({"doc": path.name, "title": title, "text": f"{title}\n{body}"})
    return chunks


# ---------------------------------------------------------------------------
# Embedding (Gemini)
# ---------------------------------------------------------------------------
def _api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _embed_model() -> str:
    return os.environ.get("GEMINI_EMBED_MODEL", "").strip() or DEFAULT_EMBED_MODEL


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    """Gọi Gemini embedding. Lỗi (không có key, mất mạng, hết hạn mức) thì raise."""
    if not _api_key():
        raise RuntimeError("Không có GEMINI_API_KEY")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_api_key())
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = client.models.embed_content(
            model=_embed_model(),
            contents=texts[i:i + EMBED_BATCH],
            config=types.EmbedContentConfig(task_type=task_type),
        )
        vectors.extend(list(e.values) for e in resp.embeddings)
    return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------
# Lập chỉ mục
# ---------------------------------------------------------------------------
def build_index(policy_dir=DEFAULT_POLICY_DIR, use_embedding: bool | None = None) -> int:
    """Lập chỉ mục và lưu ra POLICY_INDEX_PATH. Trả số đoạn.

    use_embedding=None: có GEMINI_API_KEY thì thử embedding, lỗi thì chỉ lưu đoạn (dùng BM25).
    """
    global _INDEX_CACHE
    chunks = load_chunks(policy_dir)
    vectors, model, embed_error = None, None, None
    if use_embedding is None:
        use_embedding = bool(_api_key())
    if use_embedding and chunks:
        try:
            vectors = _embed([c["text"] for c in chunks], "RETRIEVAL_DOCUMENT")
            model = _embed_model()
        except Exception as e:  # mất mạng, sai key, hết hạn mức: vẫn có BM25
            vectors, embed_error = None, f"{type(e).__name__}: {e}"[:300]
    index = {
        "version": 1,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "policy_dir": str(policy_dir),
        "files": [p.name for p in _policy_files(policy_dir)],
        "model": model,
        "embed_error": embed_error,
        "chunks": chunks,
        "vectors": vectors,
    }
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    _INDEX_CACHE = index
    return len(chunks)


def _index_is_stale(path: Path, policy_dir) -> bool:
    if not path.exists():
        return True
    files = _policy_files(policy_dir)
    built = path.stat().st_mtime
    if any(f.stat().st_mtime > built for f in files):
        return True
    try:
        saved = json.loads(path.read_text(encoding="utf-8")).get("files")
    except (OSError, ValueError):
        return True
    return saved != [f.name for f in files]


def _load_index(policy_dir=DEFAULT_POLICY_DIR) -> dict:
    """Đọc chỉ mục; chưa có hoặc file .md mới hơn chỉ mục thì tự build."""
    global _INDEX_CACHE
    path = _index_path()
    if _index_is_stale(path, policy_dir):
        build_index(policy_dir)
        return _INDEX_CACHE
    if _INDEX_CACHE is None or _INDEX_CACHE.get("_path") != str(path) \
            or _INDEX_CACHE.get("_mtime") != path.stat().st_mtime:
        _INDEX_CACHE = json.loads(path.read_text(encoding="utf-8"))
        _INDEX_CACHE["_path"], _INDEX_CACHE["_mtime"] = str(path), path.stat().st_mtime
    return _INDEX_CACHE


# ---------------------------------------------------------------------------
# Tìm kiếm
# ---------------------------------------------------------------------------
def _bm25(query: str, chunks: list[dict], k: int) -> list[dict]:
    words = _expand_abbrev(_words(query))
    folded = not _has_diacritics(query)
    q_terms = _terms(words, folded)
    q_content = _content_words(words, folded)
    if not q_terms or not q_content:
        return []

    docs_words = [_words(c["text"]) for c in chunks]
    docs_terms = [_terms(w, folded) for w in docs_words]
    docs_sets = [_content_words(w, folded) for w in docs_words]
    n = len(chunks)
    avgdl = sum(len(t) for t in docs_terms) / n
    df: dict[str, int] = {}
    for terms in docs_terms:
        for t in set(terms):
            df[t] = df.get(t, 0) + 1

    results = []
    for c, terms, cset in zip(chunks, docs_terms, docs_sets):
        coverage = len(q_content & cset) / len(q_content)
        tf: dict[str, int] = {}
        for t in terms:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t in set(q_terms):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            f = tf[t]
            score += idf * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * len(terms) / avgdl))
        if score >= MIN_SCORE_BM25 and coverage >= MIN_COVERAGE_BM25:
            results.append({"doc": c["doc"], "title": c["title"], "text": c["text"],
                            "score": round(score, 3), "retriever": "bm25"})
    results.sort(key=lambda r: -r["score"])
    return results[:k]


def _embedding_search(query: str, index: dict, k: int) -> list[dict]:
    qv = _embed([query], "RETRIEVAL_QUERY")[0]
    results = []
    for c, v in zip(index["chunks"], index["vectors"]):
        score = _cosine(qv, v)
        if score >= MIN_SCORE_EMBEDDING:
            results.append({"doc": c["doc"], "title": c["title"], "text": c["text"],
                            "score": round(score, 4), "retriever": "embedding"})
    results.sort(key=lambda r: -r["score"])
    return results[:k]


def search(query: str, k: int = 3, force_bm25: bool = False) -> list[dict]:
    """Tìm tối đa k đoạn liên quan; không có đoạn nào đủ liên quan thì trả []."""
    if not isinstance(query, str) or not query.strip():
        return []
    try:
        k = max(1, int(k))
    except (TypeError, ValueError):
        k = 3
    index = _load_index()
    chunks = index.get("chunks") or []
    if not chunks:
        return []
    if not force_bm25 and index.get("vectors") and _api_key():
        try:
            return _embedding_search(query.strip(), index, k)
        except Exception:
            pass  # lỗi mạng / hết hạn mức: dùng BM25
    return _bm25(query.strip(), chunks, k)


# ---------------------------------------------------------------------------
# Câu hỏi mẫu (PA3) để chọn ngưỡng
# ---------------------------------------------------------------------------
def load_sample_questions(path=None) -> list[tuple[str, str | None]]:
    """Đọc _cau_hoi_mau.md -> [(câu hỏi, file đúng hoặc None nếu không có trong tài liệu)]."""
    path = _resolve(path or f"{DEFAULT_POLICY_DIR}/{SAMPLE_FILE}")
    out: list[tuple[str, str | None]] = []
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = "yes" if "CÓ trong" in line else "no" if "KHÔNG có" in line else None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if section and len(cells) >= 2 and cells[0].isdigit():
            out.append((cells[1], cells[2] if section == "yes" and len(cells) > 2 else None))
    return out


def evaluate_samples(force_bm25: bool = False) -> dict:
    rows, hit, empty_ok = [], 0, 0
    samples = load_sample_questions()
    for q, doc in samples:
        res = search(q, 3, force_bm25=force_bm25)
        docs = [r["doc"] for r in res]
        ok = (doc in docs) if doc else (res == [])
        hit += bool(doc) and ok
        empty_ok += (not doc) and ok
        rows.append({"question": q, "expected": doc, "got": [(r["doc"], r["score"]) for r in res],
                     "retriever": res[0]["retriever"] if res else None, "ok": ok})
    n_yes = sum(1 for _, d in samples if d)
    return {"rows": rows, "hit": hit, "n_yes": n_yes, "empty_ok": empty_ok, "n_no": len(samples) - n_yes}


# ---------------------------------------------------------------------------
# Chạy bằng lệnh
# ---------------------------------------------------------------------------
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    _load_dotenv()
    ap = argparse.ArgumentParser(description="Chỉ mục và tìm kiếm tài liệu quy định (RAG)")
    ap.add_argument("--build", action="store_true", help="lập lại chỉ mục")
    ap.add_argument("--query", help="câu hỏi cần tìm")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--eval", action="store_true", help="chấm 11 câu trong _cau_hoi_mau.md")
    ap.add_argument("--bm25", action="store_true", help="ép dùng BM25, không gọi mạng")
    args = ap.parse_args(argv)

    if args.build:
        n = build_index(use_embedding=False if args.bm25 else None)
        idx = _INDEX_CACHE or {}
        how = f"embedding {idx.get('model')}" if idx.get("vectors") else "chỉ BM25"
        print(f"Đã lập chỉ mục {n} đoạn từ {len(idx.get('files', []))} file -> {_index_path()} ({how})")
        if idx.get("embed_error"):
            print(f"  Không tạo được embedding, dùng BM25: {idx['embed_error']}")
    if args.query is not None:
        res = search(args.query, args.k, force_bm25=args.bm25)
        print(f'Câu hỏi: "{args.query}" -> {len(res)} đoạn')
        for i, r in enumerate(res, 1):
            print(f"  {i}. [{r['retriever']} {r['score']}] {r['doc']} | {r['title']}")
            print(f"     {r['text'].splitlines()[-1][:160]}...")
        if not res:
            print("  (không có đoạn nào đủ liên quan -> trả rỗng)")
    if args.eval:
        rep = evaluate_samples(force_bm25=args.bm25)
        for r in rep["rows"]:
            mark = "ĐẠT " if r["ok"] else "SAI "
            print(f"  {mark} {r['question']}  (đúng: {r['expected'] or 'rỗng'})  -> {r['got']}")
        print(f"Kết quả: {rep['hit']}/{rep['n_yes']} câu có trong tài liệu ra đúng file trong top 3; "
              f"{rep['empty_ok']}/{rep['n_no']} câu không có trả rỗng.")
    if not (args.build or args.query is not None or args.eval):
        ap.print_help()


if __name__ == "__main__":
    main()
