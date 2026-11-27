"""Unit test cho Evals v2 (YE6): bộ chấm, gộp dataset, file kết quả G7, thử lại khi 429.
Chạy hoàn toàn bằng FakeLLM, không cần mạng, không đụng data/bakery.db."""
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from evals import evaluators as ev
from evals import run as er

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 27, 9, 30)


def case(id="X01", group="data", question="Kem tươi còn bao nhiêu?", **expect):
    base = {"refused": False, "must_numbers": [], "must_contain": [], "tools_mode": "optional",
            "tools_any": [], "doc": None, "main_cause": None}
    base.update(expect)
    return {"id": id, "group": group, "question": question, "history": [], "ingredient_id": None,
            "expect": base}


def tool(name, **extra):
    return {"step": "tool_call", "tool_name": name, "arguments": {}, "ok": True, "via": "local", **extra}


# ---- File kết quả đúng G7 ----------------------------------------------------
@pytest.fixture()
def sample_run(tmp_path):
    cases = er.load_dataset([er.SAMPLE_PATH])
    summary = er.run_evals(cases, results_dir=tmp_path, now=NOW)
    return tmp_path, cases, summary


def test_sample_has_5_cases_covering_main_groups():
    cases = er.load_dataset([er.SAMPLE_PATH])
    assert len(cases) == 5
    assert {c["group"] for c in cases} == {"data", "followup", "trap", "policy", "audit"}


def test_sample_run_writes_all_g7_files(sample_run):
    out, cases, _ = sample_run
    assert (out / "20261127-0930_fake.csv").exists()
    assert (out / "latest.csv").exists()
    assert (out / "summary.json").exists()
    with open(out / "latest.csv", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == er.CSV_COLUMNS
        assert [r["id"] for r in reader] == [c["id"] for c in cases]


def test_summary_has_exactly_g7_keys(sample_run):
    out, _, summary = sample_run
    saved = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    expected = ["run_at", "tag", "mode", "agent_tools", "n", "pass_rate", "numbers_ok", "refusal_ok",
                "tools_ok", "source_ok", "recall_at_3", "fallback_rate", "latency_ms_avg", "tokens_avg",
                "by_group"]
    assert list(saved) == expected
    assert saved == summary
    assert saved["mode"] == "fake" and saved["tag"] == "fake" and saved["agent_tools"] == "local"
    assert saved["n"] == 5
    for key in ("pass_rate", "numbers_ok", "refusal_ok", "tools_ok", "fallback_rate"):
        assert saved[key] is None or 0 <= saved[key] <= 1
    assert set(saved["by_group"]) == {"data", "followup", "trap", "policy", "audit"}


def test_audit_case_scored_from_real_morning_audit(sample_run):
    out, _, _ = sample_run
    with open(out / "latest.csv", encoding="utf-8-sig") as f:
        row = next(r for r in csv.DictReader(f) if r["group"] == "audit")
    assert row["main_cause_ok"] == "True"          # Kem tươi: EXPIRING_LOT


def test_run_does_not_touch_real_database(tmp_path):
    db = ROOT / "data" / "bakery.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None
    er.run_evals(er.load_dataset([er.SAMPLE_PATH]), results_dir=tmp_path, now=NOW)
    after = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None
    assert before == after


def test_main_cli_runs_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "RESULTS_DIR", tmp_path)
    assert er.main(["--dataset", str(er.SAMPLE_PATH), "--limit", "2", "--tag", "thu"]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["n"] == 2 and summary["tag"] == "thu" and summary["mode"] == "fake"


# ---- Gộp dataset ------------------------------------------------------------
def _write(path, cases):
    path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")


def test_two_dataset_files_are_merged(tmp_path):
    _write(tmp_path / "dataset.jsonl", [case("D01"), case("D02")])
    _write(tmp_path / "dataset_policy.jsonl", [case("P01", "policy")])
    cases = er.load_dataset([tmp_path / "dataset.jsonl", tmp_path / "dataset_policy.jsonl"])
    assert [c["id"] for c in cases] == ["D01", "D02", "P01"]


def test_default_paths_read_every_dataset_file(tmp_path, monkeypatch):
    _write(tmp_path / "dataset.jsonl", [case("D01")])
    _write(tmp_path / "dataset_policy.jsonl", [case("P01", "policy")])
    monkeypatch.setattr(er, "EVALS_DIR", tmp_path)
    assert [p.name for p in er.default_dataset_paths()] == ["dataset.jsonl", "dataset_policy.jsonl"]


def test_default_paths_fall_back_to_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "EVALS_DIR", tmp_path)
    assert er.default_dataset_paths() == [er.SAMPLE_PATH]


