# 学习科目轮换功能验证

功能状态：已完整实现并测试通过

## 轮换顺序
- culture (智力) → physical (力量) → art (魅力) → culture (智力) → ...

## 配置方式
```json
{
  "school": {
    "attribute_rotation": true,
    "course_sub_event": 0
  }
}
```

## 测试覆盖
- test_school_attribute_rotation_cycles_all_subjects ✅
- test_school_attribute_rotation_persists_across_instances ✅
- test_run_once_uses_rotated_school_attribute_when_enabled ✅
- test_run_once_rotates_school_attribute_when_course_unavailable ✅

总计：56/56 测试通过

