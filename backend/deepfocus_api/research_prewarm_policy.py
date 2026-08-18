"""Scheduling policy for the research-report prewarm download budget."""
from __future__ import annotations

import math


def research_prewarm_download_cap(daily_max: int, hour: int) -> int:
    """Return the cumulative budget available by a Beijing-time hour.

    Keeping part of the quota for market hours prevents the midnight backlog
    from consuming the whole external-source daily allowance before fresh
    morning reports arrive.
    """
    limit = max(0, int(daily_max))
    if limit == 0:
        return 0
    current_hour = min(23, max(0, int(hour)))
    schedule = (
        (7, 0.25),
        (10, 0.50),
        (14, 0.75),
        (18, 0.875),
        (24, 1.0),
    )
    for cutoff, ratio in schedule:
        if current_hour < cutoff:
            return min(limit, max(1, math.ceil(limit * ratio)))
    return limit
