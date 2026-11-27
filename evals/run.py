"""evals/run.py – Chạy bộ câu hỏi Evals và ghi bảng điểm (YE6, Giao ước G1, G2, G5, G6, G7).

Chạy (đứng ở thư mục gốc repo, đã bật .venv):
    python -m evals.run                         # Gemini giả (FakeLLM), không cần mạng
    python -m evals.run --live --limit 5        # Gemini thật, 5 câu đầu
    python -m evals.run --live --tag mcp        # đặt tên lần chạy (tên file CSV, khóa "tag")
    python -m evals.run --dataset evals/dataset_policy.jsonl
    python -m evals.run --live --langsmith      # đẩy thêm kết quả lên LangSmith (cần LANGSMITH_API_KEY)

Không có --dataset: đọc và gộp mọi file evals/dataset*.jsonl (trùng id thì báo lỗi). Chưa có file
dataset nào thì dùng evals/sample.jsonl (5 câu mẫu).

Ghi ra evals/results/: <YYYYMMDD-HHMM>_<tag>.csv, latest.csv, summary.json (đúng tên cột / khóa G7).
Mỗi lần chạy dùng một database tạm (init_db, DEMO_DATE=2026-11-20T08:00), KHÔNG đụng data/bakery.db.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from evals import evaluators as ev

ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = ROOT / "evals"
RESULTS_DIR = EVALS_DIR / "results"
SAMPLE_PATH = EVALS_DIR / "sample.jsonl"

DEMO_DATE = "2026-11-20T08:00"
GROUPS = ("data", "followup", "trap", "injection", "out_of_scope", "policy", "audit")
TOOLS_MODES = ("any", "none", "optional")

# Tên cột CSV và khóa summary.json: KHÔNG đổi (YE7, MA6 đọc đúng các tên này – G7).
CSV_COLUMNS = ["id", "group", "passed", "numbers_ok", "refusal_ok", "tools_ok", "source_ok",
               "retrieval_hit", "main_cause_ok", "fallback", "retriever", "latency_ms", "tokens",
               "answer", "note"]
SUMMARY_KEYS = ["run_at", "tag", "mode", "agent_tools", "n", "pass_rate", "numbers_ok", "refusal_ok",
                "tools_ok", "source_ok", "recall_at_3", "fallback_rate", "latency_ms_avg", "tokens_avg",
                "by_group"]

# Gặp lỗi 429 / hết hạn mức Gemini: chờ lần lượt các mốc này rồi thử lại; hết thì note RATE_LIMIT.
RETRY_WAITS = (10, 20, 40)
RATE_LIMIT_MARKERS = ("429", "resource_exhausted", "resource exhausted", "quota", "rate limit")
DEFAULT_DELAY_SECONDS = 4.0

logger = logging.getLogger("evals")


# ---------------------------------------------------------------------------
# Đọc bộ câu hỏi (G6)
# ---------------------------------------------------------------------------
def default_dataset_paths() -> list[Path]:
    paths = sorted(EVALS_DIR.glob("dataset*.jsonl"))
    if paths:
        return paths
    logger.warning("Chưa có file evals/dataset*.jsonl, dùng 5 câu mẫu evals/sample.jsonl.")
    return [SAMPLE_PATH]


def _check_case(case: dict, where: str) -> dict:
    for key in ("id", "group", "question", "expect"):
        if key not in case:
            raise ValueError(f"{where}: thiếu khóa {key!r}")
    if case["group"] not in GROUPS:
        raise ValueError(f"{where}: group {case['group']!r} không hợp lệ (chỉ nhận {', '.join(GROUPS)})")
    expect = case["expect"] or {}
    if (expect.get("tools_mode") or "optional") not in TOOLS_MODES:
        raise ValueError(f"{where}: tools_mode {expect.get('tools_mode')!r} không hợp lệ")
    case.setdefault("history", [])
    case.setdefault("ingredient_id", None)
    case["expect"] = expect
    return case


def load_dataset(paths: list[Path | str]) -> list[dict]:
    """Gộp nhiều file JSONL (mỗi dòng một câu). Trùng id giữa các file hoặc trong một file -> ValueError."""
    cases: list[dict] = []
    seen: dict[str, str] = {}
    for path in paths:
        path = Path(path)
        with open(path, encoding="utf-8-sig") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                where = f"{path.name} dòng {line_no}"
                try:
                    case = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{where}: JSON sai ({exc.msg})") from exc
                case = _check_case(case, where)
                if case["id"] in seen:
                    raise ValueError(f"Trùng id {case['id']!r}: {seen[case['id']]} và {where}")
                seen[case["id"]] = where
                cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# Database tạm, không đụng data/bakery.db
# ---------------------------------------------------------------------------
@contextmanager
def temp_database():
    from data.init_db import init_db

    old = {k: os.environ.get(k) for k in ("BAKERY_DB_PATH", "DEMO_DATE")}
    with tempfile.TemporaryDirectory(prefix="bakery-evals-") as tmp:
        path = Path(tmp) / "evals.db"
        os.environ["DEMO_DATE"] = DEMO_DATE
        os.environ["BAKERY_DB_PATH"] = str(path)
        try:
            init_db(path, "standard", now=datetime.fromisoformat(DEMO_DATE))
            yield path
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# ---------------------------------------------------------------------------
# Gọi Agent
# ---------------------------------------------------------------------------
def make_llm(live: bool, mode: str):
    """--live: None (core_agent tự dùng Gemini thật). Không --live: FakeLLM."""
    if live:
        return None
    from tests.fake_llm import FakeLLM

    return FakeLLM("tool_call" if mode == "READ_ONLY_CHAT" else "normal")


def is_rate_limited(trace) -> bool:
    for t in ev.steps(trace, "llm_error"):
        text = str(t.get("error", "")).lower()
        if any(m in text for m in RATE_LIMIT_MARKERS):
            return True
    return False


def ask_agent(case: dict, llm, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Một câu chat: run_agent("READ_ONLY_CHAT"), thử lại khi gặp 429 (chờ 10, 20, 40 giây)."""
    from agent.core_agent import run_agent

    payload = {"question": case["question"], "history": case.get("history") or []}
    note = ""
    for attempt in range(len(RETRY_WAITS) + 1):
        trace: list[dict] = []
        start = time.perf_counter()
        result = run_agent("READ_ONLY_CHAT", payload, llm=llm, trace=trace)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if not is_rate_limited(trace):
            break
        if attempt == len(RETRY_WAITS):
            note = "RATE_LIMIT"
            break
        logger.warning("%s: gặp giới hạn Gemini (429), chờ %ss rồi thử lại", case["id"], RETRY_WAITS[attempt])
        sleep(RETRY_WAITS[attempt])
    return {
        "answer": str(result.get("answer", "")),
        "refused": bool(result.get("refused", False)),
        "sources": result.get("sources") or [],
        "trace": trace,
        "latency_ms": ev.latency_from_trace(trace) or elapsed_ms,
        "tokens": ev.tokens_from_trace(trace),
        "note": note,
    }


