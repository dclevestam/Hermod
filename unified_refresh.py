"""Helpers for unified mailbox refresh fan-out."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone


_UNIFIED_MAX_WORKERS = 4


@dataclass(frozen=True)
class UnifiedFetchSpec:
    label: str
    fetch: object


def _normalized_sort_datetime(value):
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def run_bounded_calls(callables, max_workers=_UNIFIED_MAX_WORKERS):
    tasks = list(callables or [])
    if not tasks:
        return []
    worker_count = max(1, min(int(max_workers or 1), len(tasks)))
    results = [(None, None) for _ in tasks]
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(task): index
            for index, task in enumerate(tasks)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                results[index] = (future.result(), None)
            except Exception as exc:
                results[index] = (None, exc)
    return results


def collect_unified_messages(
    fetch_specs,
    transient_error_fn,
    network_ready_fn,
    error_logger=None,
    max_workers=_UNIFIED_MAX_WORKERS,
    limit=100,
):
    specs = list(fetch_specs or [])
    if not specs:
        return {
            'messages': [],
            'had_transient_error': False,
        }
    results = run_bounded_calls([spec.fetch for spec in specs], max_workers=max_workers)
    all_messages = []
    had_transient_error = False
    for spec, (messages, exc) in zip(specs, results):
        if exc is not None:
            if transient_error_fn(exc) or not network_ready_fn():
                had_transient_error = True
                continue
            if callable(error_logger):
                error_logger(spec.label, exc)
            continue
        all_messages.extend(list(messages or []))
    all_messages.sort(
        key=lambda item: _normalized_sort_datetime(item.get('date') if isinstance(item, dict) else None),
        reverse=True,
    )
    if limit is not None:
        all_messages = all_messages[:max(0, int(limit))]
    return {
        'messages': all_messages,
        'had_transient_error': had_transient_error,
    }
