# Unit tests for the panel API rate limiter (P0-2 enumeration brake).
from app.ratelimit import RateLimiter


def test_allows_up_to_limit_then_blocks():
    rl = RateLimiter(limit=3, window_seconds=60.0)
    assert [rl.allow("ip", now=t) for t in (0.0, 0.1, 0.2)] == [True, True, True]
    assert rl.allow("ip", now=0.3) is False  # 4th in window


def test_window_slides():
    rl = RateLimiter(limit=2, window_seconds=60.0)
    assert rl.allow("ip", now=0.0) is True
    assert rl.allow("ip", now=1.0) is True
    assert rl.allow("ip", now=2.0) is False
    # After the window passes, old hits expire and new ones are allowed.
    assert rl.allow("ip", now=61.5) is True


def test_keys_are_independent():
    rl = RateLimiter(limit=1, window_seconds=60.0)
    assert rl.allow("a", now=0.0) is True
    assert rl.allow("b", now=0.0) is True
    assert rl.allow("a", now=0.1) is False


def test_zero_limit_disables():
    rl = RateLimiter(limit=0, window_seconds=60.0)
    assert all(rl.allow("ip", now=float(i)) for i in range(100))