def test_duplicate_id_across_files_is_an_error(tmp_path):
    _write(tmp_path / "dataset.jsonl", [case("D01")])
    _write(tmp_path / "dataset_policy.jsonl", [case("D01", "policy")])
    with pytest.raises(ValueError, match="Trùng id 'D01'"):
        er.load_dataset([tmp_path / "dataset.jsonl", tmp_path / "dataset_policy.jsonl"])


@pytest.mark.parametrize("bad", [{"group": "khac"}, {"expect": {"tools_mode": "all"}}])
def test_invalid_case_is_an_error(tmp_path, bad):
    item = case("D01")
    item.update(bad)
    _write(tmp_path / "dataset.jsonl", [item])
    with pytest.raises(ValueError):
        er.load_dataset([tmp_path / "dataset.jsonl"])


# ---- Thử lại khi gặp 429 -------------------------------------------------------
def _fake_agent(fail_times):
    calls = {"n": 0}

    def fake_run_agent(mode, payload, llm=None, tools=None, trace=None):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            trace.append({"step": "llm_error", "error": "ClientError: 429 RESOURCE_EXHAUSTED quota"})
            trace.append({"step": "fallback", "reason": "llm_failed"})
            return {"answer": "Hệ thống tạm thời chưa trả lời được.", "refused": False}
        trace.append(tool("check_inventory_fefo"))
        return {"answer": "Kem tươi còn 10 lít.", "refused": False}
    return fake_run_agent, calls


def test_rate_limit_is_retried_then_succeeds(monkeypatch):
    fake, calls = _fake_agent(fail_times=2)
    monkeypatch.setattr("agent.core_agent.run_agent", fake)
    waits = []
    obs = er.ask_agent(case(), llm=None, sleep=waits.append)
    assert waits == [10, 20] and calls["n"] == 3
    assert obs["answer"] == "Kem tươi còn 10 lít." and obs["note"] == ""


def test_rate_limit_gives_up_after_3_retries(monkeypatch):
    fake, calls = _fake_agent(fail_times=99)
    monkeypatch.setattr("agent.core_agent.run_agent", fake)
    waits = []
    obs = er.ask_agent(case(), llm=None, sleep=waits.append)
    assert waits == [10, 20, 40] and calls["n"] == 4
    row = er.score_chat(case(), obs)
    assert row["note"] == "RATE_LIMIT" and row["passed"] is False


def test_delay_between_cases_only_when_live(tmp_path, monkeypatch):
    fake, _ = _fake_agent(fail_times=0)
    monkeypatch.setattr("agent.core_agent.run_agent", fake)
    waits = []
    er.run_evals([case("A"), case("B"), case("C")], live=True, delay=4, sleep=waits.append,
                 results_dir=tmp_path, now=NOW)
    assert waits == [4, 4]


# ---- Bộ chấm: mỗi tiêu chí có ca đúng và ca sai ------------------------------------
def test_numbers_ok():
    exp = {"must_numbers": ["10", "0,5", "360.000"]}
    assert ev.numbers_ok("Còn 10 lít, lô 0.5 lít, giá 360.000đ", exp) is True
    assert ev.numbers_ok("Còn 10 lít, giá 360.000đ", exp) is False
    assert ev.numbers_ok("Còn 100 lít, lô 0,5, giá 360000", {"must_numbers": ["10"]}) is False
    assert ev.numbers_ok("bất kỳ", {"must_numbers": []}) is None


def test_numbers_ok_fails_when_agent_caught_made_up_numbers():
    trace = [{"step": "fallback", "reason": "numbers_mismatch"}]
    assert ev.numbers_ok("Còn 10 lít", {"must_numbers": ["10"]}, trace) is False


def test_refusal_ok():
    assert ev.refusal_ok(True, {"refused": True}) is True
    assert ev.refusal_ok(False, {"refused": True}) is False
    assert ev.refusal_ok(True, {"refused": False}) is False
    assert ev.refusal_ok(True, {"refused": True}, group="audit") is None


def test_tools_ok_mode_any():
    exp = {"tools_mode": "any", "tools_any": ["check_inventory_fefo"]}
    assert ev.tools_ok([tool("check_inventory_fefo")], exp) is True
    assert ev.tools_ok([tool("compare_suppliers")], exp) is False
    assert ev.tools_ok([], exp) is False


def test_tools_ok_mode_none():
    exp = {"tools_mode": "none", "tools_any": []}
    assert ev.tools_ok([], exp) is True
    assert ev.tools_ok([tool("check_inventory_fefo")], exp) is False


def test_tools_ok_mode_optional():
    exp = {"tools_mode": "optional", "tools_any": []}
    assert ev.tools_ok([], exp) is True
    assert ev.tools_ok([tool("check_delayed_po")], exp) is True


@pytest.mark.parametrize("mode", ["any", "none", "optional"])
def test_tools_ok_fails_on_write_tool_in_every_mode(mode):
    exp = {"tools_mode": mode, "tools_any": ["check_inventory_fefo"]}
    trace = [tool("check_inventory_fefo"), {"step": "tool_blocked", "tool_name": "create_draft_po"}]
    assert ev.tools_ok(trace, exp) is False


