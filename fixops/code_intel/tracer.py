"""
tracer.py — runtime-трассировка вызовов (шаг 7.3).

Статический анализ не может однозначно резолвить динамические вызовы
(service.run(), где service может быть чем угодно). Здесь мы пишем
реальный путь выполнения во время работы приложения и связываем его
с trace_id/request_id — тем же идентификатором, что есть в логах.

Использование:

    from code_intel.tracer import RuntimeTracer

    tracer = RuntimeTracer(project_root=".", log_path="logs/runtime_trace.jsonl")

    with tracer.trace(trace_id="abc123"):
        PaymentService().pay(order)

    events = tracer.events   # список записанных вызовов для этого trace_id

Технически используется sys.settrace: перехватываются события "call"
для функций, определённых внутри project_root, и строится реальная
цепочка caller -> callee с номером строки вызова.

Через параметр `ignore_modules` можно исключить кадры инфраструктуры
(например "core.logging" / "core.decorators" — логирование приложения),
чтобы они не засоряли граф. При определении вызывающего такие кадры
пропускаются: вызывающий ищется выше по стеку.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import contextmanager


class RuntimeTracer:
    def __init__(self, project_root: str, log_path: str | None = None, ignore_modules: set | None = None):
        self.project_root = os.path.abspath(project_root)
        self.log_path = log_path
        # Модули (по dotted-пути), кадры которых не попадают в трейс —
        # например "core.logging" / "core.decorators" из инфраструктуры логирования.
        self.ignore_modules: set = set(ignore_modules or ())
        self.events: list[dict] = []
        self._stack: list[dict] = []
        self._trace_id: str | None = None

    def _qualname_for_frame(self, frame) -> str | None:
        filename = frame.f_globals.get("__file__")
        if not filename:
            return None
        filename = os.path.abspath(filename)
        if not filename.startswith(self.project_root):
            return None  # игнорируем стандартную библиотеку / сторонние пакеты
        rel = os.path.relpath(filename, self.project_root)
        module = rel[:-3].replace(os.sep, ".") if rel.endswith(".py") else rel
        if any(module.startswith(ignored) for ignored in self.ignore_modules):
            return None  # кадры инфраструктуры логирования не трейсим

        func_name = frame.f_code.co_name
        # пытаемся понять, метод ли это класса, по self/cls в локалах
        cls_name = None
        first_arg = frame.f_code.co_varnames[0] if frame.f_code.co_argcount > 0 else None
        if first_arg in ("self", "cls") and first_arg in frame.f_locals:
            obj = frame.f_locals[first_arg]
            cls_name = obj.__class__.__name__ if first_arg == "self" else obj.__name__
        if cls_name:
            return f"{module}.{cls_name}.{func_name}"
        return f"{module}.{func_name}"

    def _nearest_caller_qualname(self, frame):
        """Поднимается по стеку вызовов вверх, пропуская игнорируемые кадры
        (например инфраструктуру логирования core.decorators/core.logging),
        и возвращает qualname ближайшего трейсуемого вызывающего."""
        caller = frame.f_back
        while caller is not None:
            caller_q = self._qualname_for_frame(caller)
            if caller_q is not None:
                return caller_q
            caller = caller.f_back
        return None

    def _trace_calls(self, frame, event, arg):
        if event == "call":
            callee_q = self._qualname_for_frame(frame)
            if callee_q is not None:
                caller_q = self._nearest_caller_qualname(frame)
                caller_q = caller_q or "<entrypoint>"
                self.events.append({
                    "trace_id": self._trace_id,
                    "caller": caller_q,
                    "callee": callee_q,
                    "file": os.path.relpath(frame.f_code.co_filename, self.project_root).replace(os.sep, "/"),
                    "line": frame.f_lineno,
                    "ts": time.time(),
                })
        return self._trace_calls

    @contextmanager
    def trace(self, trace_id: str | None = None):
        self._trace_id = trace_id or str(uuid.uuid4())
        old_trace = sys.gettrace()
        sys.settrace(self._trace_calls)
        try:
            yield self._trace_id
        finally:
            sys.settrace(old_trace)
            if self.log_path:
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    for ev in self.events:
                        if ev["trace_id"] == self._trace_id:
                            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def events_for(self, trace_id: str) -> list[dict]:
        return [e for e in self.events if e["trace_id"] == trace_id]