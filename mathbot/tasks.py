"""Генераторы устных задач по математике 6 класса (программа РФ) и проверка ответов.

У каждой темы три уровня: 1 — совсем просто, 2 — обычный, 3 — посложнее.
Чтобы добавить свою задачу, напишите функцию `def g_...(rnd) -> Task` и добавьте
её в словарь GENERATORS нужной темы и уровня.
"""
from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field
from fractions import Fraction

# ---------------------------------------------------------------- темы
TOPICS: dict[str, str] = {
    "divisibility": "Делимость, НОД и НОК",
    "fractions_add": "Обыкновенные дроби: сокращение, сравнение, сложение",
    "fractions_muldiv": "Умножение и деление дробей",
    "ratio": "Отношения и пропорции",
    "percent": "Проценты",
    "circle": "Окружность и круг",
    "negatives": "Положительные и отрицательные числа, модуль",
    "rational_add": "Сложение и вычитание рациональных чисел",
    "rational_muldiv": "Умножение и деление рациональных чисел",
    "equations": "Выражения, раскрытие скобок, уравнения",
    "coordinates": "Координатная плоскость",
    "word_problems": "Текстовые задачи: движение и работа",
    "mental": "Устный счёт: приёмы",
}

# ---------------------------------------------------------------- задача


@dataclass
class Task:
    topic: str
    level: int
    text: str
    kind: str  # num | reduced | text | ints
    value: object  # Fraction | list[str] | list[int]
    explanation: str = ""
    buttons: tuple[str, ...] = ()  # быстрые кнопки ответа
    traps: list = field(default_factory=list)  # [[неверный ответ, метка ошибки], ...]

    # --- хранение в БД
    def dump(self) -> str:
        v = self.value
        if self.kind in ("num", "reduced"):
            v = [v.numerator, v.denominator]
        return json.dumps(
            {"topic": self.topic, "level": self.level, "text": self.text, "kind": self.kind,
             "value": v, "explanation": self.explanation, "buttons": list(self.buttons), "traps": self.traps},
            ensure_ascii=False,
        )

    @staticmethod
    def load(s: str) -> "Task":
        d = json.loads(s)
        if d["kind"] in ("num", "reduced"):
            d["value"] = Fraction(*d["value"])
        d["buttons"] = tuple(d["buttons"])
        d.setdefault("traps", [])
        return Task(**d)

    # --- правильный ответ для показа
    def correct_display(self) -> str:
        if self.kind in ("num", "reduced"):
            return fmt(self.value)
        if self.kind == "ints":
            sep = ":" if "«:»" in self.text else "; "
            return sep.join(fmt(i) for i in self.value)
        return str(self.value[0])


def fmt(x: Fraction | int) -> str:
    x = Fraction(x)
    if x.denominator == 1:
        return str(x.numerator).replace("-", "−")
    s = f"{abs(x.numerator)}/{x.denominator}"
    return ("−" if x < 0 else "") + s


# ---------------------------------------------------------------- разбор и проверка ответа
_MINUS = str.maketrans({"−": "-", "–": "-", "—": "-", "，": ",", "х": "x", "Х": "x", "X": "x"})
_YES = {"да", "д", "yes", "y", "+", "ага"}
_NO = {"нет", "н", "no", "n", "-", "неа"}
_SIGNS = {">": ">", "<": "<", "=": "=", "больше": ">", "меньше": "<", "равно": "=", "равны": "="}


def _clean(s: str) -> str:
    return s.translate(_MINUS).strip().lower()


def parse_number(raw: str) -> Fraction | None:
    s = _clean(raw).replace(" ", " ")
    s = re.sub(r"\s+", " ", s)
    m = re.fullmatch(r"(-?)(\d+) (\d+)/(\d+)", s)  # смешанное число
    if m:
        d = int(m.group(4))
        if d == 0:
            return None
        v = int(m.group(2)) + Fraction(int(m.group(3)), d)
        return -v if m.group(1) else v
    s = s.replace(" ", "").replace(",", ".")
    m = re.fullmatch(r"([+-]?)(\d+)/(\d+)", s)
    if m:
        d = int(m.group(3))
        if d == 0:
            return None
        v = Fraction(int(m.group(2)), d)
        return -v if m.group(1) == "-" else v
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
        return Fraction(s)
    return None


def _norm_text(raw: str) -> str:
    s = _clean(raw).replace(" ", "").replace("*", "").replace("·", "")
    return s


class BadFormat(Exception):
    """Ответ не удалось разобрать — можно переспросить без штрафа."""


def check(task: Task, raw: str) -> bool:
    raw = raw or ""
    if task.kind in ("num", "reduced"):
        v = parse_number(raw)
        if v is None:
            raise BadFormat("Введи число: например 5, −3, 3/4 или 0,5")
        if v != task.value:
            return False
        if task.kind == "reduced":
            m = re.search(r"(\d+)\s*/\s*(\d+)", raw)
            if m and math.gcd(int(m.group(1)), int(m.group(2))) != 1:
                return False  # дробь не сокращена
        return True
    if task.kind == "ints":
        nums = [int(n) for n in re.findall(r"-?\d+", _clean(raw))]
        if len(nums) != len(task.value):
            raise BadFormat("Нужно ввести числа, например: 3; −2")
        return nums == list(task.value)
    # text
    s = _norm_text(raw)
    if not s:
        raise BadFormat("Напиши ответ")
    accepted = [_norm_text(a) for a in task.value]
    if s in accepted:
        return True
    yn = "да" in accepted or "нет" in accepted
    signs = any(a in _SIGNS.values() for a in accepted)
    if yn and s in _YES:
        s = "да"
    elif yn and s in _NO:
        s = "нет"
    elif signs:
        s = _SIGNS.get(s, s)
    if s in accepted:
        return True
    if yn and s not in ("да", "нет"):
        raise BadFormat("Ответь «да» или «нет»")
    if signs and s not in _SIGNS.values():
        raise BadFormat("Поставь знак: >, < или =")
    return False


def match_trap(task: Task, raw: str) -> str | None:
    """Если неверный ответ совпал с типичной ошибкой — вернуть её метку."""
    for key, tag in task.traps:
        try:
            if task.kind in ("num", "reduced"):
                v = parse_number(raw)
                if v is not None and v == Fraction(key):
                    return tag
            elif task.kind == "ints":
                nums = [int(n) for n in re.findall(r"-?\d+", _clean(raw))]
                if nums == list(key):
                    return tag
            elif _norm_text(raw) == _norm_text(str(key)):
                return tag
        except (ValueError, ZeroDivisionError):
            continue
    return None


