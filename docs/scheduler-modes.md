# 调度机制说明 / Scheduler Modes

## 中文

助手保留两套互相独立的学习/打工调度方式，可在“设置 → 动态收益优化”中切换。

### 原调度模式（默认）

“测试：启用动态数学模型调度”保持关闭时，程序继续使用原来的金币阈值逻辑：金币达到阈值优先学习，金币不足则打工。学习和打工都可分别选择“不限次数”或开启每日次数限制；达到上限后当天不再安排该任务，第二天由每日进度自动清零。用户原先选择的课程、岗位、雇佣和照顾设置全部继续生效。

在“自动调度”分类可额外开启两个平衡选项：

- **学习/打工均衡轮换**：当学习与打工的每日次数差达到设定上限（默认 3）时，强制补齐落后一方；次数接近时仍按金币阈值优先学习。避免金币充足时长期只学习、打工工分不涨。开启后打工次数可配置每日上限，但轮换仅在两侧都未达上限时生效。
- **学习科目循环轮换**：开启后每轮学习在力量（physical）、智力（culture）、魅力（art）之间循环切换，避免长期只学单一科目导致偏科；该轮换状态会持久化，重启后从上次科目继续。手动固定课程时不参与轮换。

### 动态数学模型（测试）

主动开启测试开关后，程序在每次准备开课或开工前重新读取服务器当前目录，并以“在金币约束下让学习收益最大”为目标搜索下一步。模型使用：

- 课程的时长、学费、属性收益和可执行状态；
- 岗位的时长、当前金币收益和可执行状态；
- 学习与打工的体力、清洁消耗；
- 饼干和香皂片恢复状态所需的折算金币；
- 当天累计学习/打工时长与 100% / 25% / 10% 疲劳区间；
- 期末金币安全线，以及是否补回当天开始金币。

若服务器文本包含体力或清洁消耗，模型优先使用服务器值；缺少字段时使用设置页中的实测默认值。课程升级、岗位变化或奖励变化后，下一轮会重新计算，不沿用写死的历史方案。

### 雇佣好友失败降级

两种模式都遵循同一降级规则。好友返回“今天很累了”或今日不可继续被雇佣时，该好友会加入当天跳过名单，当前岗位立即改为无好友开工；下一轮尝试其他好友。所有好友均不可雇佣时仍会正常独自打工，名单次日自动清零。

## English

The assistant keeps two independent school/work scheduling modes. They can be switched under **Settings → Dynamic Yield Optimization**.

### Classic scheduler (default)

When **Test: enable dynamic mathematical scheduler** is off, the existing coin-threshold behavior remains unchanged: study above the threshold and work below it. Study and work can each be unlimited or use an independent daily run limit. Once a limit is reached, that task is skipped until daily progress resets the next day. Existing course, job, hiring, and care settings are preserved.

### Dynamic mathematical scheduler (experimental)

When explicitly enabled, the assistant refreshes the current server course and job catalogs before each task and searches for the next action that maximizes learning under the coin constraint. It considers duration, tuition, attribute reward, job pay, stamina and cleanliness costs, supply replacement cost, shared fatigue bands, the ending coin floor, and the optional requirement to restore the day's opening coin balance.

Server-provided resource costs take priority. Configured measured defaults are used only when those fields are absent. The plan is recalculated after course promotion, job availability changes, or reward changes.

### Friend-hiring fallback

Both schedulers share the same fallback. If a friend is reported as too tired or unavailable for further hiring today, that friend is skipped for the rest of the day and the same job immediately starts without a hired friend. Other friends are tried on later jobs; when all are unavailable, solo work continues normally. The skip list resets the next day.
