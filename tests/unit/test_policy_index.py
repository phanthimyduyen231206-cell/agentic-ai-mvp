"""tests/unit/test_policy_index.py – PA3 (tài liệu quy định) + PA4 (chỉ mục, tìm kiếm RAG).

Chạy không cần mạng: GEMINI_API_KEY để trống nên dùng BM25; nhánh embedding dùng hàm embed giả.
"""
import os
import re
import time
from pathlib import Path

import pytest

import services.policy_index as pi
from data import init_db

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "data" / "policies"
DOCS = ["bao_quan.md", "nhan_hang.md", "xu_ly_sap_het_han.md", "ncc_chinh.md",
        "ncc_hoa_toc.md", "ncc_anh_duong.md", "quy_trinh_po.md", "xuat_kho.md"]


@pytest.fixture(autouse=True)
def offline_index(tmp_path, monkeypatch):
    """Mỗi test dùng file chỉ mục riêng trong thư mục tạm, không có key (BM25)."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("POLICY_INDEX_PATH", str(tmp_path / "policy_index.json"))
    monkeypatch.setattr(pi, "_INDEX_CACHE", None)
    yield


def read(name):
    return (POLICY_DIR / name).read_text(encoding="utf-8")


# ---------------- PA3: tài liệu ----------------

def test_du_8_tai_lieu_va_file_cau_hoi_mau():
    names = sorted(p.name for p in POLICY_DIR.glob("*.md"))
    assert sorted(DOCS + ["_cau_hoi_mau.md"]) == names


@pytest.mark.parametrize("name", DOCS)
def test_dinh_dang_tai_lieu(name):
    text = read(name)
    assert text.startswith("# ")
    assert text.rstrip().endswith("Cập nhật: 28/11/2026")
    assert 300 <= len(text.split()) <= 700
    sections = re.split(r"(?m)^(?=## )", text)[1:]
    assert len(sections) >= 3
    for s in sections:
        body = s.replace("Cập nhật: 28/11/2026", "").strip()
        assert len(body) <= 600, f"{name}: mục quá 600 ký tự: {body[:40]}"


def test_han_dung_trong_tai_lieu_khop_db():
    text = read("bao_quan.md")
    names = {"BOT_MI": "Bột mì", "BO_LAT": "Bơ lạt", "KEM_TUOI": "Kem tươi", "MASCARPONE": "Mascarpone",
             "TRUNG": "Trứng gà", "DUONG": "Đường", "SUA_TUOI": "Sữa tươi", "MEN_NO": "men nở",
             "BOT_CACAO": "bột cacao", "SOCOLA": "Socola", "VANI": "vani", "PHO_MAI_KEM": "Cream cheese"}
    for ing_id, _, _, _, shelf, _ in init_db.INGREDIENTS:
        assert names[ing_id].lower() in text.lower()
        assert f"{shelf} ngày" in text, f"thiếu hạn {shelf} ngày của {ing_id}"


def _vnd(x):
    return f"{int(x):,}".replace(",", ".") if float(x).is_integer() else str(x)


def _qty(x):
    return str(int(x)) if float(x).is_integer() else str(x).replace(".", ",")


@pytest.mark.parametrize("sup_id,doc", [("NCC_CHINH", "ncc_chinh.md"), ("NCC_HOA_TOC", "ncc_hoa_toc.md"),
                                        ("NCC_ANH_DUONG", "ncc_anh_duong.md")])
def test_gia_moq_lead_time_email_khop_db(sup_id, doc):
    text = read(doc)
    for (sid, _, lead, email), ing, price, moq, rel, _ in init_db.SUPPLIER_ROWS:
        if sid != sup_id:
            continue
        assert f"{_vnd(price)} đ" in text, f"{doc}: thiếu giá {price} của {ing}"
        assert f"{_qty(moq)} " in text, f"{doc}: thiếu MOQ {moq} của {ing}"
        assert f"{lead} giờ" in text
        assert email in text
        assert f"{round(rel * 100)}%" in text


def test_phi_giao_gap_hoa_toc_15_phan_tram():
    prices = {(s[0][0], s[1]): s[2] for s in init_db.SUPPLIER_ROWS}
    for ing in ("KEM_TUOI", "BO_LAT", "BOT_MI"):
        assert prices[("NCC_HOA_TOC", ing)] == pytest.approx(prices[("NCC_CHINH", ing)] * 1.15)
    assert "15%" in read("ncc_hoa_toc.md")


def test_tai_lieu_khong_ghi_so_ton_kho():
    for name in DOCS:
        assert not re.search(r"(?i)tồn kho (hiện tại|còn) \d", read(name))


# ---------------- PA4: cắt đoạn, lập chỉ mục ----------------

def test_cat_doan_bo_file_gach_duoi():
    chunks = pi.load_chunks()
    docs = {c["doc"] for c in chunks}
    assert docs == set(DOCS)
    assert all(not c["doc"].startswith("_") for c in chunks)
    first = next(c for c in chunks if c["doc"] == "bao_quan.md" and "Kem tươi" in c["title"])
    assert first["title"] == "Quy định bảo quản nguyên liệu – Kem tươi"
    assert first["text"].startswith(first["title"])
    assert "Cập nhật" not in first["text"]


def test_build_index_tra_so_doan_va_luu_file(tmp_path):
    n = pi.build_index()
    assert n == len(pi.load_chunks()) > 0
    assert Path(os.environ["POLICY_INDEX_PATH"]).exists()


def test_search_tu_build_khi_chua_co_chi_muc():
    assert not Path(os.environ["POLICY_INDEX_PATH"]).exists()
    assert pi.search("kem tươi bảo quản")
    assert Path(os.environ["POLICY_INDEX_PATH"]).exists()


def test_chi_muc_cu_hon_file_md_thi_build_lai(tmp_path):
    pdir = tmp_path / "policies"
    pdir.mkdir()
    (pdir / "a.md").write_text("# A\n\n## Muc\nnoi dung kem tuoi\n", encoding="utf-8")
    pi.build_index(pdir)
    idx = Path(os.environ["POLICY_INDEX_PATH"])
    assert not pi._index_is_stale(idx, pdir)
    time.sleep(0.05)
    (pdir / "a.md").write_text("# A\n\n## Muc\nnoi dung moi\n", encoding="utf-8")
    os.utime(pdir / "a.md", (idx.stat().st_mtime + 5, idx.stat().st_mtime + 5))
    assert pi._index_is_stale(idx, pdir)


# ---------------- PA4: tìm kiếm BM25 (không mạng) ----------------

def test_kem_tuoi_bao_quan_ra_bao_quan():
    res = pi.search("kem tươi bảo quản")
    assert res and res[0]["doc"] == "bao_quan.md"
    assert "Kem tươi" in res[0]["title"]
    assert res[0]["retriever"] == "bm25"


def test_ncc_hoa_toc_giao_tre():
    res = pi.search("ncc hỏa tốc giao trễ")
    assert res and res[0]["doc"] == "ncc_hoa_toc.md"


def test_cau_khong_lien_quan_tra_rong():
    assert pi.search("quy định đồng phục nhân viên") == []


@pytest.mark.parametrize("q", ["", "   ", None])
def test_query_rong_tra_rong(q):
    assert pi.search(q) == []


def test_k_gioi_han_so_ket_qua():
    assert 1 <= len(pi.search("nhận hàng kiểm tra", k=3)) <= 3
    assert len(pi.search("nhận hàng kiểm tra", k=1)) == 1


def test_ket_qua_dung_dang_g4():
    res = pi.search("kem tươi bảo quản thế nào")
    assert set(res[0]) == {"doc", "title", "text", "score", "retriever"}
    assert isinstance(res[0]["score"], float)
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)
    assert all(s >= pi.MIN_SCORE_BM25 for s in scores)


@pytest.mark.parametrize("q,doc", [
    ("kem tuoi bao quan the nao", "bao_quan.md"),                 # không dấu
    ("ncc hoa toc giao tre thi lam gi", "ncc_hoa_toc.md"),
    ("hsd cua bo lat la bao lau", "bao_quan.md"),                 # viết tắt hsd
    ("dat trung o anh duong toi thieu bao nhieu", "ncc_anh_duong.md"),
])
def test_cau_hoi_khong_dau_va_viet_tat(q, doc):
    assert doc in [r["doc"] for r in pi.search(q)]


def test_11_cau_hoi_mau_chon_nguong():
    samples = pi.load_sample_questions()
    assert len(samples) == 11
    assert sum(1 for _, d in samples if d) == 8
    rep = pi.evaluate_samples(force_bm25=True)
    bad = [r for r in rep["rows"] if not r["ok"]]
    assert rep["hit"] == 8 and rep["empty_ok"] == 3, bad


# ---------------- PA4: nhánh embedding (embed giả, không mạng) ----------------

def _fake_embed(texts, task_type):
    """Vector giả: đếm từ khóa, đủ để kiểm cosine và ngưỡng."""
    keys = ["kem", "bơ", "trứng", "ncc", "hỏa", "giao", "po", "xuất", "nhận", "hạn"]
    return [[t.lower().count(k) + 0.01 for k in keys] for t in texts]


def test_co_key_thi_tim_bang_embedding(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "khoa-gia")
    monkeypatch.setattr(pi, "_embed", _fake_embed)
    monkeypatch.setattr(pi, "MIN_SCORE_EMBEDDING", 0.5)
    pi.build_index()
    res = pi.search("kem kem kem")
    assert res and all(r["retriever"] == "embedding" for r in res)
    assert len(res) <= 3


def test_embedding_loi_khi_tim_thi_dung_bm25(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "khoa-gia")
    monkeypatch.setattr(pi, "_embed", _fake_embed)
    pi.build_index()

    def broken(texts, task_type):
        raise ConnectionError("mất mạng")
    monkeypatch.setattr(pi, "_embed", broken)
    res = pi.search("kem tươi bảo quản")
    assert res and res[0]["retriever"] == "bm25" and res[0]["doc"] == "bao_quan.md"


def test_embedding_loi_khi_build_van_co_chi_muc_bm25(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "khoa-gia")

    def broken(texts, task_type):
        raise RuntimeError("429 hết hạn mức")
    monkeypatch.setattr(pi, "_embed", broken)
    assert pi.build_index() > 0
    assert pi._INDEX_CACHE["vectors"] is None and "429" in pi._INDEX_CACHE["embed_error"]
    assert pi.search("kem tươi bảo quản")[0]["retriever"] == "bm25"


def test_cosine():
    assert pi._cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert pi._cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert pi._cosine([0, 0], [1, 1]) == 0.0
