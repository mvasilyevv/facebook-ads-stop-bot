# -*- coding: utf-8 -*-
"""Тесты сканера папки креативов."""

from __future__ import annotations

import pytest

from core.campaign_creator.creo_scanner import scan_creo_folder


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


# Сценарий: две подпапки с числовыми именами и валидными файлами → два AdsetSpec в правильном порядке.
def test_scan_two_adsets(tmp_path):
    _touch(tmp_path / "1" / "a.jpeg")
    _touch(tmp_path / "1" / "b.mp4")
    _touch(tmp_path / "2" / "c.png")
    adsets = scan_creo_folder(tmp_path)
    assert len(adsets) == 2
    assert adsets[0].creo_subfolder == "1"
    assert adsets[0].creatives == ["a.jpeg", "b.mp4"]
    assert adsets[1].creo_subfolder == "2"
    assert adsets[1].creatives == ["c.png"]


# Сценарий: подпапки сортируются по числовому, а не лексикографическому ключу.
def test_scan_numeric_sort(tmp_path):
    _touch(tmp_path / "10" / "x.jpeg")
    _touch(tmp_path / "2" / "y.jpeg")
    adsets = scan_creo_folder(tmp_path)
    assert [a.creo_subfolder for a in adsets] == ["2", "10"]


# Сценарий: нечисловые подпапки игнорируются.
def test_scan_skips_non_numeric_subdirs(tmp_path):
    _touch(tmp_path / "1" / "x.jpeg")
    _touch(tmp_path / "trash" / "y.jpeg")
    _touch(tmp_path / "backup" / "z.mp4")
    adsets = scan_creo_folder(tmp_path)
    assert len(adsets) == 1
    assert adsets[0].creo_subfolder == "1"


# Сценарий: файлы с не-медийными расширениями фильтруются.
def test_scan_filters_extensions(tmp_path):
    _touch(tmp_path / "1" / "a.jpeg")
    _touch(tmp_path / "1" / "notes.txt")
    _touch(tmp_path / "1" / "report.pdf")
    adsets = scan_creo_folder(tmp_path)
    assert adsets[0].creatives == ["a.jpeg"]


# Сценарий: подпапка без валидных файлов пропускается.
def test_scan_skips_empty_adset(tmp_path):
    _touch(tmp_path / "1" / "a.jpeg")
    (tmp_path / "2").mkdir()
    _touch(tmp_path / "2" / "junk.txt")
    adsets = scan_creo_folder(tmp_path)
    assert len(adsets) == 1
    assert adsets[0].creo_subfolder == "1"


# Сценарий: общие тексты применяются ко всем адсетам.
def test_scan_applies_common_texts(tmp_path):
    _touch(tmp_path / "1" / "a.jpeg")
    _touch(tmp_path / "2" / "b.jpeg")
    adsets = scan_creo_folder(
        tmp_path,
        name_suffix="adv1",
        headline="HD",
        primary_text="PT",
        description="DESC",
    )
    for a in adsets:
        assert a.name_suffix == "adv1"
        assert a.headline == "HD"
        assert a.primary_text == "PT"
        assert a.description == "DESC"


# Сценарий: несуществующая папка бросает ValueError.
def test_scan_missing_folder(tmp_path):
    with pytest.raises(ValueError, match="не найден"):
        scan_creo_folder(tmp_path / "nonexistent")


# Сценарий: папка без числовых подпапок бросает ValueError.
def test_scan_no_numeric_subdirs(tmp_path):
    _touch(tmp_path / "trash" / "x.jpeg")
    with pytest.raises(ValueError, match="числовыми именами"):
        scan_creo_folder(tmp_path)


# Сценарий: папка с числовыми подпапками, но без валидных файлов — ValueError.
def test_scan_no_valid_creatives(tmp_path):
    _touch(tmp_path / "1" / "junk.txt")
    with pytest.raises(ValueError, match="ни одного адсета"):
        scan_creo_folder(tmp_path)
