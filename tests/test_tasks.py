import random
from fractions import Fraction

import pytest

from mathbot.tasks import ALL_TOPICS, GENERATORS, PREREQ, TAGS, TOPICS, TOPICS5, TRAPS, BadFormat, Task, check, make_task, match_trap, parse_number


def canonical(t: Task) -> str:
    if t.kind in ("num", "reduced"):
        v = t.value
        return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
    if t.kind == "ints":
        return " ".join(str(i) for i in t.value)
    return t.value[0]


@pytest.mark.parametrize("topic", list(ALL_TOPICS))
@pytest.mark.parametrize("level", [1, 2, 3])
def test_generators_consistent(topic, level):
    rnd = random.Random(level * 7)
    seen = set()
    for _ in range(300):
        t = make_task(topic, level, rnd)
        seen.add(t.text)
        assert t.topic == topic and t.level == level
        assert check(t, canonical(t)), t.text           # правильный ответ принимается
        assert Task.load(t.dump()).text == t.text        # сохранение в БД
        assert Task.load(t.dump()).value == t.value
        assert t.explanation and t.correct_display()
        if t.kind == "num":
            assert not check(t, str(t.value + 1) if t.value.denominator == 1 else "12345")
    assert len(seen) > 5  # достаточное разнообразие


def test_all_topics_have_all_levels():
    for topic in ALL_TOPICS:
        for lvl in (1, 2, 3):
            assert GENERATORS[topic][lvl]


def test_parse_variants():
    assert parse_number("0,5") == Fraction(1, 2)
    assert parse_number("−3") == Fraction(-3)
    assert parse_number(" 3 / 4 ") is None or parse_number("3/4") == Fraction(3, 4)
    assert parse_number("1 1/2") == Fraction(3, 2)
    assert parse_number("-1 1/2") == Fraction(-3, 2)
    assert parse_number("abc") is None
    assert parse_number("1/0") is None


def test_reduced_requires_lowest_terms():
    t = Task("fractions_add", 1, "x", "reduced", Fraction(1, 2))
    assert check(t, "1/2") and not check(t, "2/4") and not check(t, "3/6")
    t2 = Task("fractions_add", 1, "x", "num", Fraction(1, 2))
    assert check(t2, "2/4") and check(t2, "0,5") and check(t2, "0.5")


def test_text_answers():
    t = Task("divisibility", 1, "x", "text", ["да"])
    assert check(t, "Да") and check(t, "yes") and not check(t, "нет")
    with pytest.raises(BadFormat):
        check(t, "5")
    s = Task("fractions_add", 1, "x", "text", [">"])
    assert check(s, ">") and check(s, "больше") and not check(s, "<")
    e = Task("equations", 2, "x", "text", ["3x"])
    assert check(e, "3x") and check(e, "3 х") and check(e, "3*x") and not check(e, "4x")


def test_ints():
    t = Task("coordinates", 2, "x", "ints", [3, -2])
    assert check(t, "3; −2") and check(t, "(3, -2)") and not check(t, "-2 3")
    with pytest.raises(BadFormat):
        check(t, "3")


def test_traps_detect_typical_mistakes():
    rnd = random.Random(11)
    seen_tags = set()
    for gen in TRAPS:
        found = 0
        for _ in range(200):
            t = gen(rnd)
            for v, tag in TRAPS[gen](t):
                from mathbot.tasks import add_trap
                add_trap(t, v, tag)
            for key, tag in t.traps:
                assert tag in TAGS
                seen_tags.add(tag)
                raw = " ".join(map(str, key)) if isinstance(key, list) else str(key)
                assert match_trap(t, raw) in {g for k, g in t.traps if k == key}, (t.text, key, tag)
                assert not check(t, raw), (t.text, key)   # ловушка — всегда неверный ответ
                found += 1
        assert found, gen.__name__
    for topic in TOPICS5:
        for lvl in (1, 2, 3):
            for _ in range(100):
                t = make_task(topic, lvl, rnd)
                for key, tag in t.traps:
                    seen_tags.add(tag)
                    assert not check(t, str(key))
    assert {"add_num_den", "no_flip", "gcd_lcm", "pct_divide", "sign_rule", "minus_minus", "op_order", "decimal_point", "mixed_borrow"} <= seen_tags


def test_prereq_topics_exist():
    for parent, pre in PREREQ.items():
        assert parent in TOPICS
        assert all(p in TOPICS5 for p in pre)


def test_every_topic_has_lesson():
    from mathbot.lessons import LESSONS
    assert set(ALL_TOPICS) <= set(LESSONS)