def run_audit_once(llm) -> dict:
    """Chạy rà soát buổi sáng 1 lần trên DB tạm, trả {ingredient_id: cảnh báo} + thời gian chạy."""
    from data import repository
    from services.audit_service import run_morning_audit

    start = time.perf_counter()
    result = run_morning_audit("SCHEDULED", llm=llm)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    alerts = repository.get_alerts(run_id=result.get("run_id")) if result.get("run_id") else []
    return {"alerts": {a["ingredient_id"]: dict(a) for a in alerts}, "latency_ms": elapsed_ms,
            "status": result.get("status"), "message": result.get("message", "")}


# ---------------------------------------------------------------------------
# Chấm từng câu
# ---------------------------------------------------------------------------
def score_chat(case: dict, obs: dict) -> dict:
    expect, group, trace = case["expect"], case["group"], obs["trace"]
    row = {
        "id": case["id"], "group": group,
        "numbers_ok": ev.numbers_ok(obs["answer"], expect, trace),
        "refusal_ok": ev.refusal_ok(obs["refused"], expect, group),
        "tools_ok": ev.tools_ok(trace, expect),
        "source_ok": ev.source_ok(obs["sources"], trace, expect, group),
        "retrieval_hit": ev.retrieval_hit(trace, expect),
        "main_cause_ok": None,
        "fallback": ev.has_fallback(trace),
        "retriever": ev.retriever_of(trace),
        "latency_ms": obs["latency_ms"], "tokens": obs["tokens"],
        "answer": obs["answer"], "note": obs["note"],
    }
    return _finish(row, ev.missing_contains(obs["answer"], expect))