# метка -> (подсказка ребёнку, пояснение для мамы)
TAGS: dict[str, tuple[str, str]] = {
    "add_num_den": ("Дроби с разными знаменателями нельзя складывать «сверху и снизу». Сначала приведи их к общему знаменателю.",
                    "складывает/вычитает отдельно числители и знаменатели"),
    "no_flip": ("При делении на дробь вторую дробь нужно перевернуть и умножать.", "делит дробь на дробь, не переворачивая вторую"),
    "gcd_lcm": ("НОД — наибольшее число, на которое делятся оба числа. НОК — наименьшее число, которое делится на оба. Тут перепутаны НОД и НОК.",
                "путает НОД и НОК"),
    "pct_divide": ("Процент — это сотая доля: чтобы найти p% от n, считаем n·p/100. Здесь число разделили на процент.",
                   "делит число на процент вместо умножения на p/100"),
    "pct_forgot_base": ("Ты нашёл(ла) только сам процент. Его ещё нужно прибавить к цене или вычесть из неё.",
                        "находит процент, но забывает прибавить/вычесть его от исходного числа"),
    "pct_direction": ("Проверь направление: «выросла» — прибавляем, «упала» — вычитаем.", "путает рост и падение при процентах"),
    "abs_sign": ("Модуль — расстояние до нуля, оно не бывает отрицательным: |−7| = 7.", "не снимает знак минус в модуле"),
    "neg_compare": ("Из двух отрицательных чисел больше то, которое ближе к нулю (у него меньше модуль): −3 > −5.",
                    "сравнивает отрицательные числа по модулю"),
    "sign_rule": ("Следи за знаком: разные знаки — результат отрицательный, одинаковые — положительный.", "ошибки в знаках при умножении/сложении"),
    "minus_minus": ("Минус на минус даёт плюс. Вычесть отрицательное число — то же, что прибавить положительное.", "путает правило «минус на минус»"),
    "coord_swap": ("Первое число в скобках — абсцисса (x), второе — ордината (y).", "путает координаты x и y"),
    "symmetry_axis": ("Симметрия относительно оси x меняет знак у y, относительно оси y — знак у x.", "путает оси симметрии"),
    "eq_transfer": ("При переносе числа через знак «=» его знак меняется. Проверь себя подстановкой.", "ошибки знака при переносе в уравнении"),
    "eq_bracket": ("Сначала раздели обе части на множитель перед скобкой, потом перенеси число.", "ошибка с множителем перед скобкой"),
    "eq_divide": ("Чтобы найти x в уравнении m·x = R, нужно R разделить на m.", "умножает вместо деления в уравнении"),
    "decimal_point": ("Посчитай знаки после запятой: в произведении их столько же, сколько в обоих множителях вместе.", "ошибка с запятой при умножении/делении десятичных"),
    "decimal_align": ("При сложении и вычитании десятичных пиши запятую под запятой и дописывай нули.", "ошибка при выравнивании запятых"),
    "op_order": ("Сначала скобки, потом умножение и деление, и только потом сложение и вычитание.", "нарушает порядок действий"),
    "mixed_borrow": ("Когда дробная часть уменьшаемого меньше, «займи» единицу у целой части.", "ошибка при вычитании смешанных чисел"),
    "idk": ("", "нажимает «Не знаю»"),
}

# ---------------------------------------------------------------- вспомогательное


