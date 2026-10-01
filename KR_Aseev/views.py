"""КР (Асеев Н.С.), тема 17: мини-дашборд в стиле Grafana.

Тема: разработка паттерна для обеспечения безопасности приложения при угрозе
потери информации вследствие несогласованности работы узлов хранилища больших данных.

Пользователь вводит в строки формы почасовые показатели кластера хранилища
(5 узлов), сервер Django оценивает риск потери данных и выбирает реакцию
паттерна «кворумная запись + журнал WAL + сверка реплик», а страница
index.html рисует по этим расчётам графики и таблицы.
"""
import statistics

from django.http import JsonResponse
from django.shortcuts import render

NODES_TOTAL = 5                  # узлов в кластере хранилища
QUORUM = NODES_TOTAL // 2 + 1    # кворум — большинство узлов: 3 из 5

# Строки формы: что вводит пользователь (подпись, единица, подсказка, пример за 8 часов)
METRICS = {
    "ops": {
        "label": "Поток записи в хранилище",
        "unit": "тыс. оп/мин",
        "hint": "сколько тысяч операций записи в минуту принимал кластер в этот час",
        "default": "12, 18, 26, 34, 41, 38, 30, 22",
    },
    "lag": {
        "label": "Задержка репликации",
        "unit": "с",
        "hint": "на сколько секунд реплики отставали от основного узла",
        "default": "1.2, 1.5, 2.8, 6.5, 16, 9, 3.1, 1.8",
    },
    "conflicts": {
        "label": "Рассогласованные записи",
        "unit": "шт.",
        "hint": "сколько записей с разными версиями на узлах нашла сверка контрольных сумм за час",
        "default": "4, 6, 15, 80, 420, 260, 40, 9",
    },
    "nodes": {
        "label": f"Узлы хранилища в строю (из {NODES_TOTAL})",
        "unit": "шт.",
        "hint": f"сколько узлов отвечали на запросы; для кворумной записи нужно не меньше {QUORUM}",
        "default": "5, 5, 5, 4, 3, 4, 5, 5",
    },
}
START_HOUR = 9      # первый час на графиках — 09:00
MAX_POINTS = 24     # не больше суток

# Пороги, как thresholds в Grafana: (предупреждение, критично)
THRESHOLDS = {
    "lag": (5, 15),             # с
    "conflicts": (50, 300),     # рассогласованных записей за час
    "at_risk": (2000, 10000),   # записей под угрозой потери
}

# Реакция паттерна на состояние кластера
ACTIONS = {
    "ok": "Штатный режим: кворумная запись W = 3, фоновая сверка реплик",
    "warn": "Усиленная сверка реплик (read repair), оповещение дежурного",
    "crit": "Аварийная синхронизация реплик, ограничение потока записи",
    "no_quorum": "Кворум потерян: запись приостановлена, операции копятся в журнале WAL",
}


def parse_numbers(raw):
    """Строка «1.5, 2 3; 4» -> [1.5, 2.0, 3.0, 4.0]."""
    numbers = []
    for part in raw.replace(";", " ").replace(",", " ").split():
        try:
            numbers.append(float(part))
        except ValueError:
            raise ValueError(f"«{part}» — не число") from None
    return numbers


def level(value, warn, crit):
    """Статус по порогам: ok / warn / crit."""
    if value >= crit:
        return "crit"
    if value >= warn:
        return "warn"
    return "ok"


def nodes_level(nodes):
    """Кворум: узлов больше кворума — ok, ровно кворум — warn (нет запаса), меньше — crit."""
    if nodes < QUORUM:
        return "crit"
    if nodes == QUORUM:
        return "warn"
    return "ok"


def index(request):
    """Представление КР: возвращает страницу index.html с дашбордом и строками ввода."""
    rows = [{"key": key, **meta} for key, meta in METRICS.items()]
    context = {"rows": rows, "start_hour": START_HOUR, "nodes_total": NODES_TOTAL, "quorum": QUORUM}
    return render(request, "KR_Aseev/index.html", context)


