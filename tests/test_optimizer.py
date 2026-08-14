from __future__ import annotations

import unittest
from fractions import Fraction

from qqpet_app.client import BathItem, SchoolCourse, WorkJob
from qqpet_app.optimizer import (
    Activity,
    OptimizationRequest,
    _course_coin_cost,
    choose_adaptive_plan,
    derive_auto_optimization_inputs,
    fatigue_weighted_minutes,
    optimize_daily_plan,
)


class OptimizerTests(unittest.TestCase):
    def test_live_course_text_ignores_markdown_numbers_and_current_values(self) -> None:
        icon = "![#20px #20px](https://qqpet.gtimg.com/icon/1776409721409.png)"
        tips = "[ ](mqqapi://markdown/node?nodeType=petTips&text=%E5%AD%A6%E4%B9%A0%97)"
        short = SchoolCourse(
            "舞台表演课",
            6125003,
            f"魅力+20{tips}",
            "20分钟",
            f"{icon} 70(当前{icon}2001)体力10(当前85)清洁4(当前81)",
            can_do=True,
        )
        long = SchoolCourse(
            "艺术实践课",
            6125006,
            f"魅力+50{tips}",
            "1小时",
            f"{icon} 210(当前{icon}2001)体力30(当前85)清洁12(当前81)",
            can_do=True,
        )
        self.assertEqual(short.reward_value, 20)
        self.assertEqual(long.reward_value, 50)
        self.assertEqual(_course_coin_cost(short), 70)
        self.assertEqual(_course_coin_cost(long), 210)

    def test_auto_inputs_use_live_catalog_and_learned_food_economics(self) -> None:
        inputs = derive_auto_optimization_inputs(
            courses=(
                SchoolCourse(
                    "课程", 61001, "力量 +10", "10分钟", "20金币",
                    "体力-6，清洁-3", True,
                ),
            ),
            jobs=(
                WorkJob(
                    1, "职业", "岗位", 64001, "50金币", "10分钟", "",
                    "体力-2，清洁-1", True,
                ),
            ),
            bath_items=(BathItem("2", "沐浴球", 8, 25, 0),),
            food_count=0,
            bath_count=0,
            preferred_bath_item_id="2",
            learned_profile={"food": {"price": 7, "restore": 12}},
        )
        self.assertEqual(inputs.course_hunger_cost, 6)
        self.assertEqual(inputs.course_clean_cost, 3)
        self.assertEqual(inputs.work_hunger_cost, 2)
        self.assertEqual(inputs.work_clean_cost, 1)
        self.assertEqual(inputs.biscuit_price, 7)
        self.assertEqual(inputs.biscuit_restore, 12)
        self.assertEqual(inputs.soap_price, 8)
        self.assertEqual(inputs.soap_restore, 25)
        self.assertEqual(inputs.safety_floor, 15)
        self.assertIn("实测已校准", inputs.summary)

    def test_adaptive_plan_uses_live_course_and_highest_hourly_job(self) -> None:
        courses = (
            SchoolCourse("慢课", 61001, "力量 +20", "60分钟", "70金币", can_do=True),
            SchoolCourse("快课", 61002, "力量 +8", "20分钟", "70金币", can_do=True),
        )
        jobs = (
            WorkJob(1, "职业", "短工", 64001, "77金币", "10分钟", can_do=True),
            WorkJob(1, "职业", "长工", 64002, "200金币", "60分钟", can_do=True),
        )
        decision = choose_adaptive_plan(
            courses=courses,
            jobs=jobs,
            attribute="physical",
            current_gold=701,
            opening_gold=701,
            active_minutes=0,
            daily_active_minutes=120,
            safety_floor=200,
            preserve_opening_gold=True,
            course_hunger_cost=10,
            course_clean_cost=4,
            work_hunger_cost=4,
            work_clean_cost=2,
            biscuit_price=5,
            biscuit_restore=10,
            soap_price=2,
            soap_restore=10,
        )
        self.assertIsNotNone(decision.result)
        self.assertEqual(decision.job_name, "短工")
        self.assertEqual(decision.course_name, "快课")
        self.assertIn(decision.action, {"study", "work"})

    def test_adaptive_plan_normalizes_current_fatigue_once(self) -> None:
        course = SchoolCourse("课程", 61001, "力量 +2", "10分钟", "10金币", can_do=True)
        job = WorkJob(1, "职业", "岗位", 64001, "25金币", "10分钟", can_do=True)
        decision = choose_adaptive_plan(
            courses=(course,), jobs=(job,), attribute="physical",
            current_gold=100, opening_gold=100, active_minutes=480,
            daily_active_minutes=500, safety_floor=0, preserve_opening_gold=True,
            course_hunger_cost=0, course_clean_cost=0,
            work_hunger_cost=0, work_clean_cost=0,
            biscuit_price=5, biscuit_restore=10, soap_price=2, soap_restore=10,
        )
        self.assertTrue(decision.result and decision.result.feasible)
        self.assertEqual(decision.result.work_income, 25)

    def test_adaptive_plan_prefers_server_resource_cost_when_present(self) -> None:
        cheap = SchoolCourse(
            "低消耗课", 61001, "力量 +10", "10分钟", "20金币",
            "体力-2，清洁-1", True,
        )
        expensive = SchoolCourse(
            "高消耗课", 61002, "力量 +10", "10分钟", "20金币",
            "体力-40，清洁-20", True,
        )
        job = WorkJob(1, "职业", "岗位", 64001, "100金币", "10分钟", can_do=True)
        decision = choose_adaptive_plan(
            courses=(cheap, expensive), jobs=(job,), attribute="physical",
            current_gold=100, opening_gold=100, active_minutes=0,
            daily_active_minutes=40, safety_floor=0, preserve_opening_gold=True,
            course_hunger_cost=10, course_clean_cost=4,
            work_hunger_cost=4, work_clean_cost=2,
            biscuit_price=5, biscuit_restore=10, soap_price=2, soap_restore=10,
        )
        self.assertEqual(decision.course_name, "低消耗课")

    def test_fatigue_is_integrated_across_eight_hour_boundary(self) -> None:
        self.assertEqual(fatigue_weighted_minutes(470, 20), Fraction(25, 2))

    def test_fixed_cost_is_not_reduced_by_fatigue(self) -> None:
        request = OptimizationRequest(
            initial_gold=100,
            safety_floor=0,
            horizon_minutes=10,
            initial_active_minutes=480,
            activities=(Activity("工作", "work", 10, 7.7, fixed_coin_change=-2.4),),
        )
        result = optimize_daily_plan(request)
        self.assertEqual(result.final_gold, 116.85)

    def test_learning_first_recovers_cost_and_respects_floor(self) -> None:
        request = OptimizationRequest(
            initial_gold=100,
            safety_floor=50,
            required_end_gold=100,
            horizon_minutes=60,
            strict_study_first=True,
            activities=(
                Activity("课程", "study", 10, -1, 1),
                Activity("打工", "work", 10, 2),
            ),
        )
        result = optimize_daily_plan(request)
        self.assertTrue(result.feasible)
        self.assertEqual(result.study_minutes, 40)
        self.assertEqual(result.work_minutes, 20)
        self.assertEqual(result.learning_gain, 40)
        self.assertEqual(result.final_gold, 100)
        self.assertEqual([step.kind for step in result.plan], ["study"] * 4 + ["work"] * 2)
        self.assertEqual(result.plan_blocks[0].repeats, 4)
        self.assertEqual(result.plan_blocks[1].repeats, 2)
        self.assertGreaterEqual(result.minimum_gold, 50)

    def test_safety_floor_only_applies_to_final_balance(self) -> None:
        request = OptimizationRequest(
            initial_gold=30,
            safety_floor=20,
            required_end_gold=30,
            horizon_minutes=40,
            activities=(
                Activity("课程", "study", 10, -1, 1),
                Activity("打工", "work", 10, 1),
            ),
        )
        result = optimize_daily_plan(request)
        self.assertTrue(result.feasible)
        self.assertEqual(result.learning_gain, 20)
        self.assertEqual(result.minimum_gold, 10)
        self.assertGreaterEqual(result.final_gold, 30)

    def test_free_order_can_study_after_work(self) -> None:
        request = OptimizationRequest(
            initial_gold=10,
            safety_floor=0,
            required_end_gold=10,
            horizon_minutes=30,
            strict_study_first=False,
            activities=(
                Activity("课程", "study", 10, -2, 1),
                Activity("打工", "work", 10, 2),
            ),
        )
        result = optimize_daily_plan(request)
        self.assertTrue(result.feasible)
        self.assertEqual(result.learning_gain, 10)
        self.assertEqual(result.study_minutes, 10)
        self.assertEqual(result.work_minutes, 20)

    def test_repeat_limit_is_enforced(self) -> None:
        request = OptimizationRequest(
            initial_gold=100,
            safety_floor=0,
            horizon_minutes=30,
            activities=(Activity("限一次课程", "study", 10, 0, 1, repeat_limit=1),),
        )
        result = optimize_daily_plan(request)
        self.assertEqual(result.study_minutes, 10)

    def test_optimizer_matches_independent_small_brute_force(self) -> None:
        activities = (
            Activity("学", "study", 10, -1, 1),
            Activity("工", "work", 10, 2),
        )
        request = OptimizationRequest(
            initial_gold=30,
            safety_floor=10,
            required_end_gold=30,
            horizon_minutes=40,
            strict_study_first=False,
            activities=activities,
        )
        expected: tuple[Fraction, Fraction] | None = None

        def visit(elapsed: int, gold: Fraction, learning: Fraction) -> None:
            nonlocal expected
            if gold >= request.required_end_gold:
                score = (learning, gold)
                expected = score if expected is None or score > expected else expected
            for item in activities:
                if elapsed + item.duration_minutes > request.horizon_minutes:
                    continue
                coin = item.coin_per_minute * item.duration_minutes
                next_gold = gold + coin
                if next_gold >= request.safety_floor:
                    visit(
                        elapsed + item.duration_minutes,
                        next_gold,
                        learning + item.learning_per_minute * item.duration_minutes,
                    )

        visit(0, Fraction(30), Fraction(0))
        result = optimize_daily_plan(request)
        self.assertEqual((Fraction(str(result.learning_gain)), Fraction(str(result.final_gold))), expected)


if __name__ == "__main__":
    unittest.main()
