"""Planner detection and timer parsing."""

from __future__ import annotations

import unittest

from quest_assistant.events.sources.timers import parse_timer_request
from quest_assistant.planner.detect import try_build_research_plan


class PlannerDetectTests(unittest.TestCase):
    def test_research_laptops_budget(self) -> None:
        plan = try_build_research_plan("research laptops under $1000")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIn("laptop", plan.query.lower())
        self.assertTrue(plan.add_quest)

    def test_not_research_short(self) -> None:
        self.assertIsNone(try_build_research_plan("add milk"))

    def test_timer_minutes(self) -> None:
        parsed = parse_timer_request("set a timer in 5 minutes for tea")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        _label, delay = parsed
        self.assertEqual(delay, 300.0)


if __name__ == "__main__":
    unittest.main()
