# -*- coding: utf-8 -*-
"""У каждой джобы релизной поверхности есть свой предел времени.

18.08.2026 джоба platform зависла на `apt-get install` и шла 29 минут — её
остановил человек, а не пайплайн. 19.08.2026 предел уже стоял и срубил тот же
затык за десять минут, но проверял этот гард только verify.yml: предел был у 5
джоб из 15. Дефолт GitHub — 360 минут, то есть зависший deploy оставит
production остановленным на шесть часов молча.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = (
    ROOT / ".github/workflows/verify.yml",
    ROOT / ".github/workflows/release.yml",
    ROOT / ".github/workflows/publish-images.yml",
)
_VERIFY = ROOT / ".github/workflows/verify.yml"

# Потолок ловит зависание, но не рубит честный прогон. У проверки он жёстче:
# самая медленная её джоба идёт пять минут, а сборка образов на холодном кэше —
# десятки.
_MAX_TIMEOUT_MINUTES = 60
_VERIFY_MAX_TIMEOUT_MINUTES = 40


def _steps_jobs(path: Path) -> dict[str, dict]:
    """Джобы, которым GitHub разрешает timeout-minutes.

    Джоба-вызов reusable workflow (`uses:`) предел времени не принимает: его
    объявляют джобы внутри вызванного workflow.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: job for name, job in document["jobs"].items() if "uses" not in job}


def test_every_job_of_the_release_surface_declares_a_timeout() -> None:
    missing: list[str] = []
    for path in _WORKFLOWS:
        for name, job in _steps_jobs(path).items():
            if "timeout-minutes" not in job:
                missing.append(f"{path.name}:{name}")
    assert missing == [], (
        "джобы без timeout-minutes: "
        + ", ".join(missing)
        + " — без предела зависший шаг идёт до лимита раннера в 360 минут"
    )


def test_timeouts_stay_tight() -> None:
    too_long = {
        f"{path.name}:{name}": job["timeout-minutes"]
        for path in _WORKFLOWS
        for name, job in _steps_jobs(path).items()
        if int(job.get("timeout-minutes", 0)) > _MAX_TIMEOUT_MINUTES
    }
    assert too_long == {}, (
        f"предел выше {_MAX_TIMEOUT_MINUTES} минут не ловит зависание: {too_long}"
    )


def test_verification_timeouts_stay_tighter_than_image_builds() -> None:
    too_long = {
        name: job["timeout-minutes"]
        for name, job in _steps_jobs(_VERIFY).items()
        if int(job.get("timeout-minutes", 0)) > _VERIFY_MAX_TIMEOUT_MINUTES
    }
    assert too_long == {}, (
        f"проверка не строит образы и не должна занимать больше "
        f"{_VERIFY_MAX_TIMEOUT_MINUTES} минут: {too_long}"
    )
