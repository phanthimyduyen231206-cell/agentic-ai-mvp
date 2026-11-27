"""run_morning_audit(): chống chạy trùng, khôi phục phiên RUNNING, lưu cảnh báo.

Chủ file: Mai Anh (M05). Chữ ký hàm: xem docs/contracts.md mục 10.

    run_morning_audit(trigger, rerun=False, user=None, llm=None) -> dict
        {"run_id": int | None, "status": "SUCCEEDED" | "FAILED" | "REJECTED_DUPLICATE" | "ALREADY_DONE",
         "alert_ids": [int], "message": str}
    recover_stale_runs(max_minutes=15) -> list[int]

Các bước của 1 phiên rà soát:
1. BEGIN IMMEDIATE (khóa ghi). Đang có phiên RUNNING → từ chối, log RUN_REJECTED_DUPLICATE.
   Hôm nay đã SUCCEEDED mà không chọn "Chạy lại" → trả kết quả phiên đó (ALREADY_DONE).
   Ngược lại tạo audit_runs RUNNING, log RUN_STARTED. Index ux_run_running chặn thêm lần nữa ở DB.
2. PO đã SENT, delivery_status PENDING, quá ngày hẹn → DELAYED (status PO vẫn SENT), log DELIVERY_DELAYED.
3. Gọi calc.compute_alerts(today) (Q02) – code tính số, không dùng LLM.
4. Không thiếu → SUCCEEDED, không tạo cảnh báo.
   Có thiếu → core_agent.run_agent("MORNING_AUDIT") diễn giải (Gemini lỗi thì có câu mẫu dự phòng),
   lưu alerts OPEN theo contracts mục 10, log ALERT_CREATED từng cảnh báo, rồi RUN_SUCCEEDED.
   snapshot_hash = draft["snapshot_hash"] do calc tính TRƯỚC khi gọi Gemini (không tính lại sau):
   Gemini chậm mà có người xuất/nhập kho trong lúc chờ thì hash đã lưu khác hash hiện tại,
   create_draft_po chặn SNAPSHOT_CHANGED (TC15). Cột ai_fallback = 1 khi cảnh báo dùng câu mẫu.
   Phiên thành công (kể cả không thiếu hàng) đóng cảnh báo OPEN của các phiên trước → DISMISSED,
   log ALERT_DISMISSED (reason SUPERSEDED); cảnh báo SELECTED giữ nguyên.
5. Lỗi bất kỳ ở bước 2–4 → phiên FAILED, ghi lỗi, log RUN_FAILED. Hàm không raise.

recover_stale_runs(): gọi khi app/scheduler khởi động; phiên RUNNING quá 15 phút → FAILED,
để khóa ux_run_running được nhả và rà soát chạy lại được.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from agent.core_agent import run_agent
from data import repository as repo
from services import clock
from services.audit_log import write_audit_log

logger = logging.getLogger(__name__)

STALE_MINUTES = 15
TRIGGERS = ("SCHEDULED", "MANUAL")


@contextmanager
def _tx():
    """Mở kết nối, commit nếu không lỗi, rollback nếu lỗi, luôn đóng kết nối."""
    conn = repo.get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ts(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(timespec="seconds")


def _actor(trigger: str, user: Any) -> str:
    if isinstance(user, dict) and user.get("username"):
        return str(user["username"])
    return "scheduler" if trigger == "SCHEDULED" else "system"


def _alert_ids(run_id: int, conn=None) -> list[int]:
    return [a["alert_id"] for a in repo.get_alerts(run_id=run_id, conn=conn)]


def _result(run_id, status, alert_ids, message) -> dict:
    return {"run_id": run_id, "status": status, "alert_ids": list(alert_ids), "message": message}


# ---------------------------------------------------------------------------
# Bước 1: giành quyền chạy (chống chạy trùng)
# ---------------------------------------------------------------------------

def _start_run(trigger: str, rerun: bool, actor: str) -> dict:
    """Trả {"run_id"} nếu được chạy, hoặc dict kết quả (REJECTED_DUPLICATE / ALREADY_DONE)."""
    today = clock.today()
    conn = repo.get_connection()
    conn.isolation_level = None  # tự quản lý transaction để dùng BEGIN IMMEDIATE
    try:
        conn.execute("BEGIN IMMEDIATE")
        running = repo.get_running_audit_run(conn=conn)
        if running is not None:
            conn.execute("ROLLBACK")
            write_audit_log(actor, "RUN_REJECTED_DUPLICATE", entity_type="audit_run",
                            entity_id=running["run_id"], run_id=running["run_id"],
                            detail={"trigger": trigger, "reason": "RUNNING"})
            return _result(running["run_id"], "REJECTED_DUPLICATE", [],
                           "Đang có phiên rà soát chạy, vui lòng đợi phiên đó xong.")

        if not rerun:
            done = repo.get_audit_runs(run_date=today, status="SUCCEEDED", conn=conn)
            if done:
                conn.execute("ROLLBACK")
                last = done[0]
                return _result(last["run_id"], "ALREADY_DONE", _alert_ids(last["run_id"]),
                               "Hôm nay đã rà soát. Chọn \"Chạy lại\" nếu muốn rà soát lần nữa.")

        run_id = repo.insert_audit_run({
            "run_date": today.isoformat(), "trigger": trigger, "is_rerun": int(bool(rerun)),
            "status": "RUNNING", "started_at": _ts(clock.now()),
        }, conn=conn)
        write_audit_log(actor, "RUN_STARTED", entity_type="audit_run", entity_id=run_id, run_id=run_id,
                        detail={"trigger": trigger, "rerun": bool(rerun)}, conn=conn)
        conn.execute("COMMIT")
        return {"run_id": run_id}
    except sqlite3.IntegrityError:
        # ux_run_running: một tiến trình khác vừa tạo phiên RUNNING
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        write_audit_log(actor, "RUN_REJECTED_DUPLICATE", entity_type="audit_run",
                        detail={"trigger": trigger, "reason": "UNIQUE_RUNNING"})
        return _result(None, "REJECTED_DUPLICATE", [], "Đang có phiên rà soát chạy, vui lòng đợi phiên đó xong.")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bước 2: đánh dấu PO giao trễ
# ---------------------------------------------------------------------------

def mark_delayed_pos(run_id: int | None, actor: str = "system") -> list[str]:
    """PO SENT, delivery_status PENDING, expected_delivery_date < hôm nay → DELAYED. Trả danh sách po_id."""
    today = clock.today().isoformat()
    delayed = []
    with _tx() as conn:
        for po in repo.get_purchase_orders(status="SENT", conn=conn):
            due = (po.get("expected_delivery_date") or "")[:10]
            if po.get("delivery_status") == "PENDING" and po.get("closed_reason") is None and due and due < today:
                repo.update_purchase_order(po["po_id"], {"delivery_status": "DELAYED"}, conn=conn)
                write_audit_log(actor, "DELIVERY_DELAYED", entity_type="purchase_order", entity_id=po["po_id"],
                                run_id=run_id, detail={"expected_delivery_date": due, "today": today}, conn=conn)
                delayed.append(po["po_id"])
    return delayed


# ---------------------------------------------------------------------------
# Bước 3–4: tính cảnh báo, diễn giải, lưu
# ---------------------------------------------------------------------------

def _compute_alerts(run_date) -> list[dict]:
    from services import calc

    fn = getattr(calc, "compute_alerts", None)
    if fn is None:
        raise RuntimeError("services/calc.compute_alerts chưa có (Q02)")
    return list(fn(run_date) or [])


def _causes_json(causes: list) -> list[dict]:
    """contracts mục 10: lưu đủ dạng [{"code", "qty"}]; mã không có số lượng thì qty = None."""
    out = []
    for c in causes or []:
        if isinstance(c, dict):
            out.append({"code": c.get("code"), "qty": c.get("qty")})
        else:
            out.append({"code": str(c), "qty": None})
    return out


def _options_with_tradeoff(options: list, explained: dict) -> list[dict]:
    """contracts mục 10: mỗi phương án thêm khóa "tradeoff" – A ← tradeoff_a, B ← tradeoff_b."""
    out = []
    for i, opt in enumerate(options or []):
        opt = dict(opt)
        label = opt.get("option") or ("A" if i == 0 else "B")
        opt["tradeoff"] = explained.get("tradeoff_a" if label == "A" else "tradeoff_b")
        out.append(opt)
    return out


def _save_alerts(run_id: int, drafts: list[dict], explained: dict[str, dict], actor: str,
                 agent_detail: dict) -> list[int]:
    now = _ts(clock.now())
    ids = []
    with _tx() as conn:  # 1 transaction: lưu đủ hết hoặc không lưu gì
        for draft in drafts:
            exp = explained.get(draft.get("ingredient_id"), {})
            alert_id = repo.insert_alert({
                "run_id": run_id,
                "ingredient_id": draft["ingredient_id"],
                "status": "OPEN",
                "main_cause": draft.get("main_cause"),
                "causes": _causes_json(draft.get("causes")),
                "projected_stock": draft.get("projected_stock"),
                "shortage_qty": draft.get("shortage_qty"),
                "options_json": _options_with_tradeoff(draft.get("options"), exp),
                "explanation": exp.get("explanation"),
                # Hash của đúng dữ liệu calc đã dùng để tính cảnh báo, lấy từ AlertDraft (tính trước
                # khi gọi Gemini). KHÔNG tính lại ở đây: nếu kho đổi trong lúc chờ Gemini, hash mới sẽ
                # "hợp thức hóa" dữ liệu đã đổi và create_draft_po không phát hiện được (TC15).
                "snapshot_hash": draft.get("snapshot_hash"),
                "ai_fallback": int(bool(exp.get("ai_fallback", True))),
                "created_at": now,
            }, conn=conn)
            write_audit_log(actor, "ALERT_CREATED", entity_type="alert", entity_id=alert_id, run_id=run_id,
                            detail={"ingredient_id": draft["ingredient_id"], "main_cause": draft.get("main_cause"),
                                    "open_po_id": draft.get("open_po_id"), "note": draft.get("note"),
                                    "ai_fallback": bool(exp.get("ai_fallback", True))},
                            conn=conn)
            ids.append(alert_id)
        superseded = _supersede_old_alerts(run_id, now, conn)
        repo.update_audit_run(run_id, {"status": "SUCCEEDED", "finished_at": now, "alert_count": len(ids)},
                              conn=conn)
        write_audit_log(actor, "RUN_SUCCEEDED", entity_type="audit_run", entity_id=run_id, run_id=run_id,
                        detail={"alert_count": len(ids), "superseded": superseded, **agent_detail}, conn=conn)
    return ids


def _supersede_old_alerts(run_id: int, now: str, conn) -> list[int]:
    """Phiên mới đã tính lại số liệu: cảnh báo OPEN của các phiên trước → DISMISSED.

    Cảnh báo SELECTED (đã có PO đang xử lý) và RESOLVED/DISMISSED giữ nguyên.
    Gọi trong cùng transaction với RUN_SUCCEEDED: phiên thất bại thì không đóng gì.
    """
    closed = []
    for a in repo.get_alerts(status="OPEN", conn=conn):
        if a["run_id"] == run_id:
            continue
        repo.update_alert(a["alert_id"], {"status": "DISMISSED", "resolved_at": now}, conn=conn)
        write_audit_log("system", "ALERT_DISMISSED", entity_type="alert", entity_id=a["alert_id"],
                        run_id=run_id, detail={"reason": "SUPERSEDED", "by_run": run_id}, conn=conn)
        closed.append(a["alert_id"])
    return closed


def _trace_url(trace: list[dict]) -> str | None:
    """Link cây trace LangSmith (giao ước G2) – chỉ có khi LANGSMITH_TRACING=true."""
    return next((t.get("url") for t in trace if t.get("step") == "trace" and t.get("url")), None)


def _summarize_trace(trace: list[dict]) -> dict:
    summary = {
        "tools_called": [t["tool_name"] for t in trace if t.get("step") == "tool_call"],
        "tools_blocked": [t["tool_name"] for t in trace if t.get("step") == "tool_blocked"],
        "fallback": sorted({t.get("reason") for t in trace if t.get("step") == "fallback"}),
    }
    url = _trace_url(trace)
    if url:  # tracing tắt thì không có khóa trace_url (G2)
        summary["trace_url"] = url
    return summary


def _fail(run_id: int, actor: str, error: str, trace_url: str | None = None) -> None:
    detail = {"error": error[:500]}
    if trace_url:
        detail["trace_url"] = trace_url
    try:
        repo.update_audit_run(run_id, {"status": "FAILED", "finished_at": _ts(clock.now()), "error": error[:500]})
        write_audit_log(actor, "RUN_FAILED", entity_type="audit_run", entity_id=run_id, run_id=run_id,
                        detail=detail)
    except Exception:  # noqa: BLE001
        logger.exception("Không ghi được trạng thái FAILED cho phiên %s", run_id)


# ---------------------------------------------------------------------------
# Điểm vào
# ---------------------------------------------------------------------------

def run_morning_audit(trigger: Literal["SCHEDULED", "MANUAL"], rerun: bool = False,
                      user: Any = None, llm: Any = None) -> dict:
    """Rà soát buổi sáng (contracts mục 10). `llm=None` → Gemini thật; test truyền FakeLLM()."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger không hợp lệ: {trigger}. Chỉ nhận SCHEDULED hoặc MANUAL")
    actor = _actor(trigger, user)

    started = _start_run(trigger, rerun, actor)
    if "status" in started:
        return started
    run_id = started["run_id"]
    trace: list[dict] = []

    try:
        mark_delayed_pos(run_id, actor)
        today = clock.today()
        drafts = [dict(d) for d in _compute_alerts(today)]

        if not drafts:
            now = _ts(clock.now())
            with _tx() as conn:  # đóng cảnh báo cũ + SUCCEEDED trong 1 transaction
                superseded = _supersede_old_alerts(run_id, now, conn)
                repo.update_audit_run(run_id, {"status": "SUCCEEDED", "finished_at": now, "alert_count": 0},
                                      conn=conn)
                write_audit_log(actor, "RUN_SUCCEEDED", entity_type="audit_run", entity_id=run_id, run_id=run_id,
                                detail={"alert_count": 0, "superseded": superseded}, conn=conn)
            return _result(run_id, "SUCCEEDED", [], "Rà soát xong: không có nguyên liệu thiếu.")

        # AlertDraft thiếu hash (không đúng contracts mục 5) → tính ngay bây giờ, vẫn TRƯỚC khi gọi Gemini.
        for draft in drafts:
            if not draft.get("snapshot_hash"):
                draft["snapshot_hash"] = repo.compute_snapshot_hash(draft["ingredient_id"])

        explained = run_agent("MORNING_AUDIT", {"run_date": today.isoformat(), "alerts": drafts},
                              llm=llm, trace=trace, meta={"user": actor, "run_id": run_id, "trigger": trigger})
        by_id = {e["ingredient_id"]: e for e in explained.get("alerts", [])}
        ids = _save_alerts(run_id, drafts, by_id, actor, _summarize_trace(trace))
        return _result(run_id, "SUCCEEDED", ids, f"Rà soát xong: {len(ids)} cảnh báo.")
    except Exception as exc:  # noqa: BLE001 – không để lỗi làm treo phiên RUNNING
        logger.exception("Rà soát %s thất bại", run_id)
        _fail(run_id, actor, f"{type(exc).__name__}: {exc}", _trace_url(trace))
        return _result(run_id, "FAILED", [], "Rà soát thất bại, xem chi tiết ở Tab Nhật ký.")


def recover_stale_runs(max_minutes: int = STALE_MINUTES) -> list[int]:
    """Phiên RUNNING bắt đầu quá max_minutes phút → FAILED. Gọi khi app/scheduler khởi động."""
    now = clock.now()
    recovered = []
    for run in repo.get_audit_runs(status="RUNNING"):
        try:
            started = datetime.fromisoformat(run["started_at"])
        except (TypeError, ValueError):
            started = None
        if started is None or now - started > timedelta(minutes=max_minutes):
            _fail(run["run_id"], "system", f"Phiên bị gián đoạn quá {max_minutes} phút (khôi phục khi khởi động)")
            recovered.append(run["run_id"])
    return recovered
