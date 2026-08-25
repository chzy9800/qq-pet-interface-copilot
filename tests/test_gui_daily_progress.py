# -*- coding: utf-8 -*-
"""对“今日任务进度条”核心纯计算逻辑的单元测试（不依赖 tk 环境）。"""
import unittest

from main import MainWindow


class DailyProgressBoundsTests(unittest.TestCase):
    def test_within_limit(self) -> None:
        self.assertEqual(MainWindow._daily_bounds(12, 20), (20, 12, "12/20"))

    def test_above_limit_clamped(self) -> None:
        self.assertEqual(MainWindow._daily_bounds(25, 20), (20, 20, "20/20"))

    def test_zero_limit_means_unlimited(self) -> None:
        # limit<=0 表示不限次数，进度条按已完成占满、文字显示 ∞
        self.assertEqual(MainWindow._daily_bounds(7, 0), (7, 7, "7/∞"))
        self.assertEqual(MainWindow._daily_bounds(0, 0), (1, 0, "0/∞"))

    def test_negative_and_missing_are_safe(self) -> None:
        self.assertEqual(MainWindow._daily_bounds(-3, 5), (5, 0, "0/5"))
        self.assertEqual(MainWindow._daily_bounds(None, None), (1, 0, "0/∞"))


if __name__ == "__main__":
    unittest.main()
