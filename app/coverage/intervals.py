"""半开里程区间的归并与求交，单位 mm。

断链两侧的同名里程分属不同链，调用方必须按链分组后再使用这里的原语，
绝不能把不同链的区间放进同一次 union/intersect。
"""


def _normalized(intervals):
    parts = []
    for item in intervals:
        start, end = item
        if end > start:
            parts.append((start, end))
    return parts


def union(intervals):
    """把若干半开区间归并为互不重叠、按起点排序的 [lo, hi) 列表。"""
    merged = []
    for start, end in sorted(_normalized(intervals)):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def intersect(left, right):
    """两组已归并区间的交集，结果仍为归并后的半开区间。"""
    overlap, i, j = [], 0, 0
    left, right = union(left), union(right)
    while i < len(left) and j < len(right):
        lo = max(left[i][0], right[j][0])
        hi = min(left[i][1], right[j][1])
        if hi > lo:
            overlap.append([lo, hi])
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return overlap


def subtract(left, right):
    """从已归并区间 left 中挖去 right 覆盖的部分。"""
    remain = []
    right = union(right)
    for start, end in union(left):
        cursor = start
        for lo, hi in right:
            if hi <= cursor:
                continue
            if lo >= end:
                break
            if lo > cursor:
                remain.append([cursor, lo])
            cursor = max(cursor, hi)
        if cursor < end:
            remain.append([cursor, end])
    return union(remain)


def measure(intervals):
    return sum(end - start for start, end in union(intervals))