def score_audit(case: dict, audit: dict) -> dict:
    expect = case["expect"]
    alert = audit["alerts"].get(case.get("ingredient_id"))
    explanation = (alert or {}).get("explanation") or ""
    row = {
        "id": case["id"], "group": "audit",
        "numbers_ok": ev.numbers_ok(explanation, expect),
        "refusal_ok": None, "tools_ok": None, "source_ok": None, "retrieval_hit": None,
        "main_cause_ok": ev.main_cause_ok(alert, expect, "audit"),
        "fallback": bool((alert or {}).get("ai_fallback")),
        "retriever": None, "latency_ms": audit["latency_ms"], "tokens": None,
        "answer": explanation,
        "note": "" if alert else f"Không có cảnh báo cho {case.get('ingredient_id')} ({audit['status']})",
    }
    return _finish(row, ev.missing_contains(explanation, expect))


def _finish(row: dict, missing: list[str]) -> dict:
    if missing:
        extra = "thiếu: " + ", ".join(missing)
        row["note"] = f"{row['note']}; {extra}" if row["note"] else extra
    row["passed"] = ev.passed(row, missing)
    return row


# ---------------------------------------------------------------------------
# Tổng hợp (G7)
# ---------------------------------------------------------------------------
def _rate(values) -> float | None:
    vals = [bool(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _avg(values) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def summarize(rows: list[dict], cases: list[dict], *, tag: str, mode: str, agent_tools: str,
              run_at: str) -> dict:
    doc_ids = {c["id"] for c in cases if c["group"] == "policy" and c["expect"].get("doc")}
    groups = sorted({r["group"] for r in rows})
    summary = {
        "run_at": run_at, "tag": tag, "mode": mode, "agent_tools": agent_tools, "n": len(rows),
        "pass_rate": _rate(r["passed"] for r in rows),
        "numbers_ok": _rate(r["numbers_ok"] for r in rows),
        "refusal_ok": _rate(r["refusal_ok"] for r in rows),
        "tools_ok": _rate(r["tools_ok"] for r in rows),
        "source_ok": _rate(r["source_ok"] for r in rows),
        "recall_at_3": _rate(bool(r["retrieval_hit"]) for r in rows if r["id"] in doc_ids),
        "fallback_rate": _rate(r["fallback"] for r in rows),
        "latency_ms_avg": _avg(r["latency_ms"] for r in rows),
        "tokens_avg": _avg(r["tokens"] for r in rows),
        "by_group": {g: _rate(r["passed"] for r in rows if r["group"] == g) for g in groups},
    }
    return {k: summary[k] for k in SUMMARY_KEYS}


def _cell(value: Any) -> Any:
    return "" if value is None else value


def write_results(rows: list[dict], summary: dict, results_dir: Path, stamp: str) -> dict[str, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    run_csv = results_dir / f"{stamp}_{summary['tag']}.csv"
    for path in (run_csv, results_dir / "latest.csv"):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: _cell(row.get(k)) for k in CSV_COLUMNS})
    summary_path = results_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv": run_csv, "latest": results_dir / "latest.csv", "summary": summary_path}


# ---------------------------------------------------------------------------
# LangSmith (tùy chọn)
# ---------------------------------------------------------------------------
def push_to_langsmith(cases: list[dict], rows: list[dict], tag: str) -> bool:
    """Đẩy kết quả đã chấm lên LangSmith bằng langsmith.evaluate. Thiếu thư viện hoặc key thì bỏ qua."""
    if not os.getenv("LANGSMITH_API_KEY"):
        logger.warning("Bỏ qua --langsmith: chưa có LANGSMITH_API_KEY trong .env.")
        return False
    try:
        from langsmith import Client, evaluate
    except ImportError:
        logger.warning("Bỏ qua --langsmith: chưa cài thư viện langsmith (pip install -r requirements.txt).")
        return False
    try:
        client = Client()
        name = os.getenv("LANGSMITH_PROJECT", "bakery-agent") + "-evals"
        if not client.has_dataset(dataset_name=name):
            dataset = client.create_dataset(name, description="Bộ câu hỏi Evals tiệm bánh (G6)")
            client.create_examples(
                dataset_id=dataset.id,
                examples=[{"inputs": {"id": c["id"], "group": c["group"], "question": c["question"]},
                           "outputs": c["expect"]} for c in cases])
        by_id = {r["id"]: r for r in rows}

        def target(inputs: dict) -> dict:
            row = by_id.get(inputs.get("id"), {})
            return {k: row.get(k) for k in CSV_COLUMNS}

        def metric(key):
            def _eval(run, example) -> dict:
                value = (run.outputs or {}).get(key)
                return {"key": key, "score": None if value is None else float(bool(value))}
            return _eval

        evaluate(target, data=name, experiment_prefix=tag,
                 evaluators=[metric(k) for k in ("passed", "numbers_ok", "refusal_ok", "tools_ok",
                                                 "source_ok", "retrieval_hit", "main_cause_ok")],
                 client=client)
        return True
    except Exception as exc:  # noqa: BLE001 – LangSmith lỗi không làm hỏng kết quả CSV đã ghi
        logger.warning("Đẩy lên LangSmith thất bại: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Chạy
# ---------------------------------------------------------------------------
def run_evals(cases: list[dict], *, live: bool = False, tag: str | None = None, langsmith: bool = False,
              results_dir: Path | None = None, delay: float | None = None,
              sleep: Callable[[float], None] = time.sleep, now: datetime | None = None) -> dict:
    """Chạy toàn bộ câu hỏi, ghi kết quả, trả summary. Không --live thì không cần mạng."""
    mode = "live" if live else "fake"
    tag = tag or mode
    agent_tools = os.getenv("AGENT_TOOLS", "local").strip().lower() or "local"
    if delay is None:
        delay = float(os.getenv("EVAL_DELAY_SECONDS", DEFAULT_DELAY_SECONDS)) if live else 0.0
    started = now or datetime.now()

    rows: list[dict] = []
    with temp_database():
        chat_llm = make_llm(live, "READ_ONLY_CHAT")
        audit = None
        for i, case in enumerate(cases):
            if i and delay > 0:
                sleep(delay)
            if case["group"] == "audit":
                if audit is None:
                    audit = run_audit_once(make_llm(live, "MORNING_AUDIT"))
                row = score_audit(case, audit)
            else:
                row = score_chat(case, ask_agent(case, chat_llm, sleep=sleep))
            rows.append(row)
            logger.info("%-5s %-12s %-5s %6s ms  %s", case["id"], case["group"],
                        "ĐẠT" if row["passed"] else "TRƯỢT", row["latency_ms"], row["note"])

    summary = summarize(rows, cases, tag=tag, mode=mode, agent_tools=agent_tools,
                        run_at=started.isoformat(timespec="seconds"))
    files = write_results(rows, summary, Path(results_dir or RESULTS_DIR), started.strftime("%Y%m%d-%H%M"))
    total_s = round((datetime.now() - started).total_seconds()) if now is None else None
    logger.info("Đạt %s/%s câu%s. Đã ghi %s, %s, %s", sum(r["passed"] for r in rows), len(rows),
                f", tổng {total_s} giây" if total_s is not None else "",
                files["csv"].name, files["latest"].name, files["summary"].name)
    if langsmith:
        push_to_langsmith(cases, rows, tag)
    return summary


def _load_env() -> None:
    """Đọc .env (GEMINI_API_KEY, EVAL_DELAY_SECONDS, AGENT_TOOLS, LANGSMITH_*), không ghi đè biến đã có."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description="Chấm Agent tự động (Evals v2)")
    parser.add_argument("--live", action="store_true", help="dùng Gemini thật (mặc định: FakeLLM, không cần mạng)")
    parser.add_argument("--langsmith", action="store_true", help="đẩy thêm kết quả lên LangSmith")
    parser.add_argument("--tag", help="tên lần chạy, vd local hoặc mcp (mặc định: fake / live)")
    parser.add_argument("--dataset", action="append", help="file câu hỏi; bỏ trống = mọi evals/dataset*.jsonl")
    parser.add_argument("--limit", type=int, help="chỉ chạy N câu đầu")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)
    _load_env()

    try:
        cases = load_dataset(args.dataset or default_dataset_paths())
    except (OSError, ValueError) as exc:
        logger.error("Không đọc được bộ câu hỏi: %s", exc)
        return 2
    if args.limit:
        cases = cases[:args.limit]
    logger.info("Chạy %d câu, chế độ %s, công cụ %s", len(cases), "live" if args.live else "fake",
                os.getenv("AGENT_TOOLS", "local"))
    run_evals(cases, live=args.live, tag=args.tag, langsmith=args.langsmith)
    return 0


if __name__ == "__main__":
    sys.exit(main())
