"""
Классификатор «лид / спам» для veip.py — наивный Байес на бинарных признаках.

Обучается на двух источниках:
  * базовый датасет ml_train.json (в DATA_DIR) — тысячи размеченных сообщений
    из истории бота (переписка бота с владельцем). Лежит только на volume,
    в git не коммитится (там тексты и юзернеймы реальных людей);
  * явные пометки владельца /spam и /notspam (feedback.json) — с повышенным
    весом, чтобы свежие исправления перевешивали старые данные.

Модель не дообучается сама на своих же решениях — только на пометках
владельца. (Раньше каждое пересланное сообщение автоматически шло в «лиды»,
а каждое заблокированное — в «спам», и классификатор учился на собственных
ошибках.)
"""
import json
import math
import os
import re

# невидимые символы, которыми спамеры разбивают слова
_ZW = re.compile(r"[­​-‏⁠-⁤﻿]")
# латиница/греческий/«капители», похожие на кириллицу: «κуpьepa», «ᴧю∂ᴇй»
_HOMO = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м",
    "t": "т", "h": "н", "b": "в", "α": "а", "ο": "о", "κ": "к", "ρ": "р", "τ": "т", "υ": "и",
    "ϲ": "с", "ʙ": "в", "ʜ": "н", "ʍ": "м", "ᴇ": "е", "ᴧ": "л", "σ": "о", "δ": "б", "ʒ": "з",
    "π": "п", "η": "п", "ε": "е", "β": "в", "ẍ": "х",
})
_WORD = re.compile(r"\w+")
_TOKEN = re.compile(r"[а-яa-z]+|\d+")
_NUM_SPLIT = re.compile(r"(?<=\d)[ .,](?=\d{3}\b)")   # 4.000 / 12 000 -> 4000 / 12000
_K_NUM = re.compile(r"\b(\d+) ?к\b")                  # 12к -> 12000
_OBF = "obfmixed"


def _fix_word(m):
    w = m.group(0)
    if re.search(r"[а-я]", w) and re.search(r"[^\Wа-я\d_]", w):
        return w.translate(_HOMO) + " " + _OBF
    return w


def normalize(text):
    s = _ZW.sub("", text or "").lower().replace("ё", "е")
    s = re.sub(r"\b(\w)\.(?=\w\b)", r"\1", s)   # u.s.d.t -> usdt
    s = _WORD.sub(_fix_word, s)
    return re.sub(r"\s+", " ", s).strip()


def features(text):
    raw = text or ""
    s = normalize(raw)
    s = _NUM_SPLIT.sub("", s)
    s = _K_NUM.sub(lambda m: m.group(1) + "000", s)
    toks = []
    for t in _TOKEN.findall(s):
        if t.isdigit():
            n = int(t[:9])
            toks.append("#n" if n < 100 else "#nn" if n < 1000 else "#nnn")
        elif len(t) >= 2:
            toks.append(t[:12])   # грубый стемминг: обрезаем длинные окончания
    feats = set(toks)
    feats.update(a + "_" + b for a, b in zip(toks, toks[1:]))
    if "@" in raw:
        feats.add("#mention")
    if "http" in raw or "t.me/" in raw:
        feats.add("#link")
    if re.search(r"\+?\d[\d\s()-]{9,}\d", raw):
        feats.add("#phone")
    if re.search(r"[₽$💲💵💰€]", raw):
        feats.add("#money")
    if len(s) > 120:
        feats.add("#long")
    if len(raw.splitlines()) > 3:
        feats.add("#multiline")
    if not feats:
        feats.add("#empty")
    return feats


class SpamModel:
    """Наивный Байес (мультиномиальный на бинарных признаках) с равными
    априорными вероятностями классов.

    score(text) — лог-отношение правдоподобий «спам» / «лид» по признакам
    сообщения: > 0 — больше похоже на спам. Признаки, которых модель не
    видела, не влияют. Порог выбирается в veip.py (CLASSIFIER_MARGIN).
    """

    def __init__(self, alpha=0.5):
        self.alpha = alpha
        self.df = {"spam": {}, "lead": {}}
        self.n = {"spam": 0.0, "lead": 0.0}      # сколько сообщений (с весами)
        self.tot = {"spam": 0.0, "lead": 0.0}    # сколько признаков (с весами)
        self._cache = None

    def add(self, text, label, weight=1.0):
        d = self.df[label]
        fs = features(text)
        for f in fs:
            d[f] = d.get(f, 0.0) + weight
        self.n[label] += weight
        self.tot[label] += weight * len(fs)
        self._cache = None

    def remove(self, text, label, weight=1.0):
        d = self.df[label]
        fs = features(text)
        for f in fs:
            left = d.get(f, 0.0) - weight
            if left > 1e-9:
                d[f] = left
            else:
                d.pop(f, None)
        self.n[label] = max(0.0, self.n[label] - weight)
        self.tot[label] = max(0.0, self.tot[label] - weight * len(fs))
        self._cache = None

    def ready(self, min_examples=15):
        return self.n["spam"] >= min_examples and self.n["lead"] >= min_examples

    def _prepare(self):
        a = self.alpha
        vocab = set(self.df["spam"]) | set(self.df["lead"])
        v = len(vocab) or 1
        ds = self.tot["spam"] + a * v
        dl = self.tot["lead"] + a * v
        self._cache = {
            f: math.log((self.df["spam"].get(f, 0.0) + a) / ds)
            - math.log((self.df["lead"].get(f, 0.0) + a) / dl)
            for f in vocab
        }

    def score(self, text):
        if self._cache is None:
            self._prepare()
        w = self._cache
        return sum(w.get(f, 0.0) for f in features(text))

    def top_features(self, text, k=5):
        if self._cache is None:
            self._prepare()
        fs = [(f, self._cache[f]) for f in features(text) if f in self._cache]
        return sorted(fs, key=lambda x: -abs(x[1]))[:k]


def load_base_dataset(path):
    if not os.path.exists(path):
        return {"spam": [], "lead": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"spam": data.get("spam", []), "lead": data.get("lead", [])}


def build_model(base, feedback, feedback_weight=5.0):
    m = SpamModel()
    for t in base["spam"]:
        m.add(t, "spam")
    for t in base["lead"]:
        m.add(t, "lead")
    for t in feedback.get("spam_texts", []):
        m.add(t, "spam", feedback_weight)
    for t in feedback.get("lead_texts", []):
        m.add(t, "lead", feedback_weight)
    return m
