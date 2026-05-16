"""Денойз сырых событий записи → list[UserAction]."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ActionKind = Literal["click", "fill", "select", "key", "submit", "marker"]


@dataclass(frozen=True)
class UserAction:
    kind: ActionKind
    selectors: tuple[str, ...]
    value: str | None
    label: str | None
    section: str | None
    ts: float
    raw_indices: tuple[int, ...]
    widget: dict | None = None
    opened_after: tuple[str, ...] = ()


_CLICK_WINDOW_S = 0.2
_KEY_SUBMITS = {"Enter", "Escape", "Tab"}


def _label_for(event: dict) -> str | None:
    return event.get("label_text") or event.get("aria_label") or (event.get("text") or None)


def _selectors_for(event: dict) -> tuple[str, ...]:
    cands = event.get("selector_candidates") or []
    return tuple(str(s) for s in cands if s)


def denoise(events: list[dict]) -> list[UserAction]:
    """Свёртка сырых событий в значимые действия."""
    actions: list[UserAction] = []
    i = 0
    n = len(events)
    pending_fill: dict[str, list[int]] = {}

    def flush_fill(xpath: str) -> None:
        idxs = pending_fill.pop(xpath, [])
        if not idxs:
            return
        last = events[idxs[-1]]
        selectors = _selectors_for(last)
        actions.append(
            UserAction(
                kind="fill",
                selectors=selectors,
                value=None if last.get("value") is None else str(last["value"]),
                label=_label_for(last),
                section=last.get("nearest_heading"),
                ts=float(last.get("ts") or 0),
                raw_indices=tuple(idxs),
            )
        )

    while i < n:
        e = events[i]
        kind = e.get("type")
        xpath = e.get("xpath") or ""

        if kind == "marker":
            for xk in list(pending_fill.keys()):
                flush_fill(xk)
            label = e.get("value")
            actions.append(
                UserAction(
                    kind="marker",
                    selectors=(),
                    value=None,
                    label=str(label) if label is not None else None,
                    section=None,
                    ts=float(e.get("ts") or 0),
                    raw_indices=(i,),
                )
            )
            i += 1
            continue

        if kind in ("pointerdown", "mousedown", "click"):
            for xk in list(pending_fill.keys()):
                flush_fill(xk)
            group = [i]
            j = i + 1
            click_idx: int | None = i if kind == "click" else None
            while j < n:
                ne = events[j]
                if ne.get("xpath") != xpath:
                    break
                if (float(ne.get("ts") or 0) - float(e.get("ts") or 0)) > _CLICK_WINDOW_S:
                    break
                if ne.get("type") not in ("pointerdown", "mousedown", "click"):
                    break
                group.append(j)
                if ne.get("type") == "click":
                    click_idx = j
                j += 1
            if click_idx is not None:
                src = events[click_idx]
            else:
                # FB-листбоксы закрываются на mousedown без последующего click —
                # такой жест тоже значимое действие. Берём последний pointerdown/
                # mousedown с непустыми селекторами/текстом как источник.
                src = None
                for idx in reversed(group):
                    cand = events[idx]
                    if cand.get("type") in ("pointerdown", "mousedown") and (
                        _selectors_for(cand) or (cand.get("text") or "").strip()
                    ):
                        src = cand
                        break
            if src is not None:
                selectors = _selectors_for(src)
                text = (src.get("text") or "").strip()
                if selectors or text:
                    opened = src.get("opened_after") or []
                    actions.append(
                        UserAction(
                            kind="click",
                            selectors=selectors,
                            value=None,
                            label=_label_for(src),
                            section=src.get("nearest_heading"),
                            ts=float(src.get("ts") or 0),
                            raw_indices=tuple(group),
                            widget=src.get("widget"),
                            opened_after=tuple(str(x) for x in opened if x),
                        )
                    )
            i = j
            continue

        if kind == "input":
            pending_fill.setdefault(xpath, []).append(i)
            i += 1
            continue

        if kind == "change":
            tag = (e.get("tag") or "").lower()
            if tag == "select":
                for xk in list(pending_fill.keys()):
                    flush_fill(xk)
                actions.append(
                    UserAction(
                        kind="select",
                        selectors=_selectors_for(e),
                        value=None if e.get("value") is None else str(e["value"]),
                        label=_label_for(e),
                        section=e.get("nearest_heading"),
                        ts=float(e.get("ts") or 0),
                        raw_indices=(i,),
                    )
                )
            else:
                if xpath in pending_fill:
                    pending_fill[xpath].append(i)
                    flush_fill(xpath)
                else:
                    pending_fill.setdefault(xpath, []).append(i)
                    flush_fill(xpath)
            i += 1
            continue

        if kind == "keydown":
            key = e.get("value")
            is_last = not any(ev.get("xpath") == xpath for ev in events[i + 1 :])
            if key in _KEY_SUBMITS and is_last:
                for xk in list(pending_fill.keys()):
                    flush_fill(xk)
                actions.append(
                    UserAction(
                        kind="key",
                        selectors=_selectors_for(e),
                        value=str(key),
                        label=_label_for(e),
                        section=e.get("nearest_heading"),
                        ts=float(e.get("ts") or 0),
                        raw_indices=(i,),
                    )
                )
            i += 1
            continue

        if kind == "submit":
            for xk in list(pending_fill.keys()):
                flush_fill(xk)
            actions.append(
                UserAction(
                    kind="submit",
                    selectors=_selectors_for(e),
                    value=None,
                    label=_label_for(e),
                    section=e.get("nearest_heading"),
                    ts=float(e.get("ts") or 0),
                    raw_indices=(i,),
                )
            )
            i += 1
            continue

        i += 1

    for xk in list(pending_fill.keys()):
        flush_fill(xk)

    return actions


def analyze_session(session: dict) -> dict:
    events: list[dict] = session.get("events", [])
    actions = denoise(events)
    return {
        "offer_code": session.get("offer_code", ""),
        "raw_events_count": len(events),
        "actions_count": len(actions),
        "actions": [
            {
                "kind": a.kind,
                "selectors": list(a.selectors),
                "value": a.value,
                "label": a.label,
                "section": a.section,
                "ts": a.ts,
                "widget": a.widget,
                "opened_after": list(a.opened_after),
            }
            for a in actions
        ],
    }


def analyze_session_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return analyze_session(data)
