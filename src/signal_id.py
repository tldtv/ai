"""
signal_id.py — общая логика идентификации карточки сигнала (fid) и
определения "главной" строки кластера дублей.

Раньше hash_key/card_key были продублированы копипастой в send_digest.py
(с комментарием "проверено на совпадение с JS построчно, если меняете —
меняйте и там"), а сам алгоритм зеркалит функцию hashKey в JS в
build_dashboard.py. Вынесено в отдельный модуль, чтобы:
  - analyze_signals.py (простановка cluster_id при обнаружении дубля),
  - send_digest.py (поиск голосов сигнала в Firestore, отбор дублей
    из рассылки),
  - build_dashboard.py (то же самое на дашборде, только на Python-стороне
    сборки, а не в браузере)
использовали ровно одну реализацию. JS-версия в build_dashboard.py
по-прежнему отдельная (браузеру не отдать этот файл как есть) — если
меняете алгоритм хэша здесь, обязательно поправьте и hashKey в JS.

Про кластеры дублей (cluster_id в data/backlog.csv): у "главной" строки
кластера cluster_id либо пустой (нет дублей), либо равен ЕЁ ЖЕ
собственному fid — а у всех остальных строк, признанных дублями этой
же истории, cluster_id указывает на fid главной. is_primary(row) ниже
проверяет это без обращения к другим строкам — именно поэтому такая
схема и выбрана (не нужно тянуть весь бэклог, чтобы понять, кто на
дашборде/в рассылке главный, а кто дубль).
"""


def _imul32(a, b):
    return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF


def _utf16_code_units(s):
    units = []
    for ch in s:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            units.append(0xD800 + (cp >> 10))
            units.append(0xDC00 + (cp & 0x3FF))
        else:
            units.append(cp)
    return units


def hash_key(s: str) -> str:
    h1 = 0xdeadbeef
    h2 = 0x41c6ce57
    for ch in _utf16_code_units(s):
        h1 = _imul32(h1 ^ ch, 2654435761)
        h2 = _imul32(h2 ^ ch, 1597334677)
    h1 = (_imul32(h1 ^ (h1 >> 16), 2246822507) ^ _imul32(h2 ^ (h2 >> 13), 3266489909)) & 0xFFFFFFFF
    h2 = (_imul32(h2 ^ (h2 >> 16), 2246822507) ^ _imul32(h1 ^ (h1 >> 13), 3266489909)) & 0xFFFFFFFF
    return format(h1, "08x") + format(h2, "08x")


def card_key(row):
    return (row.get("source_url", "").strip() or row.get("title", "")) + "|" + row.get("title", "")


def is_primary(row):
    """True — если строка "главная" в своём кластере дублей (или у неё
    вообще нет дублей). См. пояснение схемы в docstring модуля."""
    cluster_id = (row.get("cluster_id") or "").strip()
    return not cluster_id or cluster_id == hash_key(card_key(row))
