import pytest

from app.limits import RateLimitExceeded, SlidingWindowLimiter


async def test_limiter_refuses_new_clients_at_capacity_without_evicting_active_limits():
    now = [0.0]
    limiter = SlidingWindowLimiter(max_clients=2, clock=lambda: now[0])
    await limiter.check("one")
    await limiter.check("two")
    with pytest.raises(RateLimitExceeded):
        await limiter.check("three")
    for _ in range(4):
        await limiter.check("one")
    with pytest.raises(RateLimitExceeded):
        await limiter.check("one")
    now[0] = 3600
    await limiter.check("three")