def metrics_api(request):
    """API для веб-интерфейса: принимает строки чисел, возвращает расчёты и реакцию паттерна (JSON)."""
    series = {}
    try:
        for key, meta in METRICS.items():
            values = parse_numbers(request.GET.get(key, ""))
            if not values:
                raise ValueError(f"строка «{meta['label']}» пустая")
            series[key] = values
        start = int(request.GET.get("start", START_HOUR))
    except ValueError as exc:
        return error(f"Проверьте данные: {exc}.")

    counts = {METRICS[key]["label"]: len(values) for key, values in series.items()}
    if len(set(counts.values())) != 1:
        details = "; ".join(f"{name} — {count}" for name, count in counts.items())
        return error("В каждой строке должно быть одинаковое количество чисел "
                     f"(по одному на час). Сейчас: {details}.")
    hours_count = len(series["ops"])
    if hours_count > MAX_POINTS:
        return error(f"Не больше {MAX_POINTS} значений в строке (одни сутки).")
    if any(value < 0 for values in series.values() for value in values):
        return error("Значения не могут быть отрицательными.")
    if any(value > NODES_TOTAL or value != int(value) for value in series["nodes"]):
        return error(f"Узлы в строю — целое число от 0 до {NODES_TOTAL}.")
    if not 0 <= start <= 23:
        return error("Начало периода — час от 0 до 23.")

    labels = [f"{(start + i) % 24:02d}:00" for i in range(hours_count)]
    hours = []
    for i in range(hours_count):
        ops, lag = series["ops"][i], series["lag"][i]
        conflicts, nodes = series["conflicts"][i], int(series["nodes"][i])
        writes = ops * 1000 * 60                  # операций записи за час
        at_risk = round(ops * 1000 / 60 * lag)    # записаны, но ещё не скопированы на реплики
        statuses = {
            level(lag, *THRESHOLDS["lag"]),
            level(conflicts, *THRESHOLDS["conflicts"]),
            level(at_risk, *THRESHOLDS["at_risk"]),
            nodes_level(nodes),
        }
        status = "crit" if "crit" in statuses else "warn" if "warn" in statuses else "ok"
        action = ACTIONS["no_quorum"] if nodes < QUORUM else ACTIONS[status]
        hours.append({
            "time": labels[i], "ops": ops, "lag": lag, "conflicts": conflicts, "nodes": nodes,
            "writes": writes, "at_risk": at_risk, "status": status, "action": action,
        })

    at_risk_series = [hour["at_risk"] for hour in hours]
    table = [metric_row(meta["label"], meta["unit"], series[key]) for key, meta in METRICS.items()]
    table.append(metric_row("Записи под угрозой потери", "шт.", at_risk_series))

    total_writes = sum(hour["writes"] for hour in hours)
    total_conflicts = sum(series["conflicts"])
    summary = {
        "hours": hours_count,
        "total_writes": round(total_writes),
        "avg_lag": round(statistics.mean(series["lag"]), 1),
        "total_conflicts": round(total_conflicts),
        "max_conflicts": max(series["conflicts"]),
        "consistency": round(100 - total_conflicts / total_writes * 100, 4) if total_writes else 100.0,
        "min_nodes": int(min(series["nodes"])),
        "peak_at_risk": max(at_risk_series),
        "problem_hours": sum(1 for hour in hours if hour["status"] != "ok"),
    }
    return JsonResponse(
        {"ok": True, "labels": labels, "series": series, "at_risk": at_risk_series,
         "hours": hours, "table": table, "summary": summary, "thresholds": THRESHOLDS,
         "cluster": {"nodes_total": NODES_TOTAL, "quorum": QUORUM}},
        json_dumps_params={"ensure_ascii": False},
    )


def metric_row(label, unit, values):
    """Строка таблицы статистики: минимум, среднее, максимум, последний час и тренд."""
    first, last = values[0], values[-1]
    return {
        "metric": label,
        "unit": unit,
        "min": min(values),
        "avg": round(statistics.mean(values), 1),
        "max": max(values),
        "last": last,
        "trend": round((last - first) / first * 100, 1) if first else 0.0,
    }


def error(message):
    """Ответ API с текстом ошибки для показа пользователю."""
    return JsonResponse({"ok": False, "error": message}, status=400,
                        json_dumps_params={"ensure_ascii": False})
