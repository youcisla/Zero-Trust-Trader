"""`wire_stop` is the last check before a stop reaches the broker.

`next_stop` decides *whether* a stop moves; `wire_stop` quantizes it to the
tick and checks the exact number that will be sent. A stop on the wrong side of
the price closes the trade instantly, and one inside the broker's stop/freeze
distance is rejected with an error the supervisor would have to swallow. This
is the same property sweep as `next_stop`'s 3,000 cases, aimed at the wire.
"""

from __future__ import annotations

import math
import pathlib
import random
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from position_manager import wire_stop  # noqa: E402

LONG, SHORT = 1, -1


def info(tick=0.01, point=0.01, stops=0, freeze=0):
    return SimpleNamespace(
        trade_tick_size=tick,
        point=point,
        trade_stops_level=stops,
        trade_freeze_level=freeze,
    )


class TheQuantizedStop(unittest.TestCase):
    def test_a_long_rounds_up_and_a_short_rounds_down(self):
        inf = info(tick=0.05, point=0.01)
        up = wire_stop(LONG, 0.0, 1.12, 1.20, inf)
        down = wire_stop(SHORT, 0.0, 1.12, 1.05, inf)
        self.assertAlmostEqual(up, 1.15, places=9)
        self.assertAlmostEqual(down, 1.10, places=9)

    def test_the_broker_distance_is_respected(self):
        # 50 points of 0.0001 is 0.005: a candidate closer than that is refused.
        inf = info(tick=0.0001, point=0.0001, stops=50)
        self.assertIsNone(wire_stop(LONG, 1.0, 1.197, 1.20, inf))
        self.assertAlmostEqual(wire_stop(LONG, 0.0, 1.194, 1.20, inf), 1.194, places=9)
        self.assertIsNone(wire_stop(SHORT, 0.0, 1.203, 1.20, inf))

    def test_it_refuses_to_move_a_stop_away_from_price(self):
        inf = info(tick=0.01, point=0.01)
        self.assertIsNone(wire_stop(LONG, 1.15, 1.14, 1.20, inf))
        self.assertIsNone(wire_stop(SHORT, 1.05, 1.06, 1.00, inf))

    def test_it_refuses_to_invent_a_stop(self):
        inf = info()
        for side in (0, 2, -1.0):
            self.assertIsNone(wire_stop(side, 0.0, 1.0, 1.1, inf))
        self.assertIsNone(wire_stop(LONG, 0.0, 1.0, 1.1, info(tick=0, point=0)))
        self.assertIsNone(wire_stop(LONG, 0.0, 1.0, 1.1, info(point=0, tick=0.01)))
        self.assertIsNone(wire_stop(LONG, -0.01, 1.0, 1.1, inf))
        self.assertIsNone(wire_stop(LONG, 0.0, 0.0, 1.1, inf))
        self.assertIsNone(wire_stop(LONG, 0.0, math.nan, 1.1, inf))


class TheWireSweep(unittest.TestCase):
    def test_property_always_safe_side_never_worse(self):
        rng = random.Random(11)
        accepted = 0
        for _ in range(3000):
            side = rng.choice((LONG, SHORT))
            tick = rng.choice((0.0001, 0.001, 0.01, 0.05))
            point = tick if rng.random() < 0.7 else rng.choice((0.0001, 0.01, 0.1))
            levels = (rng.choice((0, 10, 20, 50, 100)), rng.choice((0, 10, 30, 100)))
            distance = max(levels) * point
            price = rng.uniform(1.0, 2.0)
            candidate = price - side * (distance + rng.uniform(0.0, 0.02))
            old = (
                0.0
                if rng.random() < 0.2
                else candidate - side * rng.uniform(-0.001, 0.01)
            )
            new = wire_stop(side, old, candidate, price, info(tick, point, *levels))
            if new is None:
                continue
            accepted += 1
            if side == LONG:
                self.assertLess(new, price, "long stop at or above the price")
                self.assertGreaterEqual(
                    price - new, distance - 1e-12, "inside the stop distance"
                )
                self.assertGreaterEqual(new, candidate - 1e-9, "a long stop rounds up")
                if old:
                    self.assertGreater(new, old, "long stop moved away from the price")
            else:
                self.assertGreater(new, price, "short stop at or below the price")
                self.assertGreaterEqual(
                    new - price, distance - 1e-12, "inside the stop distance"
                )
                self.assertLessEqual(new, candidate + 1e-9, "a short stop rounds down")
                if old:
                    self.assertLess(new, old, "short stop moved away from the price")
            # always an exact tick multiple
            self.assertLess(
                abs(new - round(new / tick) * tick), 1e-9, "not on the tick"
            )
        self.assertGreater(
            accepted, 500, f"the sweep only exercised refusals: {accepted}"
        )


if __name__ == "__main__":
    unittest.main()
