# -*- coding: utf-8 -*-
"""Каталог помещений: новые кабинеты и отсутствие дублей."""
from collections import Counter

from app.seed import LOCATIONS


def test_catalog_has_no_duplicate_keys_or_rooms():
    keys = [item[3] for item in LOCATIONS]
    assert len(keys) == len(set(keys)) == 55

    rooms = [(floor, room) for floor, room, _, _ in LOCATIONS if room]
    duplicates = [pair for pair, count in Counter(rooms).items() if count > 1]
    assert duplicates == []


def test_new_rooms_are_in_catalog():
    rooms = {(floor, room) for floor, room, _, _ in LOCATIONS}
    assert ("1", "103") in rooms
    assert ("1", "105") in rooms
    assert ("1", "109а") in rooms
    assert ("2", "209(2)") in rooms
    assert ("2", "216") in rooms
    assert ("3", "303a") in rooms
    assert ("3", "317") in rooms
    assert ("3", "305") in rooms

    zones = {zone for _, _, zone, _ in LOCATIONS}
    assert "УПК" in zones
    assert "Женский санузел" in zones
    assert "Кафедра математических и естественных дисциплин" in zones
