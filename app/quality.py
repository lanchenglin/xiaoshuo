"""Deterministic diagnostics. These are NOT AI-authorship or legal-originality detectors."""
from __future__ import annotations
import re
from collections import Counter


def char_count(text: str) -> int:
    """Non-whitespace Unicode character count, including punctuation; used everywhere."""
    return len(re.sub(r"\s", "", text))


def clean(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


def ngrams(text: str, n: int = 32) -> set[str]:
    return {text[i:i+n] for i in range(0, max(0, len(text)-n+1), 4)}


def diagnose(text: str, target: int, sources: list[str], previous: str = "") -> dict:
    count = char_count(text)
    hard: list[str] = []
    warnings: list[str] = []
    if not target * 0.65 <= count <= target * 1.35:
        hard.append(f"字数 {count} 不在目标 {target} 的 65%–135% 范围内（含标点、不含空白）")
    sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if len(s.strip()) >= 24]
    repeats = [s for s, n in Counter(sentences).items() if n > 1]
    if repeats:
        hard.append(f"检测到 {len(repeats)} 处长句原样重复")
    normalized = clean(text)
    # Long exact matches are an actionable alert, not a plagiarism verdict.
    long_matches = []
    for source in sources:
        reference = clean(source)
        for i in range(0, max(0, len(normalized)-80+1), 8):
            fragment = normalized[i:i+80]
            if fragment in reference:
                long_matches.append(fragment)
                break
    if long_matches:
        hard.append("与参考材料存在至少 80 字的连续相同表达，需要重写并复核")
    if previous:
        previous = clean(previous)
        if any(normalized[i:i+100] in previous for i in range(0, max(0,len(normalized)-100+1),10)):
            hard.append("与前章存在至少 100 字的连续相同表达")
    for phrase in ["命运的齿轮", "嘴角勾起一抹", "不禁倒吸一口凉气", "一场更大的风暴", "殊不知"]:
        occurrences = text.count(phrase)
        if occurrences:
            warnings.append(f"表达线索「{phrase}」出现 {occurrences} 次，按上下文审阅，不自动删除")
    return {"chars": count, "hard": hard, "warnings": warnings,
            "note": "仅检查长度、重复和可疑表达，不判断作者身份，也不保证原创性。"}
