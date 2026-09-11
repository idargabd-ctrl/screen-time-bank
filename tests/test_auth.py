# -*- coding: utf-8 -*-
"""Код входа, подпись куки и защита от перебора."""

from app import auth


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_pin_hash_round_trip():
    stored = auth.make_pin_hash("4321")
    assert auth.check_pin("4321", stored)
    assert not auth.check_pin("1234", stored)
    assert not auth.check_pin("4321", "")
    assert not auth.check_pin("4321", "мусор-без-разделителя")


def test_cookie_signature_binds_child_and_expires():
    raw = auth.sign(7, 1_000_000, "secret")
    assert auth.verify(raw, "secret", max_age=10) is None          # просрочена
    assert auth.verify(raw.replace("7.", "8.", 1), "secret") is None
    assert auth.verify(raw, "other-secret") is None
    assert auth.verify(raw, "") is None


def test_guard_lets_a_child_mistype_a_few_times():
    clock = Clock()
    guard = auth.LoginGuard(per_source=5, total=20, window=600, clock=clock)
    for _ in range(4):
        guard.failed("tablet")
    assert guard.retry_after("tablet") == 0


def test_guard_blocks_source_after_limit_and_releases_after_window():
    clock = Clock()
    guard = auth.LoginGuard(per_source=5, total=20, window=600, clock=clock)
    for _ in range(5):
        guard.failed("tablet")
    assert guard.retry_after("tablet") == 600
    assert guard.retry_after("someone-else") == 0

    clock.t += 599
    assert guard.retry_after("tablet") == 1
    clock.t += 1
    assert guard.retry_after("tablet") == 0


def test_guard_total_limit_covers_spread_over_many_sources():
    clock = Clock()
    guard = auth.LoginGuard(per_source=5, total=20, window=600, clock=clock)
    for i in range(20):
        guard.failed(f"src-{i}")            # по одной с двадцати адресов
    assert guard.retry_after("fresh") > 0    # общий лимит закрыл всех


def test_guard_success_clears_the_source_only():
    clock = Clock()
    guard = auth.LoginGuard(per_source=5, total=6, window=600, clock=clock)
    for _ in range(5):
        guard.failed("tablet")
    guard.succeeded("tablet")
    assert guard.retry_after("tablet") == 0
    guard.failed("tablet")                   # общий счётчик: 6 из 6
    assert guard.retry_after("tablet") > 0
