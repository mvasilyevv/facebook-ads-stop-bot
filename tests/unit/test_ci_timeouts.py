# -*- coding: utf-8 -*-
"""У каждой джобы проверки есть свой предел времени.

18.08.2026 джоба platform зависла на `apt-get install` и шла 29 минут — её
остановил человек, а не пайплайн. Дефолт GitHub — 360 минут: без явного
timeout-minutes сетевой затык стоит рабочего дня, а не пары минут.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_VERIFY = ROOT / ".github/workflows/verify.yml"

# Потолок вдвое выше самого медленного наблюдённого прогона: он ловит зависание,
# но не рубит честную джобу на холодном кэше.
_MAX_TIMEOUT_MINUTES = 40


def _verify_jobs() -> dict:
    document = yaml.safe_load(_VERIFY.read_text(encoding="utf-8"))
    return document["jobs"]


def test_every_verify_job_declares_a_timeout() -> None:
    missing = [name for name, job in _verify_jobs().items() if "timeout-minutes" not in job]
    assert missing == [], (
        "джобы без timeout-minutes: "
        + ", ".join(missing)
        + " — без предела зависший шаг идёт до лимита раннера в 360 минут"
    )


def test_verify_timeouts_stay_tight() -> None:
    too_long = {
        name: job["timeout-minutes"]
        for name, job in _verify_jobs().items()
        if int(job.get("timeout-minutes", 0)) > _MAX_TIMEOUT_MINUTES
    }
    assert too_long == {}, (
        f"предел выше {_MAX_TIMEOUT_MINUTES} минут не ловит зависание: {too_long}"
    )
