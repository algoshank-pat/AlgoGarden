"""Run tests/scenarios.yaml and report tool accuracy, reply checks and latency.

Usage:
    uv run eval.py [--model gemma4] [--restaurant restaurants/demo_restaurant.yaml]
"""
import argparse
import json
import statistics
import time
import tempfile
import booking_db
from pathlib import Path

import yaml

import receptionist
from receptionist import Receptionist, load_restaurant

HERE = Path(__file__).parent


def main():
    evaluation_db = tempfile.TemporaryDirectory(prefix='receptionist-eval-')
    booking_db.DB_PATH = Path(evaluation_db.name) / 'reservations.sqlite3'
    ap = argparse.ArgumentParser()
    ap.add_argument("--restaurant", default=str(HERE / "restaurants" / "demo_restaurant.yaml"))
    ap.add_argument("--model", default=receptionist.DEFAULT_MODEL)
    args = ap.parse_args()

    # Keep eval side effects out of the real logs, and start each run clean.
    for attr in ("MESSAGES_FILE", "BOOKINGS_FILE", "ORDERS_FILE"):
        path = HERE / f"eval_{getattr(receptionist, attr).name}"
        path.unlink(missing_ok=True)
        setattr(receptionist, attr, path)

    r = load_restaurant(args.restaurant)
    scenarios = yaml.safe_load((HERE / "tests" / "scenarios.yaml").read_text(encoding="utf-8"))

    # Warm-up so model load time is not counted against the first scenario.
    t0 = time.perf_counter()
    Receptionist(r, args.model).turn("Hello")
    print(f"warm-up (model load): {time.perf_counter() - t0:.1f}s\n")

    rows, latencies, tool_ok_n, reply_ok_n = [], [], 0, 0
    for i, sc in enumerate(scenarios, 1):
        bot = Receptionist(r, args.model)
        t0 = time.perf_counter()
        reply, called = bot.turn(sc["caller"])
        dt = time.perf_counter() - t0
        latencies.append(dt)
        expected = sc["expected_tool"]
        tool_ok = (not called) if expected == "none" else (expected in called)
        wanted = sc.get("expect_in_reply") or []
        reply_ok = not wanted or any(w.lower() in reply.lower() for w in wanted)
        tool_ok_n += tool_ok
        reply_ok_n += reply_ok
        rows.append({"caller": sc["caller"], "expected": expected, "called": called,
                     "tool_ok": tool_ok, "reply_ok": reply_ok, "latency_s": round(dt, 2), "reply": reply})
        status = "PASS" if tool_ok and reply_ok else "FAIL"
        print(f"[{i:02d}] {status} {dt:5.1f}s expected={expected} called={called or 'none'}"
              f"{'' if reply_ok else '  (reply check failed)'}")
        print(f"     caller: {sc['caller']}")
        print(f"     reply : {reply}\n")

    n = len(scenarios)
    summary = {"model": args.model,
               "tool_accuracy": f"{tool_ok_n}/{n} ({100 * tool_ok_n / n:.0f}%)",
               "reply_checks": f"{reply_ok_n}/{n}",
               "avg_latency_s": round(statistics.mean(latencies), 2),
               "median_latency_s": round(statistics.median(latencies), 2),
               "max_latency_s": round(max(latencies), 2)}
    print(json.dumps(summary, indent=2))
    out = HERE / f"eval_results_{args.model.replace(':', '_')}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"saved {out.name}")


if __name__ == "__main__":
    main()
