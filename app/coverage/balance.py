"""轨段覆盖对账与双向验收范围。

基线口径（DATA-DICTIONARY M5）：按链把计划区间、有效双基点区间各自求并后取交，
重复覆盖单列，缺证片段回溯断链/掉线缺口原因。

双向口径（交班签发）：
- 区间方向按两端标定里程的增减判定，递增为上行、递减为下行；
- 同一车次的往返不互证，只有不同车次的上、下行区间相交才算双向已验；
- 一切按链分组，断链两侧的同名里程分属不同链，永远不会互相求交；
- 两趟反向车只在重合段互证，断链处自然断链，不做跨段拼接；
- 已验范围是复位区间的子集，单向先签的页面只能展示这些重合段。
"""
from collections import defaultdict

from .intervals import intersect, measure, subtract, union

UP = 'up'
DN = 'down'


def _span(segment):
    """返回 (链, 区间下界, 区间上界, 方向)；下行链标称里程递减，区间仍按小→大登记。"""
    left, right = segment['left'], segment['right']
    lo, hi = sorted((left['station_mm'], right['station_mm']))
    return left['chain'], lo, hi, _direction(segment)


def _direction(segment):
    delta = segment['right']['station_mm'] - segment['left']['station_mm']
    if delta > 0:
        return UP
    if delta < 0:
        return DN
    return None


def _chain_plans(plan):
    ordered, grouped = [], defaultdict(list)
    for item in plan:
        if not grouped[item['chain']]:
            ordered.append(item['chain'])
        grouped[item['chain']].append((item['start_mm'], item['end_mm']))
    return ordered, {chain: union(parts) for chain, parts in grouped.items()}


def _missing_reasons(chain, start, end, gaps):
    reasons, sources = [], []
    for gap in gaps:
        if gap['chain'] != chain or gap['end_mm'] <= start or gap['start_mm'] >= end:
            continue
        if gap['reason'] not in reasons:
            reasons.append(gap['reason'])
        for line in gap['sources']:
            if line not in sources:
                sources.append(line)
    if not reasons:
        reasons = ['NO_COVERAGE']
    return reasons, sorted(sources)


def _verified_pieces(directed):
    """逐对求不同车次的反向重合段，保留每段的互证车次。"""
    pieces = []
    ups = directed[UP]
    downs = directed[DN]
    for up_run, up_parts in ups:
        for down_run, down_parts in downs:
            if up_run == down_run:  # 同一车次往返不互证
                continue
            for lo, hi in intersect(up_parts, down_parts):
                pieces.append({'lo': lo, 'hi': hi, 'up_run': up_run, 'down_run': down_run})
    return pieces


def _merge_verified(pieces):
    """把带车次标注的重合段归并为连续已验片段，合并互证车次集合。"""
    merged = []
    for piece in sorted(pieces, key=lambda item: (item['lo'], item['hi'])):
        if merged and piece['lo'] <= merged[-1]['end_mm']:
            current = merged[-1]
            current['end_mm'] = max(current['end_mm'], piece['hi'])
            for key, run in (('up_runs', piece['up_run']), ('down_runs', piece['down_run'])):
                if run not in current[key]:
                    current[key].append(run)
        else:
            merged.append({'start_mm': piece['lo'], 'end_mm': piece['hi'],
                           'up_runs': [piece['up_run']], 'down_runs': [piece['down_run']]})
    for item in merged:
        item['up_runs'].sort()
        item['down_runs'].sort()
    return merged


def balance(plan, segments, gaps):
    plan_order, plans = _chain_plans(plan)

    raw_parts = defaultdict(list)          # 按链的原始有效区间（保留重复）
    directed = defaultdict(lambda: {UP: [], DN: []})
    for segment in segments:
        chain, start, end, direction = _span(segment)
        if chain not in plans or end <= start:
            continue
        raw_parts[chain].append((start, end))
        if direction is None:
            continue
        run = segment['left']['run']
        directed[chain][direction].append((run, [(start, end)]))

    tracks, missing_parts, verified_parts, pending_parts = [], [], [], []
    totals = dict(planned_mm=0, mapped_mm=0, missing_mm=0, overlap_mm=0,
                  verified_mm=0, pending_mm=0)

    for chain in plan_order:
        plan_parts = plans[chain]
        planned = measure(plan_parts)
        mapped_parts = intersect(raw_parts[chain], plan_parts)
        mapped = measure(mapped_parts)
        raw_clipped = sum(measure(intersect([part], plan_parts)) for part in raw_parts[chain])

        # 同链同方向先按车次各自求并，再让不同车次的反向区间两两重合。
        per_chain = {UP: {}, DN: {}}
        for direction in (UP, DN):
            for run, parts in directed[chain][direction]:
                per_chain[direction].setdefault(run, []).extend(parts)
        grouped = {direction: sorted((run, union(parts)) for run, parts in runs.items())
                   for direction, runs in per_chain.items()}

        # 先在复位区间上找反向重合段，再裁剪到计划轨段：已验范围永远是计划的子集。
        chain_verified = []
        for item in _merge_verified(_verified_pieces(grouped)):
            for lo, hi in intersect([(item['start_mm'], item['end_mm'])], plan_parts):
                chain_verified.append(dict(item, chain=chain, start_mm=lo, end_mm=hi))
        verified_plain = union((item['start_mm'], item['end_mm']) for item in chain_verified)
        verified = measure(verified_plain)
        pending_plain = subtract(mapped_parts, verified_plain)
        pending = measure(pending_plain)

        chain_missing = subtract(plan_parts, mapped_parts)
        for start, end in chain_missing:
            reasons, sources = _missing_reasons(chain, start, end, gaps)
            missing_parts.append({'chain': chain, 'start_mm': start, 'end_mm': end,
                                  'reasons': reasons, 'sources': sources})

        run_names = {UP: sorted(per_chain[UP]), DN: sorted(per_chain[DN])}
        tracks.append({'chain': chain, 'start_mm': plan_parts[0][0], 'end_mm': plan_parts[-1][1],
                       'planned_mm': planned, 'mapped_mm': mapped, 'missing_mm': planned - mapped,
                       'verified_mm': verified, 'pending_mm': pending,
                       'mapped_parts': mapped_parts, 'verified_parts': verified_plain,
                       'up_runs': run_names[UP], 'down_runs': run_names[DN]})
        for item in chain_verified:
            verified_parts.append(item)
        for start, end in pending_plain:
            pending_parts.append({'chain': chain, 'start_mm': start, 'end_mm': end})

        totals['planned_mm'] += planned
        totals['mapped_mm'] += mapped
        totals['missing_mm'] += planned - mapped
        totals['overlap_mm'] += raw_clipped - mapped
        totals['verified_mm'] += verified
        totals['pending_mm'] += pending

    return dict(totals, tracks=tracks, missing_parts=missing_parts,
                verified_parts=verified_parts, pending_parts=pending_parts)
