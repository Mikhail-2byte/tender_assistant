"""Repeatable, local-only Qt measurements; all writes use a temporary database.

Run as a module. Timings exclude cProfile unless --profile is explicitly used.
Reports contain counts and timings, never tender contents or source paths.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import cProfile
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import math

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
IMPORT_STARTED = time.perf_counter()

import psutil
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from shiboken6 import getAllValidWrappers

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.settings import Settings


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def qt_object_count():
    # findChildren(QObject) creates wrappers for C++ internal Qt objects and can
    # change retention during the measurement. Count already existing wrappers.
    return sum(isinstance(obj, QObject) for obj in getAllValidWrappers())


def multiply_demo(connection, scale):
    """Clone fictional relational data, retaining foreign keys and row ordering."""
    tables = ("tenders", "execution_specs", "execution_positions", "execution_history", "execution_comments")
    rows = {table: list(connection.execute(f"SELECT * FROM {table}")) for table in tables}
    strides = {table: max((row["id"] for row in values), default=0) + 1 for table, values in rows.items()}
    links = {"tender_id": "tenders", "spec_id": "execution_specs", "position_id": "execution_positions"}
    with connection:
        for replica in range(1, scale):
            for table in tables:
                for original in rows[table]:
                    values = dict(original)
                    values["id"] += replica * strides[table]
                    for column, target in links.items():
                        if column in values and values[column] is not None:
                            values[column] += replica * strides[target]
                    if table == "tenders":
                        values["number"] += f"-demo-{replica}"
                        values["folder_path"] = ""
                    columns = ",".join(values)
                    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in values)})", list(values.values()))


def match_demo_volume(connection, source_path):
    """Use only source row counts; all values come from fictional demo records."""
    tables = ("tenders", "execution_specs", "execution_positions", "execution_history", "execution_comments")
    with closing(sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        counts = {table: source.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
        statuses = [status for status, count in sorted(source.execute(
            "SELECT status, count(*) FROM tenders GROUP BY status"), key=lambda row: row[0] != "Победа")
                    for _ in range(count)]
    templates = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}
    position_specs = {}
    with connection:
        for table in reversed(tables):
            connection.execute(f"DELETE FROM {table}")
        for table in tables:
            for index in range(counts[table]):
                values = templates[table][index % len(templates[table])].copy()
                values["id"] = index + 1
                if table == "tenders":
                    values.update(number=f"DEMO-{index + 1}", folder_path="", url="", status=statuses[index])
                elif table == "execution_specs":
                    values.update(tender_id=index % counts["tenders"] + 1, number=f"SPEC-{index + 1}",
                                  contract=f"DEMO-CONTRACT-{index // 2}")
                    connection.execute("UPDATE tenders SET status='Победа' WHERE id=?", (values["tender_id"],))
                elif table == "execution_positions":
                    values.update(spec_id=index % counts["execution_specs"] + 1,
                                  ordinal=index // counts["execution_specs"])
                    position_specs[values["id"]] = values["spec_id"]
                elif table == "execution_history":
                    values["spec_id"] = index % counts["execution_specs"] + 1
                else:
                    values["spec_id"] = index % counts["execution_specs"] + 1
                    values["position_id"] = (index % counts["execution_positions"] + 1
                                             if values["position_id"] is not None and counts["execution_positions"] else None)
                    if values["position_id"] is not None:
                        values["spec_id"] = position_specs[values["position_id"]]
                connection.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                                   list(values.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--match-volume", type=Path, help="Fictional data with only row counts taken from this DB")
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--idle-seconds", type=float, default=15)
    parser.add_argument("--active-seconds", type=float, default=30)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--document-kit", action="store_true", help="Include the fictional import folder and Excel blank")
    args = parser.parse_args()
    if args.scale < 1 or min(args.idle_seconds, args.active_seconds) < 0:
        parser.error("Scale must be positive and durations nonnegative")
    if args.source_db and args.scale != 1:
        parser.error("Scaling is allowed only for fictional data")
    if args.source_db and args.match_volume:
        parser.error("Choose a real copy or fictional volume matching")
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    process = psutil.Process()
    result = {"source": "working-copy" if args.source_db else "fictional", "scale": args.scale,
              "profiled": args.profile, "qt_platform": app.platformName(),
              "module_init_ms": (time.perf_counter() - IMPORT_STARTED) * 1000,
              "document_kit": args.document_kit, "logical_cpus": psutil.cpu_count(),
              "object_metric": "existing-python-qobject-wrappers",
              "idle_seconds_requested": args.idle_seconds, "active_seconds_requested": args.active_seconds,
              "samples": [], "cycle_samples": [], "errors": []}
    actions = defaultdict(list)
    lag = defaultdict(list)
    sql = [0]
    profile = cProfile.Profile() if args.profile else None
    with tempfile.TemporaryDirectory(prefix="tender-perf-") as temporary:
        root = Path(temporary)
        path = root / "tenders.db"
        if args.source_db:
            # SQLite backup includes committed WAL data without writing the source.
            with closing(sqlite3.connect(args.source_db.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(path)) as target:
                    source.backup(target)
        else:
            create_demo_database(path)
        started = time.perf_counter()
        repository = ExecutionRepository(path)
        if args.match_volume:
            match_demo_volume(repository.connection, args.match_volume)
        if args.scale > 1:
            multiply_demo(repository.connection, args.scale)
        result["counts"] = {table: repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                            for table in ("tenders", "execution_specs", "execution_positions", "execution_comments")}
        repository.connection.set_trace_callback(lambda statement: sql.__setitem__(0, sql[0] + 1))
        if profile:
            profile.enable()
        window = ExecutionWindow(repository, Settings(root / "settings.json"), root / "documents")
        window.resize(1366, 768)
        window.show()
        app.processEvents()
        result["startup_ms"] = (time.perf_counter() - started) * 1000
        result["startup_sql"] = sql[0]
        specs = repository.specifications()
        tenders = repository.all()
        # No resource synchronization, external URLs, real model calls or credentials.
        steps = [
            ("registry", lambda: window.pages.setCurrentIndex(0)),
            ("search", lambda: window.registry.search.setText("test-no-match")),
            ("search_clear", lambda: window.registry.search.clear()),
            ("sort", lambda: window.registry.table.sortItems(1)),
            ("scroll", lambda: window.registry.table.verticalScrollBar().setValue(200)),
            ("execution", lambda: window.pages.setCurrentIndex(1)),
        ]
        if specs:
            steps.extend([
                ("first_specification", lambda: window.open_specification(specs[0].id)),
                ("specification", lambda: window.open_specification(specs[-1].id)),
            ])
        if tenders:
            steps.extend([
                ("tender_card", lambda: window.open_registry_tender(tenders[0].id)),
                ("registry_return", window.return_to_registry),
            ])
        steps.extend([("dock_open", window.toggle_agent_dock), ("dock_close", window.agent_dock.hide),
                      ("refresh", window.refresh_execution)])
        # Save/import occur once, on the disposable copy, before the timed loop.
        if tenders:
            mark = time.perf_counter()
            repository.save(replace(tenders[0], action="Local performance check"))
            result["save_ms"] = (time.perf_counter() - mark) * 1000
        text = "1\t01.08.2026\t10.08.2026 12:30:07\t10\tPERF-FICTIONAL-UNIQUE\tУчебный заказчик\tТендер\t\tНет\tВ работе\tПоставка\t\tПроверка"
        mark = time.perf_counter()
        plan = repository.preview_1c(text)
        documents = root / "documents"
        documents.mkdir(exist_ok=True)
        repository.import_1c(plan, [row.ordinal for row in plan.rows if row.category == "new"],
                             documents if args.document_kit else None)
        result["import_ms"] = (time.perf_counter() - mark) * 1000
        window.refresh_execution()
        phase = "warmup"
        phase_started = time.monotonic()
        previous_beat = phase_started
        active_started = None
        index = 0
        last_progress = time.monotonic()
        action_queries = defaultdict(list)
        process.cpu_percent()
        last_sample = time.monotonic()
        driver = QObject()
        beat_timer = QTimer(driver)
        sample_timer = QTimer(driver)
        action_timer = QTimer(driver)

        def sample():
            nonlocal last_sample, last_progress
            memory = process.memory_info()
            io = process.io_counters()
            children = process.children(recursive=True)
            result["samples"].append({
                "phase": phase, "seconds": round(time.monotonic() - phase_started, 2),
                "cpu_machine_percent": process.cpu_percent() / (psutil.cpu_count() or 1),
                "rss": memory.rss, "private": getattr(memory, "private", memory.vms),
                "handles": process.num_handles() if os.name == "nt" else process.num_fds(),
                "threads": process.num_threads(), "qobjects": qt_object_count(),
                "read_bytes": io.read_bytes, "write_bytes": io.write_bytes,
                "children": len(children), "child_rss": sum(p.memory_info().rss for p in children if p.is_running()),
                "ai_jobs": len(window.ai_service.jobs), "resource_job": getattr(window, "resource_job", None) is not None,
                "interval_seconds": time.monotonic() - last_sample,
            })
            last_sample = time.monotonic()
            if last_sample - last_progress >= 60:
                checkpoint = {"phase": phase, "phase_seconds": round(last_sample - phase_started),
                              "actions": index, "last_sample": result["samples"][-1]}
                (args.output / "progress.json").write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
                print(json.dumps(checkpoint), flush=True)
                last_progress = last_sample

        def beat():
            nonlocal previous_beat, phase, phase_started, active_started
            now = time.monotonic()
            lag[phase].append(max(0, (now - previous_beat) * 1000 - 50))
            previous_beat = now
            elapsed = now - phase_started
            if phase == "warmup" and elapsed >= 3:
                phase, phase_started = "idle", now
                process.cpu_percent()
            elif phase == "idle":
                if elapsed >= args.idle_seconds / 2 and not window.isMinimized():
                    window.showMinimized()
                if elapsed >= args.idle_seconds:
                    phase, phase_started, active_started = "active", now, now
                    window.showNormal()
                    action_timer.start(100)
            elif phase == "active" and now - active_started >= args.active_seconds:
                action_timer.stop()
                sample()
                app.quit()

        def action():
            nonlocal index
            name, callback = steps[index % len(steps)]
            mark, count = time.perf_counter(), sql[0]
            try:
                callback()
                # Include paint/layout work in the action latency.
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
            except Exception as error:
                result["errors"].append(type(error).__name__)
                app.quit()
            actions[name].append((time.perf_counter() - mark) * 1000)
            action_queries[name].append(sql[0] - count)
            index += 1
            if index % len(steps) == 0:
                memory = process.memory_info()
                result["cycle_samples"].append({
                    "seconds": time.monotonic() - active_started, "rss": memory.rss,
                    "private": getattr(memory, "private", memory.vms),
                    "handles": process.num_handles() if os.name == "nt" else process.num_fds(),
                    "qobjects": qt_object_count(), "ai_jobs": len(window.ai_service.jobs),
                    "resource_job": getattr(window, "resource_job", None) is not None})

        import sys
        old_hook = sys.excepthook
        def failed(kind, error, traceback):
            result["errors"].append(kind.__name__)
            app.quit()
        sys.excepthook = failed
        beat_timer.timeout.connect(beat)
        sample_timer.timeout.connect(sample)
        action_timer.timeout.connect(action)
        beat_timer.start(50)
        sample_timer.start(1000)
        try:
            app.exec()
        finally:
            for timer in (beat_timer, sample_timer, action_timer):
                timer.stop()
            sys.excepthook = old_hook
            window.dirty = False
            window.execution.comment_drafts.clear()
            mark = time.perf_counter()
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            result["close_ms"] = (time.perf_counter() - mark) * 1000
            result["remaining_children"] = len(process.children(recursive=True))
            repository.close()
            if profile:
                profile.disable()
                profile.dump_stats(str(args.output / "profile.pstats"))
        result["actions"] = {name: {"count": len(values), "p50_ms": percentile(values, .5),
                                    "p95_ms": percentile(values, .95), "max_ms": max(values),
                                    "sql_mean": sum(action_queries[name]) / len(values)} for name, values in actions.items()}
        result["event_lag"] = {name: {"p95_ms": percentile(values, .95), "max_ms": max(values)} for name, values in lag.items()}
        result["resources"] = {}
        for name in ("idle", "active"):
            samples = [s for s in result["samples"] if s["phase"] == name and s["seconds"] >= 2]
            if samples:
                duration = sum(s["interval_seconds"] for s in samples)
                result["resources"][name] = {
                    "cpu_machine_mean": sum(s["cpu_machine_percent"] * s["interval_seconds"] for s in samples) / duration,
                    "cpu_machine_peak": max(s["cpu_machine_percent"] for s in samples),
                    "rss_peak": max(s["rss"] for s in samples), "private_peak": max(s["private"] for s in samples),
                    "read_bytes_delta": samples[-1]["read_bytes"] - samples[0]["read_bytes"],
                    "write_bytes_delta": samples[-1]["write_bytes"] - samples[0]["write_bytes"],
                    "children_peak": max(s["children"] for s in samples),
                }
        tail = [s for s in result["cycle_samples"] if s["seconds"] >= max(10, args.active_seconds - 1200)]
        if len(tail) >= 2:
            result["stability"] = {key + "_growth": tail[-1][key] - tail[0][key]
                                   for key in ("rss", "private", "handles", "qobjects")}
            result["stability"]["seconds"] = tail[-1]["seconds"] - tail[0]["seconds"]
            result["stability"]["within_growth_limits"] = (result["stability"]["rss_growth"] <= 10 * 1048576
                and result["stability"]["private_growth"] <= 10 * 1048576
                and result["stability"]["handles_growth"] <= 20
                and result["stability"]["qobjects_growth"] <= 0)
        result["acceptance"] = {
            "idle_cpu_under_one_percent": (result["resources"]["idle"]["cpu_machine_mean"] <= 1
                                           if "idle" in result["resources"] else None),
            "actions_under_200ms": {name: values["p95_ms"] <= 200 for name, values in result["actions"].items()},
            "long_durations_requested": args.idle_seconds >= 600 and args.active_seconds >= 1800,
            "no_children_at_close": result["remaining_children"] == 0,
        }
        result["completed"] = not result["errors"] and phase == "active" and time.monotonic() - active_started >= args.active_seconds
        (args.output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        window = None
    print(json.dumps({key: value for key, value in result.items() if key not in ("samples", "cycle_samples")}, indent=2))
    return 0 if result["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
