"""QQ 宠物每日学习/打工计划优化器。

模型使用官方新版疲劳区间：累计学习+打工 0-8 小时为 100%，
8-12 小时为 25%，12-24 小时为 10%。课程金币成本默认不受疲劳影响，
学习收益和打工金币收益受疲劳影响。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import math
import re
from typing import Iterable, Literal, Optional


ActivityKind = Literal["study", "work"]


def _number(value: int | float | str | Fraction) -> Fraction:
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


def _display(value: Fraction) -> float:
    return round(float(value), 6)


@dataclass(frozen=True)
class FatigueBand:
    """A cumulative-active-time band, ending at ``end_minute``."""

    end_minute: int
    multiplier: Fraction | int | float | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "multiplier", _number(self.multiplier))
        if self.end_minute <= 0 or self.multiplier < 0:
            raise ValueError("疲劳区间终点必须为正数，倍率不得为负数")


OFFICIAL_FATIGUE_BANDS = (
    FatigueBand(8 * 60, 1),
    FatigueBand(12 * 60, "0.25"),
    FatigueBand(24 * 60, "0.10"),
)


@dataclass(frozen=True)
class Activity:
    """A repeatable course or job described by per-minute rates.

    ``coin_per_minute`` is negative for a course cost and positive for job pay.
    A course normally sets ``fatigue_affects_coin=False`` because its price does
    not become cheaper when tired. A job normally leaves it as ``True``.
    """

    name: str
    kind: ActivityKind
    duration_minutes: int
    coin_per_minute: Fraction | int | float | str
    learning_per_minute: Fraction | int | float | str = 0
    fixed_coin_change: Fraction | int | float | str = 0
    repeat_limit: Optional[int] = None
    fatigue_affects_coin: Optional[bool] = None
    fatigue_affects_learning: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.kind not in ("study", "work"):
            raise ValueError(f"未知活动类型：{self.kind}")
        if not self.name or self.duration_minutes <= 0:
            raise ValueError("活动名称不得为空，时长必须大于 0")
        if self.repeat_limit is not None and self.repeat_limit < 0:
            raise ValueError("repeat_limit 不得为负数")
        object.__setattr__(self, "coin_per_minute", _number(self.coin_per_minute))
        object.__setattr__(self, "learning_per_minute", _number(self.learning_per_minute))
        object.__setattr__(self, "fixed_coin_change", _number(self.fixed_coin_change))
        if self.fatigue_affects_coin is None:
            object.__setattr__(self, "fatigue_affects_coin", self.kind == "work")
        if self.fatigue_affects_learning is None:
            object.__setattr__(self, "fatigue_affects_learning", self.kind == "study")


@dataclass(frozen=True)
class OptimizationRequest:
    initial_gold: Fraction | int | float | str
    safety_floor: Fraction | int | float | str
    horizon_minutes: int
    activities: tuple[Activity, ...]
    required_end_gold: Fraction | int | float | str | None = None
    minimum_balance: Fraction | int | float | str = 0
    initial_active_minutes: int = 0
    strict_study_first: bool = False
    fatigue_bands: tuple[FatigueBand, ...] = OFFICIAL_FATIGUE_BANDS
    max_labels_per_minute: int = 100_000

    def __post_init__(self) -> None:
        initial = _number(self.initial_gold)
        floor = _number(self.safety_floor)
        minimum = _number(self.minimum_balance)
        requested_end = initial if self.required_end_gold is None else _number(self.required_end_gold)
        required = max(requested_end, floor)
        object.__setattr__(self, "initial_gold", initial)
        object.__setattr__(self, "safety_floor", floor)
        object.__setattr__(self, "required_end_gold", required)
        object.__setattr__(self, "minimum_balance", minimum)
        object.__setattr__(self, "activities", tuple(self.activities))
        object.__setattr__(self, "fatigue_bands", tuple(self.fatigue_bands))
        if initial < minimum:
            raise ValueError("初始金币不能低于中途最低余额")
        if self.horizon_minutes < 0 or self.initial_active_minutes < 0:
            raise ValueError("时间不得为负数")
        if self.initial_active_minutes + self.horizon_minutes > self.fatigue_bands[-1].end_minute:
            raise ValueError("累计活动时长不得跨越当日 24 小时边界")
        if not self.activities:
            raise ValueError("活动列表不得为空")
        ends = [band.end_minute for band in self.fatigue_bands]
        if ends != sorted(set(ends)):
            raise ValueError("疲劳区间终点必须严格递增")


@dataclass(frozen=True)
class PlanStep:
    name: str
    kind: ActivityKind
    start_minute: int
    end_minute: int
    average_fatigue_multiplier: float
    coin_change: float
    learning_gain: float
    gold_after: float


@dataclass(frozen=True)
class PlanBlock:
    name: str
    kind: ActivityKind
    repeats: int
    start_minute: int
    end_minute: int
    average_fatigue_multiplier: float
    coin_change: float
    learning_gain: float
    gold_after: float


@dataclass(frozen=True)
class OptimizationResult:
    feasible: bool
    reason: str
    plan: tuple[PlanStep, ...] = ()
    plan_blocks: tuple[PlanBlock, ...] = ()
    total_minutes: int = 0
    study_minutes: int = 0
    work_minutes: int = 0
    study_cost: float = 0
    work_income: float = 0
    learning_gain: float = 0
    final_gold: float = 0
    minimum_gold: float = 0
    break_even_at_minute: Optional[int] = None
    work_minutes_to_break_even: Optional[int] = None


@dataclass(frozen=True)
class AdaptiveDecision:
    action: ActivityKind | None
    course_sub_event: int = 0
    career_type: int = 0
    job_sub_event: int = 0
    course_name: str = ""
    job_name: str = ""
    explanation: str = ""
    result: OptimizationResult | None = None


@dataclass(frozen=True)
class AutoOptimizationInputs:
    """Runtime values inferred from current catalogs and observations."""

    daily_active_minutes: int
    safety_floor: float
    preserve_opening_gold: bool
    course_hunger_cost: float
    course_clean_cost: float
    work_hunger_cost: float
    work_clean_cost: float
    biscuit_price: float
    biscuit_restore: float
    soap_price: float
    soap_restore: float
    summary: str


def _last_number(text: str) -> float:
    values = re.findall(r"\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(values[-1]) if values else 0.0


def _course_coin_cost(item: object) -> float:
    """Parse the course fee without consuming URL or current-state numbers."""
    text = str(getattr(item, "cost", ""))
    # Server text starts with a Markdown coin icon, then 70(当前...2001),
    # followed by hunger/clean costs and their current values.
    visible = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:金币)?\s*\(\s*当前", visible)
    if match:
        return float(match.group(1))
    explicit = re.search(r"(\d+(?:\.\d+)?)\s*金币", visible)
    if explicit:
        return float(explicit.group(1))
    values = re.findall(r"\d+(?:\.\d+)?", visible.replace(",", ""))
    return float(values[0]) if values else 0.0


def _resource_cost(item: object, keyword: str, fallback: float) -> Fraction:
    """Read a resource cost from server text when present, otherwise use config."""
    text = f"{getattr(item, 'cost', '')} {getattr(item, 'description', '')}"
    match = re.search(rf"{re.escape(keyword)}\s*(?:值)?\s*[-−－:]?\s*(\d+(?:\.\d+)?)", text)
    return Fraction(match.group(1)) if match else Fraction(str(fallback))


def _catalog_cost(
    items: Iterable[object], keyword: str, fallback: float
) -> tuple[float, bool]:
    values: list[float] = []
    for item in items:
        text = f"{getattr(item, 'cost', '')} {getattr(item, 'description', '')}"
        match = re.search(
            rf"{re.escape(keyword)}\s*(?:值)?\s*[-−－:]?\s*(\d+(?:\.\d+)?)",
            text,
        )
        if match:
            values.append(float(match.group(1)))
    if not values:
        return float(fallback), False
    values.sort()
    middle = len(values) // 2
    value = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return value, True


def derive_auto_optimization_inputs(
    *,
    courses: Iterable[object],
    jobs: Iterable[object],
    bath_items: Iterable[object],
    food_count: int,
    bath_count: int,
    preferred_bath_item_id: str,
    learned_profile: dict[str, object] | None = None,
) -> AutoOptimizationInputs:
    """Infer all technical optimizer inputs; users only choose whether to enable it."""

    courses = tuple(courses)
    jobs = tuple(jobs)
    bath_items = tuple(bath_items)
    profile = learned_profile or {}
    food_value = profile.get("food", {})
    food = food_value if isinstance(food_value, dict) else {}
    course_hunger, course_hunger_live = _catalog_cost(courses, "体力", 10)
    course_clean, course_clean_live = _catalog_cost(courses, "清洁", 4)
    work_hunger, work_hunger_live = _catalog_cost(jobs, "体力", 4)
    work_clean, work_clean_live = _catalog_cost(jobs, "清洁", 2)

    selected_bath = next(
        (item for item in bath_items if str(getattr(item, "item_id", "")) == preferred_bath_item_id),
        None,
    )
    if selected_bath is None:
        usable = [item for item in bath_items if float(getattr(item, "clean_gain", 0)) > 0]
        selected_bath = min(
            usable,
            key=lambda item: float(getattr(item, "gold_price", 0))
            / max(1.0, float(getattr(item, "clean_gain", 0))),
            default=None,
        )
    soap_price = float(getattr(selected_bath, "gold_price", 2) or 2)
    soap_restore = float(getattr(selected_bath, "clean_gain", 10) or 10)
    biscuit_price = float(food.get("price", 5) or 5)
    biscuit_restore = float(food.get("restore", 10) or 10)
    safety_floor = (biscuit_price if food_count <= 0 else 0) + (
        soap_price if bath_count <= 0 else 0
    )
    food_source = (
        "食物价格/恢复=实测已校准"
        if food.get("price") and food.get("restore")
        else "食物价格/恢复=待首次喂食自动校准"
    )
    activity_source = (
        "课程/岗位消耗=服务器目录逐项解析（摘要为目录中位数）"
        if all((course_hunger_live, course_clean_live, work_hunger_live, work_clean_live))
        else "部分课程/岗位消耗=目录未下发，暂用保守估值"
    )
    bath_source = (
        "洗护价格/恢复=服务器目录"
        if selected_bath is not None
        else "洗护价格/恢复=目录未下发，暂用保守估值"
    )
    return AutoOptimizationInputs(
        daily_active_minutes=24 * 60,
        safety_floor=safety_floor,
        preserve_opening_gold=True,
        course_hunger_cost=course_hunger,
        course_clean_cost=course_clean,
        work_hunger_cost=work_hunger,
        work_clean_cost=work_clean,
        biscuit_price=biscuit_price,
        biscuit_restore=biscuit_restore,
        soap_price=soap_price,
        soap_restore=soap_restore,
        summary=(
            "自动测算：学习体力/清洁 "
            f"{course_hunger:g}/{course_clean:g}，打工 {work_hunger:g}/{work_clean:g}，"
            f"食物 {biscuit_price:g}金币/+{biscuit_restore:g}，洗护 "
            f"{soap_price:g}金币/+{soap_restore:g}，期末自动储备 {safety_floor:g}金币；"
            + activity_source
            + "；"
            + bath_source
            + "；"
            + food_source
        ),
    )


def _fatigue_multiplier_at(active_minutes: int) -> Fraction:
    for band in OFFICIAL_FATIGUE_BANDS:
        if active_minutes < band.end_minute:
            return band.multiplier
    return OFFICIAL_FATIGUE_BANDS[-1].multiplier


def choose_adaptive_plan(
    *,
    courses: Iterable[object],
    jobs: Iterable[object],
    attribute: str,
    current_gold: float,
    opening_gold: float,
    active_minutes: int,
    daily_active_minutes: int,
    safety_floor: float,
    preserve_opening_gold: bool,
    course_hunger_cost: float,
    course_clean_cost: float,
    work_hunger_cost: float,
    work_clean_cost: float,
    biscuit_price: float,
    biscuit_restore: float,
    soap_price: float,
    soap_restore: float,
) -> AdaptiveDecision:
    """Search server catalogs and return the next sustainable study/work action.

    Server catalog rewards are assumed to already reflect the current fatigue
    band. They are normalized back to their 100% value before the optimizer
    applies future bands, preventing a second fatigue discount.
    """
    remaining = max(0, int(daily_active_minutes) - int(active_minutes))
    if remaining <= 0:
        return AdaptiveDecision(None, explanation="今日学习/打工规划时长已用完")

    keyword = {"physical": "力量", "culture": "智力", "art": "魅力"}.get(attribute, "")
    available_courses = [
        item for item in courses
        if bool(getattr(item, "can_do", False))
        and int(getattr(item, "sub_event_type", 0)) > 0
        and (not keyword or keyword in str(getattr(item, "reward", "")))
    ]
    available_jobs = [
        item for item in jobs
        if bool(getattr(item, "can_do", False))
        and int(getattr(item, "sub_event_type", 0)) > 0
        and int(getattr(item, "duration_seconds", 0)) < 2**31 - 1
        and float(getattr(item, "reward_value", 0)) > 0
    ]
    current_multiplier = _fatigue_multiplier_at(int(active_minutes))
    replacement_hunger = Fraction(str(biscuit_price)) / Fraction(str(biscuit_restore))
    replacement_clean = Fraction(str(soap_price)) / Fraction(str(soap_restore))
    best_job = None
    if available_jobs:
        best_job = max(
            available_jobs,
            key=lambda item: (
                Fraction(int(getattr(item, "reward_value", 0)), max(1, math.ceil(int(getattr(item, "duration_seconds", 0)) / 60))),
                int(getattr(item, "reward_value", 0)),
                -int(getattr(item, "duration_seconds", 0)),
            ),
        )

    target_gold = opening_gold if preserve_opening_gold else safety_floor
    work_care = Fraction(0)
    if best_job is not None:
        work_care = (
            _resource_cost(best_job, "体力", work_hunger_cost) * replacement_hunger
            + _resource_cost(best_job, "清洁", work_clean_cost) * replacement_clean
        )
    best_choice: tuple[OptimizationResult, object] | None = None
    for course in available_courses:
        course_minutes = max(1, math.ceil(int(getattr(course, "duration_seconds", 0)) / 60))
        if course_minutes > remaining:
            continue
        course_cost = _course_coin_cost(course)
        course_care = (
            _resource_cost(course, "体力", course_hunger_cost) * replacement_hunger
            + _resource_cost(course, "清洁", course_clean_cost) * replacement_clean
        )
        displayed_learning = float(getattr(course, "reward_value", 0))
        base_learning = Fraction(str(displayed_learning)) / current_multiplier
        activities = [
            Activity(
                name=f"school:{int(getattr(course, 'sub_event_type', 0))}:{getattr(course, 'name', '')}",
                kind="study",
                duration_minutes=course_minutes,
                coin_per_minute=-Fraction(str(course_cost)) / course_minutes,
                learning_per_minute=base_learning / course_minutes,
                fixed_coin_change=-course_care,
            )
        ]
        if best_job is not None:
            job_minutes = max(1, math.ceil(int(getattr(best_job, "duration_seconds", 0)) / 60))
            displayed_pay = Fraction(int(getattr(best_job, "reward_value", 0)))
            base_pay = displayed_pay / current_multiplier
            activities.append(
                Activity(
                    name=f"work:{int(getattr(best_job, 'career_type', 0))}:{int(getattr(best_job, 'sub_event_type', 0))}:{getattr(best_job, 'name', '')}",
                    kind="work",
                    duration_minutes=job_minutes,
                    coin_per_minute=base_pay / job_minutes,
                    fixed_coin_change=-work_care,
                )
            )
        result = optimize_daily_plan(
            OptimizationRequest(
                initial_gold=current_gold,
                safety_floor=safety_floor,
                required_end_gold=target_gold,
                minimum_balance=0,
                initial_active_minutes=int(active_minutes),
                horizon_minutes=remaining,
                activities=tuple(activities),
            )
        )
        if result.feasible and result.plan and (
            best_choice is None
            or (result.learning_gain, result.final_gold, -result.total_minutes)
            > (best_choice[0].learning_gain, best_choice[0].final_gold, -best_choice[0].total_minutes)
        ):
            best_choice = (result, course)

    if best_choice is None:
        if best_job is None:
            return AdaptiveDecision(None, explanation="服务器当前没有可用于优化的课程或岗位")
        return AdaptiveDecision(
            "work",
            career_type=int(getattr(best_job, "career_type", 0)),
            job_sub_event=int(getattr(best_job, "sub_event_type", 0)),
            job_name=str(getattr(best_job, "name", "")),
            explanation="当前资金无法形成可持续学习闭环，先执行单位时间金币最高的岗位",
        )

    result, selected_course = best_choice
    first = result.plan[0]
    parts = first.name.split(":", 3)
    if first.kind == "study":
        action = AdaptiveDecision(
            "study",
            course_sub_event=int(parts[1]),
            course_name=str(getattr(selected_course, "name", "")),
            job_name=str(getattr(best_job, "name", "")) if best_job else "",
            explanation=(
                f"动态最优计划：预计学习 {result.study_minutes} 分钟、打工 {result.work_minutes} 分钟，"
                f"学习收益 {result.learning_gain:g}，期末金币 {result.final_gold:g}"
            ),
            result=result,
        )
    else:
        action = AdaptiveDecision(
            "work",
            career_type=int(parts[1]),
            job_sub_event=int(parts[2]),
            course_name=str(getattr(selected_course, "name", "")),
            job_name=str(getattr(best_job, "name", "")) if best_job else "",
            explanation=(
                f"动态最优计划先赚取学费：预计学习 {result.study_minutes} 分钟、"
                f"打工 {result.work_minutes} 分钟，期末金币 {result.final_gold:g}"
            ),
            result=result,
        )
    return action


@dataclass
class _Label:
    elapsed: int
    gold: Fraction
    learning: Fraction
    limited_counts: tuple[int, ...]
    work_started: bool
    study_start_penalty: int
    parent: Optional["_Label"] = None
    activity_index: Optional[int] = None


def fatigue_weighted_minutes(
    start_minute: int,
    duration_minutes: int,
    bands: Iterable[FatigueBand] = OFFICIAL_FATIGUE_BANDS,
) -> Fraction:
    """Integrate fatigue exactly, including an activity crossing a boundary."""

    end = start_minute + duration_minutes
    cursor = start_minute
    weighted = Fraction(0)
    previous_end = 0
    for band in bands:
        band_start = previous_end
        overlap_start = max(cursor, band_start)
        overlap_end = min(end, band.end_minute)
        if overlap_end > overlap_start:
            weighted += (overlap_end - overlap_start) * band.multiplier
            cursor = overlap_end
        previous_end = band.end_minute
        if cursor >= end:
            return weighted
    if cursor < end:
        raise ValueError("活动超出疲劳区间覆盖范围")
    return weighted


def _effects(request: OptimizationRequest, activity: Activity, elapsed: int) -> tuple[Fraction, Fraction, Fraction]:
    start = request.initial_active_minutes + elapsed
    weighted = fatigue_weighted_minutes(start, activity.duration_minutes, request.fatigue_bands)
    coin_minutes = weighted if activity.fatigue_affects_coin else Fraction(activity.duration_minutes)
    learning_minutes = weighted if activity.fatigue_affects_learning else Fraction(activity.duration_minutes)
    coin_change = activity.coin_per_minute * coin_minutes + activity.fixed_coin_change
    return coin_change, activity.learning_per_minute * learning_minutes, weighted


def _prune(labels: dict[tuple[Fraction, tuple[int, ...], bool], _Label]) -> dict[tuple[Fraction, tuple[int, ...], bool], _Label]:
    groups: dict[tuple[tuple[int, ...], bool], list[_Label]] = {}
    for label in labels.values():
        groups.setdefault((label.limited_counts, label.work_started), []).append(label)

    kept: dict[tuple[Fraction, tuple[int, ...], bool], _Label] = {}
    for group in groups.values():
        best_learning: Optional[Fraction] = None
        for label in sorted(group, key=lambda item: (item.gold, item.learning, -item.study_start_penalty), reverse=True):
            if best_learning is not None and label.learning <= best_learning:
                continue
            kept[(label.gold, label.limited_counts, label.work_started)] = label
            best_learning = label.learning
    return kept


def optimize_daily_plan(request: OptimizationRequest) -> OptimizationResult:
    """Find the exact lexicographic optimum under the supplied discrete actions.

    Objective order: maximize learning, then ending gold, then put learning
    earlier, then use less active time. Intermediate balances only need to stay
    at or above ``minimum_balance`` (normally zero); ``safety_floor`` applies
    to the ending balance through ``required_end_gold``.
    """

    limited_indices = [index for index, item in enumerate(request.activities) if item.repeat_limit is not None]
    count_position = {activity_index: position for position, activity_index in enumerate(limited_indices)}
    states: list[dict[tuple[Fraction, tuple[int, ...], bool], _Label]] = [dict() for _ in range(request.horizon_minutes + 1)]
    root = _Label(0, request.initial_gold, Fraction(0), (0,) * len(limited_indices), False, 0)
    states[0][(root.gold, root.limited_counts, root.work_started)] = root

    for elapsed in range(request.horizon_minutes + 1):
        if not states[elapsed]:
            continue
        states[elapsed] = _prune(states[elapsed])
        if len(states[elapsed]) > request.max_labels_per_minute:
            raise RuntimeError(f"第 {elapsed} 分钟的状态数过多；请缩短时域或减少活动种类")
        for label in states[elapsed].values():
            for index, activity in enumerate(request.activities):
                finish = elapsed + activity.duration_minutes
                if finish > request.horizon_minutes:
                    continue
                if request.strict_study_first and label.work_started and activity.kind == "study":
                    continue
                counts = list(label.limited_counts)
                if index in count_position:
                    position = count_position[index]
                    if counts[position] >= (activity.repeat_limit or 0):
                        continue
                    counts[position] += 1
                coin_change, learning_gain, _ = _effects(request, activity, elapsed)
                next_gold = label.gold + coin_change
                if next_gold < request.minimum_balance:
                    continue
                next_label = _Label(
                    elapsed=finish,
                    gold=next_gold,
                    learning=label.learning + learning_gain,
                    limited_counts=tuple(counts),
                    work_started=label.work_started or activity.kind == "work",
                    study_start_penalty=label.study_start_penalty + (elapsed if activity.kind == "study" else 0),
                    parent=label,
                    activity_index=index,
                )
                key = (next_label.gold, next_label.limited_counts, next_label.work_started)
                current = states[finish].get(key)
                if current is None or (next_label.learning, -next_label.study_start_penalty) > (
                    current.learning,
                    -current.study_start_penalty,
                ):
                    states[finish][key] = next_label

    candidates = [
        label
        for minute_states in states
        for label in minute_states.values()
        if label.gold >= request.required_end_gold
    ]
    if not candidates:
        highest = max((label.gold for minute_states in states for label in minute_states.values()), default=request.initial_gold)
        return OptimizationResult(
            feasible=False,
            reason=f"在给定时长和活动下无法达到期末金币要求；可搜索到的最高余额为 {_display(highest)}",
            final_gold=_display(request.initial_gold),
            minimum_gold=_display(request.initial_gold),
        )

    best = max(candidates, key=lambda item: (item.learning, item.gold, -item.study_start_penalty, -item.elapsed))
    activity_indices: list[int] = []
    cursor = best
    while cursor.parent is not None:
        assert cursor.activity_index is not None
        activity_indices.append(cursor.activity_index)
        cursor = cursor.parent
    activity_indices.reverse()

    steps: list[PlanStep] = []
    gold = request.initial_gold
    elapsed = 0
    minimum_gold = gold
    study_cost = Fraction(0)
    work_income = Fraction(0)
    learning = Fraction(0)
    study_minutes = 0
    work_minutes = 0
    work_receipts: list[tuple[int, int, Fraction]] = []
    for index in activity_indices:
        activity = request.activities[index]
        coin_change, learning_gain, weighted = _effects(request, activity, elapsed)
        gold += coin_change
        minimum_gold = min(minimum_gold, gold)
        learning += learning_gain
        if activity.kind == "study":
            study_minutes += activity.duration_minutes
            study_cost += max(Fraction(0), -coin_change)
        else:
            work_minutes += activity.duration_minutes
            work_income += max(Fraction(0), coin_change)
            work_receipts.append((elapsed + activity.duration_minutes, work_minutes, max(Fraction(0), coin_change)))
        steps.append(
            PlanStep(
                name=activity.name,
                kind=activity.kind,
                start_minute=elapsed,
                end_minute=elapsed + activity.duration_minutes,
                average_fatigue_multiplier=_display(weighted / activity.duration_minutes),
                coin_change=_display(coin_change),
                learning_gain=_display(learning_gain),
                gold_after=_display(gold),
            )
        )
        elapsed += activity.duration_minutes

    cumulative_work = Fraction(0)
    break_even_at: Optional[int] = None
    break_even_work_minutes: Optional[int] = None
    for end_minute, cumulative_minutes, receipt in work_receipts:
        cumulative_work += receipt
        if cumulative_work >= study_cost:
            break_even_at = end_minute
            break_even_work_minutes = cumulative_minutes
            break

    block_rows: list[dict[str, object]] = []
    for step in steps:
        if (
            block_rows
            and block_rows[-1]["name"] == step.name
            and block_rows[-1]["kind"] == step.kind
            and block_rows[-1]["end_minute"] == step.start_minute
            and block_rows[-1]["average_fatigue_multiplier"] == step.average_fatigue_multiplier
        ):
            row = block_rows[-1]
            row["repeats"] = int(row["repeats"]) + 1
            row["end_minute"] = step.end_minute
            row["coin_change"] = float(row["coin_change"]) + step.coin_change
            row["learning_gain"] = float(row["learning_gain"]) + step.learning_gain
            row["gold_after"] = step.gold_after
        else:
            block_rows.append(
                {
                    "name": step.name,
                    "kind": step.kind,
                    "repeats": 1,
                    "start_minute": step.start_minute,
                    "end_minute": step.end_minute,
                    "average_fatigue_multiplier": step.average_fatigue_multiplier,
                    "coin_change": step.coin_change,
                    "learning_gain": step.learning_gain,
                    "gold_after": step.gold_after,
                }
            )
    plan_blocks = tuple(PlanBlock(**row) for row in block_rows)

    return OptimizationResult(
        feasible=True,
        reason="找到满足非负余额、学费回补和期末安全线的精确离散最优解",
        plan=tuple(steps),
        plan_blocks=plan_blocks,
        total_minutes=elapsed,
        study_minutes=study_minutes,
        work_minutes=work_minutes,
        study_cost=_display(study_cost),
        work_income=_display(work_income),
        learning_gain=_display(learning),
        final_gold=_display(gold),
        minimum_gold=_display(minimum_gold),
        break_even_at_minute=break_even_at,
        work_minutes_to_break_even=break_even_work_minutes,
    )