def _frac(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


def _coprime_pair(rnd, lo, hi):
    while True:
        a, b = rnd.randint(lo, hi), rnd.randint(lo, hi)
        if a != b and math.gcd(a, b) == 1:
            return a, b


def _signed(n: int) -> str:
    """Число в скобках, если отрицательное."""
    return f"({fmt(n)})" if n < 0 else str(n)


def _dec(x: Fraction) -> str:
    """Десятичная запись (для конечных дробей)."""
    return str(float(x)).replace(".", ",").rstrip("0").rstrip(",") if x.denominator != 1 else str(int(x))


# ================================================================ 1. ДЕЛИМОСТЬ
def d_sign(rnd) -> Task:
    k = rnd.choice([2, 3, 5, 9, 10])
    n = rnd.randint(30, 999)
    if rnd.random() < 0.5:
        n -= n % k
        n = max(n, k * 3)
    ok = n % k == 0
    return Task("divisibility", 1, f"Делится ли {n} на {k}? (да/нет)", "text", ["да" if ok else "нет"],
                f"{n} {'делится' if ok else 'не делится'} на {k}.", ("Да", "Нет"))


def d_gcd(rnd) -> Task:
    g = rnd.randint(2, 9)
    p, q = _coprime_pair(rnd, 2, 7)
    a, b = g * p, g * q
    return Task("divisibility", 2, f"Найди НОД({a}, {b}) — наибольший общий делитель.", "num", Fraction(g),
                f"Оба числа делятся на {g}, и больше общих множителей нет.")


def d_lcm(rnd) -> Task:
    a, b = rnd.choice([(4, 6), (6, 8), (6, 9), (4, 10), (3, 5), (4, 5), (6, 10), (8, 12), (6, 15), (9, 12), (10, 15), (12, 18), (5, 6), (3, 7)])
    return Task("divisibility", 2, f"Найди НОК({a}, {b}) — наименьшее общее кратное.", "num", Fraction(a * b // math.gcd(a, b)),
                f"Ищем наименьшее число, которое делится и на {a}, и на {b}.")


def d_prime(rnd) -> Task:
    n = rnd.randint(11, 60)
    pr = all(n % i for i in range(2, int(n ** 0.5) + 1))
    return Task("divisibility", 3, f"Простое ли число {n}? (да/нет)", "text", ["да" if pr else "нет"],
                f"{n} {'простое — делится только на 1 и само на себя' if pr else 'составное — у него есть другие делители'}.",
                ("Да", "Нет"))


def d_gcd_hard(rnd) -> Task:
    g = rnd.choice([6, 8, 12, 4, 9])
    p, q = _coprime_pair(rnd, 3, 9)
    a, b = g * p, g * q
    return Task("divisibility", 3, f"Найди НОД({a}, {b}).", "num", Fraction(g), f"Наибольшее число, на которое делятся оба: {g}.")


# ================================================================ 2. ДРОБИ: сокращение, сравнение, сложение
def fa_reduce(rnd) -> Task:
    while True:
        p, q = rnd.randint(1, 7), rnd.randint(2, 9)
        if p < q and math.gcd(p, q) == 1:
            break
    k = rnd.randint(2, 6)
    return Task("fractions_add", 1, f"Сократи дробь {p * k}/{q * k} до несократимой.", "reduced", Fraction(p, q),
                f"Делим числитель и знаменатель на {k}: {_frac(Fraction(p, q))}.")


def fa_cmp_same(rnd) -> Task:
    d = rnd.randint(5, 12)
    a, b = rnd.sample(range(1, d), 2)
    sign = ">" if a > b else "<"
    return Task("fractions_add", 1, f"Поставь знак: {a}/{d} ? {b}/{d}  (введи >, < или =)", "text", [sign],
                "Знаменатели одинаковые — больше та дробь, у которой больше числитель.", (">", "<", "="))


def fa_add_same(rnd) -> Task:
    d = rnd.randint(5, 12)
    a = rnd.randint(1, d - 2)
    b = rnd.randint(1, d - 1 - a)
    return Task("fractions_add", 1, f"Вычисли: {a}/{d} + {b}/{d}", "num", Fraction(a + b, d),
                f"Знаменатель не меняем, складываем числители: {a + b}/{d}.")


def fa_add_mult(rnd) -> Task:
    q = rnd.choice([2, 3, 4, 5])
    m = rnd.choice([2, 3])
    d2 = q * m
    a = rnd.randint(1, q - 1) if q > 2 else 1
    b = rnd.randint(1, d2 - 1)
    if rnd.random() < 0.5:
        x = Fraction(a, q) + Fraction(b, d2)
        return Task("fractions_add", 2, f"Вычисли: {a}/{q} + {b}/{d2}", "num", x,
                    f"Приводим к знаменателю {d2}: {a * m}/{d2} + {b}/{d2} = {_frac(x)}. Можно записать и в виде смешанного числа.")
    x = Fraction(a, q) - Fraction(b, d2)
    if x <= 0:
        return fa_add_mult(rnd)
    return Task("fractions_add", 2, f"Вычисли: {a}/{q} − {b}/{d2}", "num", x,
                f"Приводим к знаменателю {d2}: {a * m}/{d2} − {b}/{d2} = {_frac(x)}.")


def fa_cmp_diff(rnd) -> Task:
    while True:
        q1, q2 = _coprime_pair(rnd, 2, 6)
        a, b = rnd.randint(1, q1 - 1), rnd.randint(1, q2 - 1)
        if Fraction(a, q1) != Fraction(b, q2):
            break
    sign = ">" if Fraction(a, q1) > Fraction(b, q2) else "<"
    return Task("fractions_add", 2, f"Поставь знак: {a}/{q1} ? {b}/{q2}  (введи >, < или =)", "text", [sign],
                "Приведи к общему знаменателю и сравни числители.", (">", "<"))


def fa_add_diff(rnd) -> Task:
    q1, q2 = rnd.choice([(2, 3), (2, 5), (3, 4), (3, 5), (4, 5), (2, 7), (3, 2), (6, 4), (6, 9), (4, 6)])
    a, b = rnd.randint(1, q1 - 1), rnd.randint(1, q2 - 1)
    if rnd.random() < 0.5:
        x = Fraction(a, q1) + Fraction(b, q2)
        op = "+"
    else:
        x = Fraction(a, q1) - Fraction(b, q2)
        op = "−"
        if x <= 0:
            return fa_add_diff(rnd)
    lcm = q1 * q2 // math.gcd(q1, q2)
    return Task("fractions_add", 3, f"Вычисли: {a}/{q1} {op} {b}/{q2}", "num", x,
                f"Общий знаменатель {lcm}: {a * lcm // q1}/{lcm} {op} {b * lcm // q2}/{lcm} = {_frac(x)}.")


# ================================================================ 3. УМНОЖЕНИЕ И ДЕЛЕНИЕ ДРОБЕЙ
def fm_of_number(rnd) -> Task:
    q = rnd.choice([2, 3, 4, 5, 10])
    p = rnd.randint(1, q - 1)
    n = q * rnd.randint(2, 8)
    return Task("fractions_muldiv", 1, f"Найди {p}/{q} от числа {n}.", "num", Fraction(n * p, q),
                f"{n} : {q} · {p} = {n * p // q}.")


def fm_mul(rnd) -> Task:
    a, b = rnd.sample([1, 2, 3, 4, 5], 2)
    c, d = rnd.sample([2, 3, 4, 5, 6, 7], 2)
    if a == b or c == d or a >= c:
        return fm_mul(rnd)
    x = Fraction(a, c) * Fraction(b, d)
    return Task("fractions_muldiv", 2, f"Вычисли: {a}/{c} · {b}/{d}", "num", x,
                f"Числитель на числитель, знаменатель на знаменатель, потом сократить: {_frac(x)}.")


def fm_number_by_part(rnd) -> Task:
    q = rnd.choice([2, 3, 4, 5])
    p = rnd.randint(1, q - 1)
    n = rnd.randint(2, 9) * q
    part = n * p // q
    return Task("fractions_muldiv", 2, f"{p}/{q} числа равны {part}. Найди это число.", "num", Fraction(n),
                f"{part} : {p} · {q} = {n}.")


def fm_div(rnd) -> Task:
    a, b = rnd.choice([(1, 2), (1, 3), (2, 3), (3, 4), (1, 4), (2, 5), (3, 5)]), rnd.choice([(1, 2), (1, 3), (2, 3), (1, 4), (3, 4), (2, 5)])
    x = Fraction(*a) / Fraction(*b)
    if x == 1:
        return fm_div(rnd)
    return Task("fractions_muldiv", 3, f"Вычисли: {a[0]}/{a[1]} : {b[0]}/{b[1]}", "num", x,
                f"Делить на дробь — значит умножать на перевёрнутую: {a[0]}/{a[1]} · {b[1]}/{b[0]} = {_frac(x)}.")


def fm_mixed_word(rnd) -> Task:
    q = rnd.choice([3, 4, 5])
    n = q * rnd.randint(3, 8)
    p = rnd.randint(1, q - 1)
    rest = n - n * p // q
    return Task("fractions_muldiv", 3, f"В книге {n} страниц. Саша прочитал {p}/{q} книги. Сколько страниц осталось?",
                "num", Fraction(rest), f"Прочитано {n * p // q}, осталось {n} − {n * p // q} = {rest}.")


# ================================================================ 4. ОТНОШЕНИЯ И ПРОПОРЦИИ
def r_proportion(rnd) -> Task:
    b = rnd.randint(2, 6)
    a = rnd.randint(1, 5)
    k = rnd.randint(2, 5)
    return Task("ratio", 1, f"Найди x в пропорции: {a} : {b} = x : {b * k}", "num", Fraction(a * k),
                f"Знаменатель вырос в {k} раз, значит x = {a}·{k} = {a * k}.")


def r_price(rnd) -> Task:
    n = rnd.randint(2, 6)
    price = rnd.randint(3, 9) * 5
    m = rnd.randint(n + 1, 10)
    return Task("ratio", 1, f"{n} тетради стоят {n * price} руб. Сколько стоят {m} таких тетрадей?", "num",
                Fraction(m * price), f"Одна тетрадь стоит {price} руб., {m}·{price} = {m * price}.")


def r_reduce_ratio(rnd) -> Task:
    p, q = _coprime_pair(rnd, 1, 7)
    k = rnd.randint(2, 6)
    return Task("ratio", 2, f"Упрости отношение {p * k} : {q * k}. Запиши через «:»", "ints", [p, q],
                f"Делим оба числа на {k}: {p} : {q}.")


def r_part(rnd) -> Task:
    m, g = _coprime_pair(rnd, 2, 9)
    k = rnd.randint(2, 4)
    m, g = m * k, g * k
    x = Fraction(g, m + g)
    return Task("ratio", 2, f"В классе {m} мальчиков и {g} девочек. Какую часть класса составляют девочки? (дробью)", "num", x,
                f"Девочек {g} из {m + g}: {g}/{m + g} = {_frac(x)}.")


def r_split(rnd) -> Task:
    a, b = _coprime_pair(rnd, 1, 5)
    k = rnd.randint(2, 9)
    total = (a + b) * k
    return Task("ratio", 3, f"Раздели {total} в отношении {a} : {b}. Чему равна большая часть?", "num", Fraction(max(a, b) * k),
                f"Всего {a + b} частей, на одну часть {k}. Большая часть: {max(a, b)}·{k} = {max(a, b) * k}.")


def r_proportion_hard(rnd) -> Task:
    a, b = _coprime_pair(rnd, 2, 8)
    k = rnd.randint(2, 5)
    return Task("ratio", 3, f"Найди x: {a} : x = {a * k} : {b * k}", "num", Fraction(b),
                f"Отношение {a * k} : {b * k} = {a} : {b}, значит x = {b}.")


# ================================================================ 5. ПРОЦЕНТЫ
def p_basic(rnd) -> Task:
    pc, den = rnd.choice([(50, 2), (10, 10), (25, 4), (20, 5), (1, 100), (75, 4)])
    n = den * rnd.randint(1, 8) * (10 if pc in (1, 10) else 1)
    return Task("percent", 1, f"Найди {pc}% от {n}.", "num", Fraction(n * pc, 100), f"{pc}% = {pc}/100. {n}·{pc}/100 = {fmt(Fraction(n * pc, 100))}.")


def p_of(rnd) -> Task:
    pc = rnd.choice([15, 30, 40, 60, 5, 12, 35])
    n = 100 if pc == 12 else rnd.choice([20, 40, 60, 80, 200])
    return Task("percent", 2, f"Найди {pc}% от {n}.", "num", Fraction(n * pc, 100), f"{n}·{pc}/100 = {fmt(Fraction(n * pc, 100))}.")


def p_what_percent(rnd) -> Task:
    n = rnd.choice([20, 25, 40, 50, 80, 200])
    pc = rnd.choice([5, 10, 20, 25, 50, 75])
    a = n * pc // 100
    if a * 100 != n * pc:
        return p_what_percent(rnd)
    return Task("percent", 2, f"Сколько процентов составляет {a} от {n}?", "num", Fraction(pc), f"{a}/{n}·100 = {pc}%.")


def p_change(rnd) -> Task:
    n = rnd.choice([50, 80, 100, 200, 250, 400])
    pc = rnd.choice([10, 20, 25, 50])
    up = rnd.random() < 0.5
    delta = Fraction(n * pc, 100)
    return Task("percent", 3, f"Цена {n} руб. {'выросла' if up else 'упала'} на {pc}%. Какой стала цена?", "num",
                n + delta if up else n - delta, f"{pc}% от {n} — это {fmt(delta)}. {'Прибавляем' if up else 'Вычитаем'}.")


def p_find_number(rnd) -> Task:
    pc = rnd.choice([10, 20, 25, 50, 30])
    n = rnd.choice([20, 40, 60, 100, 200])
    part = Fraction(n * pc, 100)
    return Task("percent", 3, f"{pc}% числа равны {fmt(part)}. Найди это число.", "num", Fraction(n), f"{fmt(part)} : {pc} · 100 = {n}.")


# ================================================================ 6. ПОЛОЖИТЕЛЬНЫЕ И ОТРИЦАТЕЛЬНЫЕ ЧИСЛА
def n_abs(rnd) -> Task:
    a = rnd.randint(-15, 15)
    return Task("negatives", 1, f"Найди модуль числа: |{fmt(a)}|", "num", Fraction(abs(a)), "Модуль — расстояние до нуля, он не бывает отрицательным.")


def n_opposite(rnd) -> Task:
    a = rnd.choice([i for i in range(-15, 16) if i])
    return Task("negatives", 1, f"Назови число, противоположное числу {fmt(a)}.", "num", Fraction(-a), "Меняем знак на противоположный.")


def n_bigger(rnd) -> Task:
    a, b = rnd.sample(range(-12, 10), 2)
    return Task("negatives", 2, f"Какое число больше: {fmt(a)} или {fmt(b)}?", "num", Fraction(max(a, b)),
                "На числовой прямой правее — больше. Из двух отрицательных больше то, у которого модуль меньше.")


def n_abs_expr(rnd) -> Task:
    a, b = rnd.randint(-12, 12), rnd.randint(-12, 12)
    op = rnd.choice(["+", "−"])
    ans = abs(a) + abs(b) if op == "+" else abs(a) - abs(b)
    return Task("negatives", 2, f"Вычисли: |{fmt(a)}| {op} |{fmt(b)}|", "num", Fraction(ans), "Сначала найди модули, потом считай.")


def n_temp(rnd) -> Task:
    t = rnd.randint(-10, -2)
    d = rnd.randint(3, 12)
    return Task("negatives", 3, f"Утром было {fmt(t)}°, к обеду потеплело на {d}°. Сколько градусов стало?", "num", Fraction(t + d),
                f"{fmt(t)} + {d} = {fmt(t + d)}.")


def n_between(rnd) -> Task:
    a = rnd.randint(-7, -2)
    b = rnd.randint(1, 6)
    return Task("negatives", 3, f"Сколько целых чисел лежит между {fmt(a)} и {b} (сами числа не считаем)?", "num", Fraction(b - a - 1),
                f"Целые числа от {a + 1} до {b - 1}: всего {b - a - 1}.")


# ================================================================ 7. СЛОЖЕНИЕ И ВЫЧИТАНИЕ ЦЕЛЫХ И ДРОБНЫХ РАЦИОНАЛЬНЫХ ЧИСЕЛ
def ra_simple(rnd) -> Task:
    a, b = rnd.randint(-9, 9), rnd.randint(1, 10)
    if rnd.random() < 0.5:
        return Task("rational_add", 1, f"Вычисли: {fmt(a)} + {b}", "num", Fraction(a + b), "Двигаемся по числовой прямой вправо.")
    return Task("rational_add", 1, f"Вычисли: {fmt(a)} − {b}", "num", Fraction(a - b), "Двигаемся по числовой прямой влево.")


def ra_neg(rnd) -> Task:
    a, b = rnd.randint(-15, 15), rnd.randint(-12, -1)
    if rnd.random() < 0.5:
        return Task("rational_add", 2, f"Вычисли: {fmt(a)} + ({fmt(b)})", "num", Fraction(a + b), "Прибавить отрицательное — то же, что вычесть.")
    return Task("rational_add", 2, f"Вычисли: {fmt(a)} − ({fmt(b)})", "num", Fraction(a - b), "Вычесть отрицательное — то же, что прибавить.")


def ra_dec(rnd) -> Task:
    a = Fraction(rnd.randint(-30, 30), 10)
    b = Fraction(rnd.randint(-30, 30), 10)
    if a == 0 or b == 0:
        return ra_dec(rnd)
    return Task("rational_add", 3, f"Вычисли: {_dec(a)} + ({_dec(b)})", "num", a + b, "Считаем как с десятичными дробями, следим за знаком.")


def ra_frac(rnd) -> Task:
    d = rnd.choice([3, 4, 5, 6, 8])
    a, b = rnd.randint(1, d - 1), rnd.randint(1, d - 1)
    return Task("rational_add", 3, f"Вычисли: −{a}/{d} + {b}/{d}", "num", Fraction(b - a, d), f"Знаменатель общий: ({b} − {a})/{d}.")


# ================================================================ 8. УМНОЖЕНИЕ И ДЕЛЕНИЕ РАЦИОНАЛЬНЫХ ЧИСЕЛ
def rm_simple(rnd) -> Task:
    a, b = rnd.randint(2, 9), rnd.randint(2, 9)
    s = rnd.choice([(-1, 1), (1, -1)])
    x, y = a * s[0], b * s[1]
    return Task("rational_muldiv", 1, f"Вычисли: {_signed(x)} · {_signed(y)}", "num", Fraction(x * y), "Разные знаки — минус, одинаковые — плюс.")


def rm_div(rnd) -> Task:
    b = rnd.randint(2, 9)
    q = rnd.randint(2, 9)
    a = b * q
    sa, sb = rnd.choice([1, -1]), rnd.choice([1, -1])
    return Task("rational_muldiv", 2, f"Вычисли: {_signed(a * sa)} : {_signed(b * sb)}", "num", Fraction(q * sa * sb),
                "Знаки работают как при умножении.")


def rm_neg_neg(rnd) -> Task:
    a, b = rnd.randint(2, 9), rnd.randint(2, 9)
    return Task("rational_muldiv", 2, f"Вычисли: (−{a}) · (−{b})", "num", Fraction(a * b), "Минус на минус даёт плюс.")


def rm_three(rnd) -> Task:
    f = [rnd.choice([-1, 1]) * rnd.randint(1, 4) for _ in range(3)]
    ans = f[0] * f[1] * f[2]
    return Task("rational_muldiv", 3, f"Вычисли: {_signed(f[0])} · {_signed(f[1])} · {_signed(f[2])}", "num", Fraction(ans),
                "Сколько минусов? Нечётное число — результат отрицательный.")


def rm_dec(rnd) -> Task:
    a = Fraction(rnd.choice([-1, 1]) * rnd.randint(1, 9), 2)
    b = rnd.choice([-1, 1]) * rnd.choice([2, 4, 6, 8])
    return Task("rational_muldiv", 3, f"Вычисли: {_dec(a)} · ({b})" if b < 0 else f"Вычисли: {_dec(a)} · {b}", "num", a * b,
                "Умножь модули и определи знак.")


# ================================================================ 9. КООРДИНАТНАЯ ПЛОСКОСТЬ
def c_quarter(rnd) -> Task:
    x = rnd.choice([i for i in range(-7, 8) if i])
    y = rnd.choice([i for i in range(-7, 8) if i])
    q = 1 if x > 0 and y > 0 else 2 if x < 0 and y > 0 else 3 if x < 0 else 4
    return Task("coordinates", 1, f"В какой координатной четверти лежит точка A({x}; {y})? (1, 2, 3 или 4)", "num", Fraction(q),
                "I: (+;+), II: (−;+), III: (−;−), IV: (+;−).", ("1", "2", "3", "4"))


def c_abscissa(rnd) -> Task:
    x, y = rnd.randint(-8, 8), rnd.randint(-8, 8)
    which = rnd.choice(["абсцисса (x)", "ордината (y)"])
    return Task("coordinates", 1, f"Точка B({x}; {y}). Чему равна её {which}?", "num", Fraction(x if which.startswith("абс") else y),
                "Первое число — абсцисса (x), второе — ордината (y).")


def c_symmetry(rnd) -> Task:
    x = rnd.choice([i for i in range(-6, 7) if i])
    y = rnd.choice([i for i in range(-6, 7) if i])
    if rnd.random() < 0.5:
        return Task("coordinates", 2, f"Точка A({x}; {y}) симметрична точке B относительно оси x. Запиши координаты B: x; y", "ints", [x, -y],
                    "Симметрия относительно оси x меняет знак у y.")
    return Task("coordinates", 2, f"Точка A({x}; {y}) симметрична точке B относительно оси y. Запиши координаты B: x; y", "ints", [-x, y],
                "Симметрия относительно оси y меняет знак у x.")


def c_dist(rnd) -> Task:
    x = rnd.randint(-5, 5)
    y1, y2 = rnd.sample(range(-6, 7), 2)
    if rnd.random() < 0.5:
        return Task("coordinates", 3, f"Найди длину отрезка между точками ({x}; {y1}) и ({x}; {y2}).", "num", Fraction(abs(y1 - y2)),
                    "Абсциссы равны, значит считаем разницу ординат по модулю.")
    return Task("coordinates", 3, f"Найди длину отрезка между точками ({y1}; {x}) и ({y2}; {x}).", "num", Fraction(abs(y1 - y2)),
                "Ординаты равны, значит считаем разницу абсцисс по модулю.")


def c_mid(rnd) -> Task:
    a = rnd.randint(-6, 6)
    k = rnd.randint(-3, 3)
    b = a + 2 * k
    if k == 0:
        return c_mid(rnd)
    y = rnd.randint(-5, 5)
    return Task("coordinates", 3, f"Найди абсциссу середины отрезка с концами ({a}; {y}) и ({b}; {y}).", "num", Fraction(a + k),
                f"({a} + {fmt(b)}) : 2 = {fmt(a + k)}.")


# ================================================================ 10. ВЫРАЖЕНИЯ И УРАВНЕНИЯ
def e_linear1(rnd) -> Task:
    x = rnd.randint(2, 15)
    c = rnd.randint(2, 15)
    t = rnd.random()
    if t < 0.4:
        return Task("equations", 1, f"Реши уравнение: x + {c} = {x + c}", "num", Fraction(x), f"x = {x + c} − {c} = {x}.")
    if t < 0.7:
        return Task("equations", 1, f"Реши уравнение: x − {c} = {x}", "num", Fraction(x + c), f"x = {x} + {c} = {x + c}.")
    m = rnd.randint(2, 9)
    return Task("equations", 1, f"Реши уравнение: {m}x = {m * x}", "num", Fraction(x), f"x = {m * x} : {m} = {x}.")


def e_like_terms(rnd) -> Task:
    a, b, c = rnd.randint(2, 9), rnd.randint(1, 8), rnd.randint(1, 5)
    return Task("equations", 2, f"Приведи подобные слагаемые: {a + b + c}x − {b}x − {c}x  (ответ вида 3x)", "text", [f"{a}x"],
                f"({a + b + c} − {b} − {c})x = {a}x.")


def e_linear2(rnd) -> Task:
    x = rnd.choice([i for i in range(-6, 10) if i])
    m = rnd.randint(2, 5)
    c = rnd.randint(1, 9)
    return Task("equations", 2, f"Реши уравнение: {m}x + {c} = {fmt(m * x + c)}", "num", Fraction(x), f"{m}x = {fmt(m * x)}, x = {fmt(x)}.")


def e_brackets(rnd) -> Task:
    a = rnd.randint(2, 5)
    x = rnd.randint(2, 9)
    b = rnd.randint(1, 5)
    return Task("equations", 3, f"Реши уравнение: {a}(x − {b}) = {a * (x - b)}", "num", Fraction(x),
                f"x − {b} = {x - b}, значит x = {x}.")


def e_both_sides(rnd) -> Task:
    x = rnd.randint(2, 9)
    a, b = rnd.randint(2, 4), rnd.randint(5, 8)
    c = rnd.randint(1, 9)
    return Task("equations", 3, f"Реши уравнение: {b}x − {c} = {a}x + {(b - a) * x - c}", "num", Fraction(x),
                f"Переносим: {b - a}x = {(b - a) * x}, x = {x}.")


def e_open_brackets(rnd) -> Task:
    a, b = rnd.randint(3, 9), rnd.randint(1, 6)
    return Task("equations", 3, f"Упрости, раскрыв скобки: {a} − ({a} − {b}).  Чему равно значение?", "num", Fraction(b), f"{a} − {a} + {b} = {b}.")


# ================================================================ 5 КЛАСС (для «спуска вниз»)
TOPICS5: dict[str, str] = {
    "mult_table": "5 класс: таблица умножения и деление",
    "decimals_addsub": "5 класс: десятичные дроби, сложение и вычитание",
    "decimals_muldiv": "5 класс: десятичные дроби, умножение и деление",
    "order_ops": "5 класс: порядок действий",
    "mixed_numbers": "5 класс: смешанные числа",
}
ALL_TOPICS: dict[str, str] = {**TOPICS, **TOPICS5}

# какие темы 5 класса нужны для темы 6 класса
PREREQ: dict[str, list[str]] = {
    "divisibility": ["mult_table"],
    "fractions_add": ["mixed_numbers", "mult_table"],
    "fractions_muldiv": ["mixed_numbers", "mult_table"],
    "ratio": ["mult_table"],
    "percent": ["decimals_muldiv", "mult_table"],
    "negatives": ["order_ops"],
    "rational_add": ["decimals_addsub"],
    "rational_muldiv": ["mult_table", "decimals_muldiv"],
    "coordinates": [],
    "equations": ["order_ops", "mult_table"],
    "circle": ["decimals_muldiv"],
    "word_problems": ["mult_table", "decimals_muldiv"],
    "mental": ["mult_table"],
}


def _mk(topic, level, text, kind, value, expl, traps=()):
    t = Task(topic, level, text, kind, value, expl)
    for v, tag in traps:
        add_trap(t, v, tag)
    return t


def add_trap(t: Task, value, tag: str) -> None:
    """Запомнить типичный неверный ответ (если он отличается от верного)."""
    if t.kind in ("num", "reduced"):
        value = Fraction(value)
        if value == t.value:
            return
        key = f"{value.numerator}/{value.denominator}" if value.denominator != 1 else str(value.numerator)
    elif t.kind == "ints":
        if list(value) == list(t.value):
            return
        key = list(value)
    else:
        key = str(value)
    if [key, tag] not in t.traps:
        t.traps.append([key, tag])


def m1(rnd) -> Task:
    a, b = rnd.randint(2, 9), rnd.randint(2, 9)
    return Task("mult_table", 1, f"{a} · {b} = ?", "num", Fraction(a * b), f"{a}·{b} = {a * b}.")


def m2(rnd) -> Task:
    if rnd.random() < 0.5:
        a, b = rnd.randint(6, 9), rnd.randint(6, 9)
        return Task("mult_table", 2, f"{a} · {b} = ?", "num", Fraction(a * b), f"{a}·{b} = {a * b}.")
    a, b = rnd.randint(3, 9), rnd.randint(3, 9)
    return Task("mult_table", 2, f"{a * b} : {b} = ?", "num", Fraction(a), f"{a}·{b} = {a * b}, поэтому {a * b} : {b} = {a}.")


def m3(rnd) -> Task:
    b = rnd.randint(3, 9)
    a = rnd.randint(12, 25)
    if rnd.random() < 0.5:
        return Task("mult_table", 3, f"{a} · {b} = ?", "num", Fraction(a * b),
                    f"{a // 10 * 10}·{b} + {a % 10}·{b} = {a // 10 * 10 * b} + {a % 10 * b} = {a * b}.")
    return Task("mult_table", 3, f"{a * b} : {b} = ?", "num", Fraction(a), f"Проверка: {a}·{b} = {a * b}.")


def da1(rnd) -> Task:
    a, b = Fraction(rnd.randint(1, 9), 10), Fraction(rnd.randint(1, 9), 10)
    if rnd.random() < 0.5:
        return Task("decimals_addsub", 1, f"{_dec(a)} + {_dec(b)} = ?", "num", a + b, "Складываем десятые, запятая под запятой.")
    a, b = max(a, b) + Fraction(rnd.randint(0, 2)), min(a, b)
    return Task("decimals_addsub", 1, f"{_dec(a)} − {_dec(b)} = ?", "num", a - b, "Вычитаем десятые, запятая под запятой.")


def da2(rnd) -> Task:
    a, b = Fraction(rnd.randint(11, 59), 10), Fraction(rnd.randint(11, 99), 100)
    if rnd.random() < 0.5:
        return Task("decimals_addsub", 2, f"{_dec(a)} + {_dec(b)} = ?", "num", a + b, "Запятая под запятой, дописываем нули: 2,5 = 2,50.")
    a = a + 2
    return Task("decimals_addsub", 2, f"{_dec(a)} − {_dec(b)} = ?", "num", a - b, "Запятая под запятой, дописываем нули.")


def da3(rnd) -> Task:
    n = rnd.choice([5, 10])
    b = Fraction(rnd.randint(11, n * 10 - 11), 10)
    t = Task("decimals_addsub", 3, f"{n} − {_dec(b)} = ?", "num", n - b, f"{n} = {n},0; вычитаем с запятой под запятой.")
    add_trap(t, (n - int(b)) + (b - int(b)), "decimal_align")
    return t


def dm1(rnd) -> Task:
    a, n = Fraction(rnd.randint(1, 9), 10), rnd.randint(2, 9)
    return Task("decimals_muldiv", 1, f"{_dec(a)} · {n} = ?", "num", a * n, "Умножь как целые числа, потом поставь запятую.")


def dm2(rnd) -> Task:
    if rnd.random() < 0.6:
        i, j = rnd.randint(2, 9), rnd.randint(2, 9)
        a, b = Fraction(i, 10), Fraction(j, 10)
        t = Task("decimals_muldiv", 2, f"{_dec(a)} · {_dec(b)} = ?", "num", a * b,
                 f"{i}·{j} = {i * j}, знаков после запятой 2, значит {_dec(a * b)}.")
        add_trap(t, a * b * 10, "decimal_point")
        return t
    k, n = rnd.randint(2, 9), rnd.randint(2, 9)
    return Task("decimals_muldiv", 2, f"{_dec(Fraction(k * n, 10))} : {n} = ?", "num", Fraction(k, 10), f"{k * n} : {n} = {k}, одна цифра после запятой.")


def dm3(rnd) -> Task:
    if rnd.random() < 0.5:
        d = Fraction(rnd.choice([2, 3, 4, 5, 6]), 10)
        q = rnd.randint(2, 12)
        t = Task("decimals_muldiv", 3, f"{_dec(q * d)} : {_dec(d)} = ?", "num", Fraction(q),
                 "Умножь делимое и делитель на 10, чтобы делитель стал целым.")
        add_trap(t, Fraction(q, 10), "decimal_point")
        return t
    a, b = Fraction(rnd.randint(11, 29), 10), Fraction(rnd.choice([2, 4, 5, 6]), 10)
    t = Task("decimals_muldiv", 3, f"{_dec(a)} · {_dec(b)} = ?", "num", a * b, "Считай как целые, потом отсчитай два знака после запятой.")
    add_trap(t, a * b * 10, "decimal_point")
    return t


def o1(rnd) -> Task:
    a, b, c = rnd.randint(2, 9), rnd.randint(2, 6), rnd.randint(2, 6)
    if rnd.random() < 0.5:
        t = Task("order_ops", 1, f"{a} + {b} · {c} = ?", "num", Fraction(a + b * c), "Сначала умножение: потом сложение.")
        add_trap(t, (a + b) * c, "op_order")
        return t
    a += b * c
    t = Task("order_ops", 1, f"{a} − {b} · {c} = ?", "num", Fraction(a - b * c), "Сначала умножение: потом вычитание.")
    add_trap(t, (a - b) * c, "op_order")
    return t


def o2(rnd) -> Task:
    if rnd.random() < 0.5:
        a, b, c = rnd.randint(2, 6), rnd.randint(2, 6), rnd.randint(2, 6)
        d = rnd.randint(1, a + b)
        t = Task("order_ops", 2, f"({a} + {b}) · {c} − {d} = ?", "num", Fraction((a + b) * c - d), "Сначала скобки, потом умножение, потом вычитание.")
        add_trap(t, a + b * c - d, "op_order")
        return t
    a, b, c, d = rnd.randint(2, 9), rnd.randint(2, 6), rnd.randint(2, 6), rnd.randint(2, 6)
    t = Task("order_ops", 2, f"{a * b} : {b} + {c} · {d} = ?", "num", Fraction(a + c * d), "Сначала деление и умножение, потом сложение.")
    add_trap(t, (a + c) * d, "op_order")
    return t


def o3(rnd) -> Task:
    a, b, c, e = rnd.randint(3, 9), rnd.randint(2, 5), rnd.randint(2, 5), rnd.randint(2, 5)
    d = rnd.randint(1, a)
    t = Task("order_ops", 3, f"{a} + {b} · {c} − {d * e} : {e} = ?", "num", Fraction(a + b * c - d),
             f"Сначала умножение и деление: {b * c} и {d}; потом слева направо.")
    add_trap(t, Fraction(((a + b) * c - d * e), e), "op_order")
    return t


def mx1(rnd) -> Task:
    n = rnd.randint(3, 8)
    q, r = rnd.randint(1, 5), rnd.randint(1, n - 1)
    return Task("mixed_numbers", 1, f"Сколько целых в числе {q * n + r}/{n}?", "num", Fraction(q), f"{q * n + r} : {n} = {q} (остаток {r}).")


def mx2(rnd) -> Task:
    n = rnd.randint(3, 8)
    w, r = rnd.randint(1, 5), rnd.randint(1, n - 1)
    return Task("mixed_numbers", 2, f"Запиши {w} {r}/{n} неправильной дробью. Ответ: числитель; знаменатель", "ints", [w * n + r, n],
                f"{w}·{n} + {r} = {w * n + r}, знаменатель тот же: {w * n + r}/{n}.")


def mx3(rnd) -> Task:
    n = rnd.randint(4, 9)
    if rnd.random() < 0.5:
        w1, w2 = rnd.randint(1, 4), rnd.randint(1, 4)
        r1, r2 = rnd.randint(1, n - 1), rnd.randint(1, n - 1)
        return Task("mixed_numbers", 3, f"Вычисли: {w1} {r1}/{n} + {w2} {r2}/{n}", "num", Fraction(w1 * n + r1 + w2 * n + r2, n),
                    "Отдельно целые, отдельно дробные части (можно ответить и смешанным числом).")
    w1, w2 = rnd.randint(3, 6), rnd.randint(1, 2)
    r1 = rnd.randint(1, n - 2)
    r2 = rnd.randint(r1 + 1, n - 1)
    t = Task("mixed_numbers", 3, f"Вычисли: {w1} {r1}/{n} − {w2} {r2}/{n}", "num", Fraction(w1 * n + r1 - w2 * n - r2, n),
             f"Занимаем единицу: {w1 - 1} {r1 + n}/{n} − {w2} {r2}/{n}.")
    add_trap(t, Fraction((w1 - w2) * n + (r2 - r1), n), "mixed_borrow")
    return t


# ---------------------------------------------------------------- ловушки для задач 6 класса (разбор по тексту условия)
def _ints(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"-?\d+", text.replace("−", "-"))]


def _tr_frac_sum(t: Task):
    m = re.search(r"(\d+)/(\d+) ([+−]) (\d+)/(\d+)", t.text)
    if not m:
        return []
    a, b, op, c, d = int(m[1]), int(m[2]), m[3], int(m[4]), int(m[5])
    if op == "+":
        return [(Fraction(a + c, b + d), "add_num_den")]
    if b != d and b - d > 0 and a - c != 0:
        return [(Fraction(a - c, b - d), "add_num_den")]
    return []


def _tr_div(t):
    m = re.search(r"(\d+)/(\d+) : (\d+)/(\d+)", t.text)
    return [(Fraction(int(m[1]), int(m[2])) * Fraction(int(m[3]), int(m[4])), "no_flip")] if m else []


def _tr_gcd_lcm(t):
    m = re.search(r"\((\d+), (\d+)\)", t.text)
    if not m:
        return []
    a, b = int(m[1]), int(m[2])
    g = math.gcd(a, b)
    return [(Fraction(a * b // g if "НОД" in t.text else g), "gcd_lcm")]


def _tr_pct(t):
    m = re.search(r"Найди (\d+)% от (\d+)", t.text)
    return [(Fraction(int(m[2]), int(m[1])), "pct_divide")] if m else []


def _tr_pct_change(t):
    m = re.search(r"Цена (\d+) руб\. (выросла|упала) на (\d+)%", t.text)
    if not m:
        return []
    n, up, pc = int(m[1]), m[2] == "выросла", int(m[3])
    delta = Fraction(n * pc, 100)
    return [(delta, "pct_forgot_base"), (n - delta if up else n + delta, "pct_direction")]


def _tr_abs(t):
    m = re.search(r"\|(−?\d+)\|", t.text)
    return [(Fraction(int(m[1].replace("−", "-"))), "abs_sign")] if m and m[1].startswith("−") else []


def _tr_bigger(t):
    a, b = _ints(t.text.split(":", 1)[1])[:2]
    return [(Fraction(min(a, b)), "neg_compare")] if a < 0 and b < 0 else []


def _tr_ra_neg(t):
    m = re.search(r"(−?\d+) ([+−]) \((−?\d+)\)", t.text)
    if not m:
        return []
    a, b = int(m[1].replace("−", "-")), int(m[3].replace("−", "-"))
    return [(Fraction(a - b), "sign_rule")] if m[2] == "+" else [(Fraction(a + b), "minus_minus")]


def _tr_mul(t):
    nums = _ints(t.text.split(":", 1)[1])
    p = 1
    for n in nums:
        p *= n
    return [(Fraction(-p), "sign_rule")]


def _tr_negneg(t):
    a, b = _ints(t.text.split(":", 1)[1])
    return [(Fraction(a * b * -1), "minus_minus")]


def _tr_rdiv(t):
    a, b = _ints(t.text.split(":", 1)[1].replace(" : ", " ; "))
    q = Fraction(a, b)
    return [(-q, "sign_rule")]


def _tr_abscissa(t):
    x, y = _ints(t.text.split("(", 1)[1].split(")", 1)[0])
    return [(Fraction(y if "абсцисса" in t.text else x), "coord_swap")]


def _tr_symmetry(t):
    x, y = _ints(t.text.split("(", 1)[1].split(")", 1)[0])
    return [([x, -y] if "оси y" in t.text else [-x, y], "symmetry_axis")]


def _tr_eq(t):
    body = t.text.split(":", 1)[1].replace("−", "-").strip()
    if m := re.fullmatch(r"x \+ (\d+) = (-?\d+)", body):
        return [(Fraction(int(m[2]) + int(m[1])), "eq_transfer")]
    if m := re.fullmatch(r"x - (\d+) = (-?\d+)", body):
        return [(Fraction(int(m[2]) - int(m[1])), "eq_transfer")]
    if m := re.fullmatch(r"(\d+)x = (-?\d+)", body):
        return [(Fraction(int(m[2]) * int(m[1])), "eq_divide")]
    if m := re.fullmatch(r"(\d+)x \+ (\d+) = (-?\d+)", body):
        return [(Fraction(int(m[3]) + int(m[2]), int(m[1])), "eq_transfer")]
    if m := re.fullmatch(r"(\d+)\(x - (\d+)\) = (-?\d+)", body):
        return [(Fraction(int(m[3]), int(m[1])) - int(m[2]), "eq_bracket")]
    return []


# ================================================================ НОВОЕ: окружность, текстовые задачи, устный счёт (в духе классических учебников)
PI = Fraction(314, 100)


def ci1(rnd) -> Task:
    r = rnd.randint(1, 12)
    return Task("circle", 1, f"Длина окружности C = 2πr. Найди длину окружности радиуса {r} см (π ≈ 3,14).", "num", 2 * PI * r,
                f"C = 2 · 3,14 · {r} = {_dec(2 * PI * r)} см.")


def ci2(rnd) -> Task:
    r = rnd.choice([1, 2, 3, 4, 5, 10])
    return Task("circle", 2, f"Площадь круга S = πr². Найди площадь круга радиуса {r} см (π ≈ 3,14).", "num", PI * r * r,
                f"S = 3,14 · {r}² = 3,14 · {r * r} = {_dec(PI * r * r)} кв. см.")


def ci3(rnd) -> Task:
    r = rnd.randint(1, 10)
    if rnd.random() < 0.5:
        return Task("circle", 3, f"Длина окружности равна {_dec(2 * PI * r)} см. Найди радиус (π ≈ 3,14).", "num", Fraction(r),
                    f"r = C : (2π) = {_dec(2 * PI * r)} : 6,28 = {r} см.")
    return Task("circle", 3, f"Длина окружности равна {_dec(PI * 2 * r)} см. Найди диаметр (π ≈ 3,14).", "num", Fraction(2 * r),
                f"C = πd, значит d = C : π = {_dec(2 * PI * r)} : 3,14 = {2 * r} см.")


def w1(rnd) -> Task:
    k = rnd.randint(0, 3)
    if k == 0:
        v, t = rnd.choice([40, 50, 60, 70, 80, 90]), rnd.randint(2, 5)
        return Task("word_problems", 1, f"Поезд идёт со скоростью {v} км/ч. Сколько километров он пройдёт за {t} ч?", "num", Fraction(v * t),
                    f"Путь = скорость · время = {v}·{t} = {v * t} км.")
    if k == 1:
        v, t = rnd.randint(3, 6), rnd.randint(2, 5)
        return Task("word_problems", 1, f"За {t} ч пешеход прошёл {v * t} км. С какой скоростью он шёл?", "num", Fraction(v),
                    f"Скорость = путь : время = {v * t} : {t} = {v} км/ч.")
    if k == 2:
        s_, v = rnd.choice([(120, 40), (180, 60), (150, 50), (240, 80), (210, 70)])
        return Task("word_problems", 1, f"Автомобиль проехал {s_} км со скоростью {v} км/ч. Сколько часов он был в пути?", "num", Fraction(s_, v),
                    f"Время = путь : скорость = {s_} : {v} = {s_ // v} ч.")
    u, c = rnd.randint(12, 25), rnd.randint(2, 5)
    return Task("word_problems", 1, f"Скорость катера в стоячей воде {u} км/ч, скорость течения {c} км/ч. Какова скорость катера против течения?",
                "num", Fraction(u - c), f"Против течения скорости вычитаются: {u} − {c} = {u - c} км/ч.")


def w2(rnd) -> Task:
    if rnd.random() < 0.5:
        a, b, k = rnd.choice([40, 50, 60]), rnd.choice([30, 40, 70, 80]), rnd.randint(2, 5)
        return Task("word_problems", 2,
                    f"Из двух городов, расстояние между которыми {(a + b) * k} км, одновременно навстречу друг другу выехали два автомобиля "
                    f"со скоростями {a} и {b} км/ч. Через сколько часов они встретятся?", "num", Fraction(k),
                    f"Скорость сближения {a} + {b} = {a + b} км/ч. Время = {(a + b) * k} : {a + b} = {k} ч.")
    x, y = rnd.choice([(6, 3), (12, 4), (10, 15), (20, 5), (6, 12), (30, 20), (15, 10), (24, 8)])
    return Task("word_problems", 2, f"Один рабочий выполнит задание за {x} ч, другой — за {y} ч. За сколько часов они выполнят его, работая вместе?",
                "num", Fraction(x * y, x + y),
                f"За 1 час они делают 1/{x} + 1/{y} = {x + y}/{x * y} задания. Всё задание: {x * y}/{x + y} ч.")


def w3(rnd) -> Task:
    k = rnd.randint(0, 1)
    if k == 0:
        dv, m, t = rnd.choice([6, 10, 12, 15]), rnd.randint(1, 2), rnd.randint(2, 4)
        v1, v2 = dv * m, dv * m + dv
        return Task("word_problems", 3,
                    f"Из пункта A выехал велосипедист со скоростью {v1} км/ч. Через {t} ч вслед за ним выехал мотоциклист со скоростью {v2} км/ч. "
                    f"Через сколько часов после своего выезда мотоциклист догонит велосипедиста?", "num", Fraction(m * t),
                    f"Когда выехал мотоциклист, между ними было {v1}·{t} = {v1 * t} км. Скорость сближения {v2} − {v1} = {dv} км/ч. "
                    f"Время = {v1 * t} : {dv} = {m * t} ч.")
    u, c, t = rnd.randint(12, 24), rnd.randint(2, 4), rnd.randint(2, 5)
    return Task("word_problems", 3, f"Катер прошёл по течению реки {(u + c) * t} км за {t} ч. Скорость течения {c} км/ч. "
                                    "Какова скорость катера в стоячей воде?", "num", Fraction(u),
                f"Скорость по течению {(u + c) * t} : {t} = {u + c} км/ч. Своя скорость: {u + c} − {c} = {u} км/ч.")


def mt1(rnd) -> Task:
    k = rnd.randint(0, 2)
    if k == 0:
        a = rnd.randrange(12, 99, 2)
        return Task("mental", 1, f"Вычисли устно: {a} · 5", "num", Fraction(a * 5), f"Умножить на 5 — значит умножить на 10 и разделить на 2: {a * 10} : 2 = {a * 5}.")
    if k == 1:
        a = rnd.randrange(12, 97, 4)
        return Task("mental", 1, f"Вычисли устно: {a} · 25", "num", Fraction(a * 25), f"25 = 100 : 4. Значит {a} : 4 = {a // 4}, и умножаем на 100: {a * 25}.")
    a = rnd.randint(12, 49)
    return Task("mental", 1, f"Вычисли устно: {a} · 11", "num", Fraction(a * 11), f"{a}·10 + {a} = {a * 10} + {a} = {a * 11}.")


def mt2(rnd) -> Task:
    k = rnd.randint(0, 3)
    if k == 0:
        a = rnd.choice([15, 25, 35, 45, 55, 65, 75, 85, 95])
        n = a // 10
        return Task("mental", 2, f"Вычисли устно: {a}²", "num", Fraction(a * a),
                    f"Число, оканчивающееся на 5: {n} · {n + 1} = {n * (n + 1)}, и приписываем 25: {a * a}.")
    if k == 1:
        a = rnd.randint(11, 20)
        return Task("mental", 2, f"Вычисли устно: {a}²", "num", Fraction(a * a), f"{a}² = {a}·{a} = {a * 10} + {a * (a - 10)} = {a * a}.")
    if k == 2:
        n = rnd.randint(3, 9)
        return Task("mental", 2, f"Вычисли устно: 99 · {n}", "num", Fraction(99 * n), f"99 = 100 − 1: {n * 100} − {n} = {99 * n}.")
    a = rnd.randrange(75, 400, 25)
    return Task("mental", 2, f"Вычисли устно: {a} : 25", "num", Fraction(a, 25), f"{a}·4 = {a * 4}, делим на 100: {a // 25}.")


RACHINSKY = [
    ("(10² + 11² + 12² + 13² + 14²) : 365 = ?", 2, "10² + 11² + 12² = 13² + 14² = 365, всего 730 = 2 · 365."),
    ("(10² + 11² + 12²) : 365 = ?", 1, "100 + 121 + 144 = 365."),
    ("(13² + 14²) : 365 = ?", 1, "169 + 196 = 365."),
    ("(21² + 22² + 23² + 24²) : 2030 = ?", 1, "441 + 484 + 529 + 576 = 2030."),
    ("(25² + 26² + 27²) : 2030 = ?", 1, "625 + 676 + 729 = 2030."),
]


def mt3(rnd) -> Task:
    k = rnd.randint(0, 3)
    if k == 0:
        text, ans, expl = rnd.choice(RACHINSKY)
        return Task("mental", 3, "Вычисли устно, как предлагал С. А. Рачинский: " + text, "num", Fraction(ans), expl)
    if k == 1:
        n, d = rnd.choice([(1, 8), (3, 8), (5, 8), (7, 8), (1, 25), (3, 25), (1, 20), (7, 20), (3, 40)])
        return Task("mental", 3, f"Запиши дробь {n}/{d} десятичной дробью.", "num", Fraction(n, d), f"{n} : {d} = {_dec(Fraction(n, d))}.")
    if k == 2:
        x = rnd.choice([8, 16, 24, 32, 40, 48, 64, 72])
        return Task("mental", 3, f"Вычисли устно: 0,25 · {x}", "num", Fraction(x, 4), f"0,25 = 1/4, четверть от {x} равна {x // 4}.")
    x = rnd.choice([8, 16, 40, 80, 24])
    return Task("mental", 3, f"Вычисли устно: 1000 : {x}", "num", Fraction(1000, x), f"1000 : {x} = {_dec(Fraction(1000, x))}.")


GENERATORS: dict[str, dict[int, list]] = {
    "divisibility": {1: [d_sign], 2: [d_gcd, d_lcm], 3: [d_prime, d_gcd_hard, d_lcm]},
    "fractions_add": {1: [fa_reduce, fa_cmp_same, fa_add_same], 2: [fa_add_mult, fa_cmp_diff, fa_reduce], 3: [fa_add_diff, fa_add_mult]},
    "fractions_muldiv": {1: [fm_of_number], 2: [fm_mul, fm_number_by_part, fm_of_number], 3: [fm_div, fm_mixed_word, fm_mul]},
    "ratio": {1: [r_proportion, r_price], 2: [r_reduce_ratio, r_part, r_price], 3: [r_split, r_proportion_hard, r_part]},
    "percent": {1: [p_basic], 2: [p_of, p_what_percent], 3: [p_change, p_find_number, p_what_percent]},
    "negatives": {1: [n_abs, n_opposite], 2: [n_bigger, n_abs_expr], 3: [n_temp, n_between, n_abs_expr]},
    "rational_add": {1: [ra_simple], 2: [ra_neg, ra_simple], 3: [ra_dec, ra_frac, ra_neg]},
    "rational_muldiv": {1: [rm_simple], 2: [rm_div, rm_neg_neg], 3: [rm_three, rm_dec, rm_div]},
    "coordinates": {1: [c_quarter, c_abscissa], 2: [c_symmetry, c_quarter], 3: [c_dist, c_mid, c_symmetry]},
    "equations": {1: [e_linear1], 2: [e_like_terms, e_linear2], 3: [e_brackets, e_both_sides, e_open_brackets]},
    "circle": {1: [ci1], 2: [ci2], 3: [ci3]},
    "word_problems": {1: [w1], 2: [w2, w1], 3: [w3, w2]},
    "mental": {1: [mt1], 2: [mt2], 3: [mt3]},
    "mult_table": {1: [m1], 2: [m2], 3: [m3]},
    "decimals_addsub": {1: [da1], 2: [da2], 3: [da3, da2]},
    "decimals_muldiv": {1: [dm1], 2: [dm2], 3: [dm3]},
    "order_ops": {1: [o1], 2: [o2], 3: [o3]},
    "mixed_numbers": {1: [mx1], 2: [mx2], 3: [mx3]},
}

TRAPS = {
    fa_add_same: _tr_frac_sum, fa_add_mult: _tr_frac_sum, fa_add_diff: _tr_frac_sum,
    fm_div: _tr_div, d_gcd: _tr_gcd_lcm, d_lcm: _tr_gcd_lcm, d_gcd_hard: _tr_gcd_lcm,
    p_basic: _tr_pct, p_of: _tr_pct, p_change: _tr_pct_change,
    n_abs: _tr_abs, n_bigger: _tr_bigger, ra_neg: _tr_ra_neg,
    rm_simple: _tr_mul, rm_neg_neg: _tr_negneg, rm_div: _tr_rdiv,
    c_abscissa: _tr_abscissa, c_symmetry: _tr_symmetry,
    e_linear1: _tr_eq, e_linear2: _tr_eq, e_brackets: _tr_eq,
}


def make_task(topic: str, level: int, rnd: random.Random | None = None, avoid: set[str] | None = None) -> Task:
    """Сгенерировать задачу; `avoid` — тексты, которые уже были в этой сессии."""
    rnd = rnd or random
    level = max(1, min(3, level))
    for _ in range(30):
        gen = rnd.choice(GENERATORS[topic][level])
        t = gen(rnd)
        t.level = level
        if gen in TRAPS:
            for v, tag in TRAPS[gen](t):
                add_trap(t, v, tag)
        if not avoid or t.text not in avoid:
            return t
    return t
