"""
resolver.py — резолвинг вызовов в полные qualname.

Это шаг "Комбинация методов" (2) из описания: чистый AST даёт только
текст вызова ("promo.get" / "DiscountService.calculate" / "self.x").
Резолвер пытается статически превратить это в точный узел графа
(module.Class.method), используя:

  1. импорты файла (from repositories.promo import PromoRepository)
  2. self.* -> методы того же класса
  3. локальные присваивания x = ClassName(...) внутри функции
     (упрощённый аналог того, что даёт mypy/pyright)

Если однозначно резолвнуть не получилось — вызов помечается как
"unresolved" и остаётся кандидатом на уточнение через runtime-трейс
(см. tracer.py), как описано в шаге 7.3 (Runtime tracing).

Логика резолвинга собрана в класс `CallResolver`: экземпляр привязан
к конкретному `ProjectIndex` и умеет резолвить произвольный CallSite
в qualname узла графа. Для обратной совместимости сохранена модульная
функция-обёртка `resolve_call`.
"""

import asyncio
from typing import Optional


class ProjectIndex:
    """Удобная обёртка над результатом indexer.scan_project для поиска."""

    def __init__(self, modules: list):
        self.modules = modules
        # module.Class -> module dotted path (для резолва импортов классов)
        self.class_to_module: dict[str, str] = {}
        # module.function (top level) -> module
        self.func_to_module: dict[str, str] = {}
        # qualname -> FunctionInfo
        self.functions_by_qualname: dict = {}
        # (module, class, method) -> qualname, для быстрого поиска self.method
        self.methods_by_class: dict[tuple[str, str], str] = {}

        for m in modules:
            for c in m.classes:
                self.class_to_module[c.name] = m.module
            for fn in m.functions:
                self.functions_by_qualname[fn.qualname] = fn
                if fn.class_name:
                    self.methods_by_class[(fn.class_name, fn.name)] = fn.qualname
                else:
                    self.func_to_module[fn.name] = m.module

    def module_of(self, module_dotted_or_file: str) -> Optional[object]:
        for m in self.modules:
            if m.module == module_dotted_or_file or m.file == module_dotted_or_file:
                return m
        return None

    def imports_for(self, module_obj) -> dict:
        """Возвращает {локальное_имя: полное_имя} для импортов файла,
        напр. {'DiscountService': 'services.discount.DiscountService',
               'PromoRepository': 'repositories.promo.PromoRepository'}"""
        mapping = {}
        for imp in module_obj.imports:
            if imp["kind"] == "import":
                local = imp["asname"] or imp["module"]
                mapping[local] = imp["module"]
            else:  # from X import Y as Z
                local = imp["asname"] or imp["name"]
                full_module = imp["module"]
                mapping[local] = f"{full_module}.{imp['name']}"
        return mapping


class CallResolver:
    """Резолвинг одного CallSite в qualname узла графа по индексу проекта.

    Каждый экземпляр привязан к конкретному `ProjectIndex`, что позволяет
    резолвить вызовы из любого модуля/функции этого индекса без повторной
    передачи индекса в каждый вызов.
    """

    def __init__(self, idx: ProjectIndex):
        self.idx = idx

    async def resolve_call(self, module_obj, fn, call) -> dict:
        """Пытается резолвнуть один CallSite в qualname узла графа.

        Возвращает dict:
          {"target": "services.discount.DiscountService.calculate",
           "resolved": True/False,
           "reason": "self" | "import+class" | "local_type" | "unresolved"}
        """
        def _resolve_sync():
            imports = self.idx.imports_for(module_obj)

            # 1) self.method(...) -> метод текущего класса
            if call["kind"] == "self_attr" and fn.class_name:
                key = (fn.class_name, call["attr"])
                if key in self.idx.methods_by_class:
                    return {"target": self.idx.methods_by_class[key], "resolved": True, "reason": "self"}

            # 2) ClassName.method(...) где ClassName импортирован
            if call["kind"] == "attr" and call["base"] in imports:
                full = imports[call["base"]]              # напр. services.discount.DiscountService
                cls_name = full.rsplit(".", 1)[-1]
                key = (cls_name, call["attr"])
                if key in self.idx.methods_by_class:
                    return {"target": self.idx.methods_by_class[key], "resolved": True, "reason": "import+class"}

            # 3) переменная с известным локальным типом: promo = PromoRepository(); promo.get(...)
            if call["kind"] == "attr" and call["base"] in fn.local_types:
                cls_name = fn.local_types[call["base"]]
                key = (cls_name, call["attr"])
                if key in self.idx.methods_by_class:
                    return {"target": self.idx.methods_by_class[key], "resolved": True, "reason": "local_type"}
                # тип мог прийти из импорта: promo = PromoRepository(); PromoRepository импортирован как алиас
                if cls_name in imports:
                    real_cls = imports[cls_name].rsplit(".", 1)[-1]
                    key2 = (real_cls, call["attr"])
                    if key2 in self.idx.methods_by_class:
                        return {"target": self.idx.methods_by_class[key2], "resolved": True, "reason": "local_type+import"}

            # 4) вызов свободной функции (не метод), импортированной напрямую
            if call["kind"] == "name" and call["attr"] in self.idx.func_to_module:
                target_module = self.idx.func_to_module[call["attr"]]
                return {"target": f"{target_module}.{call['attr']}", "resolved": True, "reason": "top_level_function"}

            # 5) вызов конструктора: ClassName(...) — определён в проекте
            if call["kind"] == "name" and call["attr"] in self.idx.class_to_module:
                target_module = self.idx.class_to_module[call["attr"]]
                return {"target": f"{target_module}.{call['attr']}.__init__", "resolved": True, "reason": "constructor"}

            return {"target": call["raw"], "resolved": False, "reason": "unresolved"}

        return await asyncio.to_thread(_resolve_sync)


async def resolve_call(idx: ProjectIndex, module_obj, fn, call) -> dict:
    """Обратно-совместимая обёртка над CallResolver.resolve_call."""
    return await CallResolver(idx).resolve_call(module_obj, fn, call)
