from apps.meta_api_worker.main import TERMINAL_OPERATOR_REASONS


def test_terminal_operator_reasons_contain_all_branches():
    expected_codes = {
        "invalid_payload",
        "enable_grace_precondition",
        "owner_scoping",
        "irreversible_no_retry",
        "TokenInvalidError",
        "LoginRequiredError",
        "NotFoundError",
        "PermissionError",
        "MutationValidationError",
        "PermanentError",
        "NotImplementedError",
    }

    missing = expected_codes - set(TERMINAL_OPERATOR_REASONS.keys())
    assert not missing, f"Missing operator reasons for branches: {missing}"

    # Отдельно проверяем, что в тексте нет сырого содержимого исключения (типа %s, {}, exc)
    for code, text in TERMINAL_OPERATOR_REASONS.items():
        assert text, f"Empty text for code {code}"
        assert "%s" not in text, f"Raw exception formatting found in {code}: {text}"
        assert "{}" not in text, f"Raw exception formatting found in {code}: {text}"
        assert "exc" not in text.lower() or "exception" in text.lower(), (
            f"Raw exception variable found in {code}: {text}"
        )
        # Проверяем 3 вопроса: что случилось, чем грозит, что делать
        assert len(text.split(". ")) >= 3, (
            f"Text for {code} is too short, should answer 3 questions: {text}"
        )


def test_every_operator_reason_is_actually_written_by_a_branch() -> None:
    """Словарь без использования — это тот же «Причина не записана».

    Код попадает оператору двумя путями: литеральной строкой в терминальной
    ветке либо динамически, по имени класса постоянного исключения. Проверяются
    оба, иначе удаление строки ``"operator_reason": TERMINAL_OPERATOR_REASONS[...]``
    из ветки проходит незамеченным: словарь остаётся полным, а поле пустеет.
    """
    import re
    from pathlib import Path

    from apps.meta_api_worker.main import _PERMANENT_EXCEPTIONS

    worker_source = (
        Path(__file__).resolve().parents[2] / "apps" / "meta_api_worker" / "main.py"
    ).read_text(encoding="utf-8")

    reachable_by_exception_name = {exc.__name__ for exc in _PERMANENT_EXCEPTIONS}
    # Подклассы постоянных исключений разбираются отдельными isinstance-ветками:
    # их exc_name тоже доходит до словаря.
    reachable_by_exception_name |= set(re.findall(r"isinstance\(exc, (\w+)\)", worker_source))
    unused = [
        code
        for code in TERMINAL_OPERATOR_REASONS
        if f'TERMINAL_OPERATOR_REASONS["{code}"]' not in worker_source
        and code not in reachable_by_exception_name
    ]
    assert not unused, (
        f"коды есть в словаре, но ни одна ветка их не пишет оператору: {sorted(unused)}"
    )

    permanent_names = {exc.__name__ for exc in _PERMANENT_EXCEPTIONS}
    missing_for_permanent = sorted(permanent_names - set(TERMINAL_OPERATOR_REASONS))
    assert not missing_for_permanent, (
        "постоянное исключение без текста оператору — он увидит общую "
        f"формулировку вместо своей причины: {missing_for_permanent}"
    )
