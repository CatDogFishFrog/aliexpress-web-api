from __future__ import annotations

import math
import random
import statistics
from typing import Any, cast

import pytest

from aliweb.infrastructure.browser.human import (
    MAX_DELAY_S,
    HumanInput,
    build_mouse_path,
    typing_timings,
)
from aliweb.infrastructure.config import HumanSettings
from tests.fakes_browser import FakeElement, FakePage

EMAIL = "vadim.goncharov.1999@outlook.com"


class TestTypingTimings:
    def test_text_is_preserved_char_by_char(self) -> None:
        timings = typing_timings(EMAIL, random.Random(1))
        assert "".join(t.char for t in timings) == EMAIL

    @pytest.mark.parametrize("seed", range(40))
    def test_delays_are_human_not_instant_not_glacial(self, seed: int) -> None:
        timings = typing_timings(EMAIL, random.Random(seed))
        assert all(0.04 <= t.wait_s <= MAX_DELAY_S for t in timings)  # жодної паузи довше ~1,2 с
        assert all(0.035 <= t.hold_s <= 0.14 for t in timings)
        total = sum(t.wait_s + t.hold_s for t in timings)
        assert 2.0 < total < 9.0  # 33 символи: ~3–6 с, а не миттєво й не хвилину

    def test_delays_are_irregular(self) -> None:
        waits = [t.wait_s for t in typing_timings(EMAIL, random.Random(7))[1:]]
        assert statistics.pstdev(waits) > 0.02 and len({round(w, 3) for w in waits}) > len(waits) // 2

    def test_speed_multiplier_scales_time(self) -> None:
        slow = sum(t.wait_s for t in typing_timings(EMAIL, random.Random(3), speed=0.5))
        fast = sum(t.wait_s for t in typing_timings(EMAIL, random.Random(3), speed=2.0))
        assert slow == pytest.approx(fast * 4, rel=1e-6)

    def test_empty_text(self) -> None:
        assert typing_timings("", random.Random(0)) == []


class TestMousePath:
    @pytest.mark.parametrize("seed", range(40))
    def test_ends_exactly_at_target_with_reasonable_duration(self, seed: int) -> None:
        rng = random.Random(seed)
        start = (rng.uniform(0, 1300), rng.uniform(0, 700))
        end = (rng.uniform(0, 1300), rng.uniform(0, 700))
        path = build_mouse_path(start, end, rng)
        assert (path[-1].x, path[-1].y) == end
        total = sum(s.dt_s for s in path)
        assert (0.0 <= total <= 1.4) and (math.dist(start, end) < 30 or total >= 0.2)

    def test_path_is_curved_not_a_straight_line(self) -> None:
        deviations = []
        for seed in range(30):
            rng = random.Random(seed)
            start, end = (100.0, 100.0), (900.0, 500.0)
            path = build_mouse_path(start, end, rng)
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            deviations.append(max(abs((s.x - start[0]) * dy - (s.y - start[1]) * dx) / length for s in path))
        assert statistics.mean(deviations) > 4.0

    def test_speed_is_not_constant(self) -> None:
        path = build_mouse_path((0, 0), (800, 0), random.Random(5))
        assert len({round(s.dt_s, 4) for s in path}) > len(path) // 2

    def test_zero_distance_single_step(self) -> None:
        assert len(build_mouse_path((5, 5), (5, 5), random.Random(0))) == 1


def human(
    page: FakePage, *, enabled: bool = True, speed: float = 1.0, seed: int = 0
) -> tuple[HumanInput, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return HumanInput(
        cast(Any, page), HumanSettings(enabled, speed), rng=random.Random(seed), sleep=fake_sleep
    ), sleeps


class TestHumanInput:
    async def test_type_text_sends_each_char_with_hold_delay(self) -> None:
        page = FakePage()
        h, sleeps = human(page)
        await h.type_text("Ab@1")
        assert [c for c, _ in page.keyboard.typed] == ["A", "b", "@", "1"]
        assert all(d is not None and 35 <= d <= 140 for _, d in page.keyboard.typed)
        assert len(sleeps) == 4 and max(sleeps) <= MAX_DELAY_S

    async def test_click_lands_inside_element_and_presses_once(self) -> None:
        page = FakePage()
        el = FakeElement(page, 800, 400, 200, 48)
        h, sleeps = human(page)
        await h.click(cast(Any, el))
        x, y = page.mouse.moves[-1]
        assert 803 <= x <= 997 and 403 <= y <= 445
        assert (page.mouse.downs, page.mouse.ups) == (1, 1) and el.clicks == 0
        assert all(0 <= s <= MAX_DELAY_S for s in sleeps)

    async def test_click_target_is_not_always_the_exact_center(self) -> None:
        points = set()
        for seed in range(15):
            page = FakePage()
            h, _ = human(page, seed=seed)
            await h.click(cast(Any, FakeElement(page, 800, 400, 200, 48)))
            points.add(page.mouse.moves[-1])
        assert len(points) > 10

    async def test_offscreen_element_is_scrolled_into_view_with_wheel(self) -> None:
        page = FakePage()
        el = FakeElement(page, 800, 1500, 200, 48)  # нижче видимої області (768)
        h, _ = human(page)
        await h.click(cast(Any, el))
        assert page.mouse.wheels and sum(page.mouse.wheels) > 500
        assert len(page.mouse.wheels) >= 3  # кількома поштовхами, а не одним стрибком
        _, y = page.mouse.moves[-1]
        assert 0 < y < 768

    async def test_fill_clicks_then_types(self) -> None:
        page = FakePage()
        el = FakeElement(page, 700, 300, 400, 48)
        h, _ = human(page)
        await h.fill(cast(Any, el), "abc")
        assert page.mouse.downs == 1 and "".join(c for c, _ in page.keyboard.typed) == "abc"
        assert el.filled == []  # не «миттєвий» fill

    async def test_disabled_mode_is_instant_and_plain(self) -> None:
        page = FakePage()
        el = FakeElement(page, 700, 300, 400, 48)
        h, sleeps = human(page, enabled=False)
        await h.fill(cast(Any, el), "abc")
        await h.click(cast(Any, el))
        await h.pause(1, 2)
        assert el.filled == ["abc"] and el.clicks == 1 and sleeps == [] and page.mouse.downs == 0

    async def test_drag_reaches_distance_within_human_duration(self) -> None:
        page = FakePage()
        h, sleeps = human(page)
        await h.drag_horizontally((500.0, 300.0), 260.0)
        last_x, _ = page.mouse.moves[-1]
        assert abs(last_x - 760.0) < 1.5
        assert (page.mouse.downs, page.mouse.ups) == (1, 1)
        assert len(page.mouse.moves) > 20 and 0.7 < sum(sleeps) < 3.5

    async def test_scroll_is_split_into_several_wheel_events(self) -> None:
        page = FakePage()
        h, _ = human(page)
        await h.scroll(300)
        assert 3 <= len(page.mouse.wheels) <= 6 and sum(page.mouse.wheels) == pytest.approx(300)

    async def test_settle_moves_mouse_and_may_scroll(self) -> None:
        page = FakePage()
        h, _ = human(page, seed=3)
        await h.settle()
        assert page.mouse.moves  # мишка ворухнулась

    async def test_idle_scroll_skipped_when_page_does_not_scroll(self) -> None:
        page = FakePage()
        page.overflow = 0
        h, _ = human(page)
        await h.idle_scroll()
        assert page.mouse.wheels == []