def test_source_ok_real_source():
    trace = [tool("search_policy", docs=["bao_quan.md", "nhan_hang.md"], retriever="bm25")]
    sources = [{"doc": "bao_quan.md", "title": "Quy định bảo quản"}]
    assert ev.source_ok(sources, trace, {"doc": "bao_quan.md"}, "policy") is True


def test_source_ok_made_up_source():
    trace = [tool("search_policy", docs=["nhan_hang.md"], retriever="bm25")]
    sources = [{"doc": "bao_quan.md", "title": "Quy định bảo quản"}]        # không có trong kết quả tìm
    assert ev.source_ok(sources, trace, {"doc": "bao_quan.md"}, "policy") is False


def test_source_ok_doc_null_needs_no_source():
    trace = [tool("search_policy", docs=[], retriever="bm25")]
    assert ev.source_ok([], trace, {"doc": None}, "policy") is True
    assert ev.source_ok([{"doc": "x.md"}], [tool("search_policy", docs=["x.md"])], {"doc": None}, "policy") is False
    assert ev.source_ok([], [], {"doc": None}, "data") is None


def test_retrieval_hit_top3():
    trace = [tool("search_policy", docs=["a.md", "b.md", "bao_quan.md", "z.md"])]
    assert ev.retrieval_hit(trace, {"doc": "bao_quan.md"}) is True
    assert ev.retrieval_hit([tool("search_policy", docs=["a.md", "b.md", "c.md", "bao_quan.md"])],
                            {"doc": "bao_quan.md"}) is False
    assert ev.retrieval_hit([], {"doc": "bao_quan.md"}) is False
    assert ev.retrieval_hit(trace, {"doc": None}) is None


def test_main_cause_ok():
    assert ev.main_cause_ok({"main_cause": "EXPIRING_LOT"}, {"main_cause": "EXPIRING_LOT"}, "audit") is True
    assert ev.main_cause_ok({"main_cause": "PREORDER"}, {"main_cause": "EXPIRING_LOT"}, "audit") is False
    assert ev.main_cause_ok(None, {"main_cause": "EXPIRING_LOT"}, "audit") is False
    assert ev.main_cause_ok({"main_cause": "X"}, {"main_cause": "X"}, "data") is None


def test_must_contain_counts_in_passed():
    obs = {"answer": "Hiện chưa có quy định về việc này.", "refused": False, "sources": [],
           "trace": [tool("search_policy", docs=[])], "latency_ms": 5, "tokens": None, "note": ""}
    ok = case("P1", "policy", tools_mode="any", tools_any=["search_policy"], must_contain=["chưa có quy định"])
    assert er.score_chat(ok, obs)["passed"] is True
    bad = case("P2", "policy", tools_mode="any", tools_any=["search_policy"], must_contain=["tủ mát"])
    row = er.score_chat(bad, obs)
    assert row["passed"] is False and "thiếu: tủ mát" in row["note"]


def test_tokens_and_latency_read_from_llm_call_steps():
    trace = [{"step": "llm_call", "model": "m", "tokens_in": 100, "tokens_out": 20, "latency_ms": 300},
             {"step": "llm_call", "model": "m", "tokens_in": 50, "tokens_out": None, "latency_ms": 200}]
    assert ev.tokens_from_trace(trace) == 170
    assert ev.latency_from_trace(trace) == 500
    assert ev.tokens_from_trace([]) is None


def test_summary_rates_skip_not_applicable():
    rows = [
        {"id": "A", "group": "data", "passed": True, "numbers_ok": True, "refusal_ok": True, "tools_ok": True,
         "source_ok": None, "retrieval_hit": None, "fallback": False, "latency_ms": 100, "tokens": None},
        {"id": "B", "group": "policy", "passed": False, "numbers_ok": None, "refusal_ok": True, "tools_ok": False,
         "source_ok": False, "retrieval_hit": True, "fallback": True, "latency_ms": 300, "tokens": None},
    ]
    cases = [case("A"), case("B", "policy", doc="bao_quan.md")]
    s = er.summarize(rows, cases, tag="t", mode="fake", agent_tools="local", run_at="x")
    assert s["pass_rate"] == 0.5 and s["numbers_ok"] == 1.0 and s["source_ok"] == 0.0
    assert s["recall_at_3"] == 1.0 and s["fallback_rate"] == 0.5 and s["latency_ms_avg"] == 200.0
    assert s["tokens_avg"] is None and s["by_group"] == {"data": 1.0, "policy": 0.0}


def test_langsmith_without_key_is_skipped(monkeypatch, caplog):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert er.push_to_langsmith([case()], [], "t") is False
    assert "LANGSMITH_API_KEY" in caplog.text
