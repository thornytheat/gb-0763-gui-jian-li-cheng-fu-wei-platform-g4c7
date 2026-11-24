from app.coverage.balance import balance
from app.coverage.intervals import intersect, subtract, union

PLAN = [dict(chain='UP-B', start_mm=2000000, end_mm=2400000)]


def segment(run, chain, lo, hi, *, line=1):
    """构造一个有效双基点区间；lo/hi 按小→大给，方向由两个基点的里程顺序表达。"""
    left = dict(run=run, chain=chain, station_mm=lo, line=line, at='2026-11-22T00:00:00+00:00',
                odo_mm=0, ref=f'{run}-L')
    right = dict(run=run, chain=chain, station_mm=hi, line=line + 1, at='2026-11-22T00:01:00+00:00',
                 odo_mm=100000, ref=f'{run}-R')
    return {'left': left, 'right': right, 'reason': None, 'seconds': 60}


def down(segment_obj):
    """把上行区间翻转为下行（两端标定里程递减）。"""
    segment_obj['left'], segment_obj['right'] = segment_obj['right'], segment_obj['left']
    return segment_obj


def test_conservation(dirty):
    result = dirty['coverage']
    assert result['planned_mm'] == result['mapped_mm'] + result['missing_mm'] == 600000


def test_duplicate_coverage(dirty):
    assert (dirty['coverage']['mapped_mm'], dirty['coverage']['overlap_mm']) == (400000,100000)


def test_gap_explained(dirty):
    assert dirty['coverage']['missing_parts'] == [dict(chain='UP-B', start_mm=2100000, end_mm=2300000,
                                                      reasons=['SIGNAL_GAP'], sources=[5,6])]


def test_unplanned_chain_does_not_credit(dirty):
    result = balance([dict(chain='NONE',start_mm=1000000,end_mm=1100000)], dirty['chainage']['segments'], [])
    assert (result['mapped_mm'], result['missing_mm']) == (0, 100000)


def test_touching_and_contained():
    assert union([(0,7),(7,10),(1,2),(19,19)]) == [[0,10]]


def test_clean_api(client):
    result = client.get('/api/coverage?sample=clean').json
    assert (result['mapped_mm'], result['missing_parts']) == (600000, [])


def test_single_direction_not_verified():
    # 只有一趟上行车：整段已映射但无反向互证，不能虚报双向已验。
    result = balance(PLAN, [segment('R1', 'UP-B', 2000000, 2400000)], [])
    assert (result['mapped_mm'], result['verified_mm'], result['pending_mm']) == (400000, 0, 400000)
    assert result['verified_parts'] == []
    assert result['pending_parts'] == [dict(chain='UP-B', start_mm=2000000, end_mm=2400000)]


def test_opposite_runs_verify_overlap_only():
    # 两趟反向车只在 [2100000, 2300000) 重合：只认重合段，断链两侧不拼接。
    segments = [segment('R1', 'UP-B', 2000000, 2300000),
                down(segment('R2', 'UP-B', 2100000, 2400000))]
    result = balance(PLAN, segments, [])
    assert result['verified_mm'] == 200000
    assert result['verified_parts'] == [
        dict(chain='UP-B', start_mm=2100000, end_mm=2300000, up_runs=['R1'], down_runs=['R2'])]
    assert result['pending_parts'] == [
        dict(chain='UP-B', start_mm=2000000, end_mm=2100000),
        dict(chain='UP-B', start_mm=2300000, end_mm=2400000)]
    assert result['mapped_mm'] == 400000


def test_same_run_round_trip_is_not_mutual_evidence():
    # 同一车次往返（R3 自己上行又自己下行）不算双向互证。
    segments = [segment('R3', 'UP-B', 2000000, 2400000),
                down(segment('R3', 'UP-B', 2000000, 2400000, line=9))]
    result = balance(PLAN, segments, [])
    assert (result['mapped_mm'], result['verified_mm'], result['pending_mm']) == (400000, 0, 400000)
    assert result['tracks'][0]['up_runs'] == ['R3']
    assert result['tracks'][0]['down_runs'] == ['R3']


def test_same_station_on_different_chains_never_cross_verifies():
    # 断链两侧的同名里程分属不同链：UP-B 与 DN-X 的 [2100000, 2300000) 数值相同也不互证。
    plan = PLAN + [dict(chain='DN-X', start_mm=2100000, end_mm=2300000)]
    segments = [segment('R1', 'UP-B', 2100000, 2300000),
                down(segment('R2', 'DN-X', 2100000, 2300000))]
    result = balance(plan, segments, [])
    assert result['verified_mm'] == 0
    assert result['verified_parts'] == []
    assert result['pending_mm'] == 400000


def test_two_up_one_down_partial_overlap():
    # 下行车只与两趟上行车的相邻片段各重合一段，已验范围是两段重合的并集。
    segments = [segment('R1', 'UP-B', 2000000, 2150000),
                segment('R2', 'UP-B', 2250000, 2400000),
                down(segment('R4', 'UP-B', 2100000, 2300000))]
    result = balance(PLAN, segments, [])
    assert result['verified_parts'] == [
        dict(chain='UP-B', start_mm=2100000, end_mm=2150000, up_runs=['R1'], down_runs=['R4']),
        dict(chain='UP-B', start_mm=2250000, end_mm=2300000, up_runs=['R2'], down_runs=['R4'])]
    assert result['verified_mm'] == 100000
    # 中间 [2150000, 2250000) 双向都缺：映射仍在（待签），不计缺证
    assert result['pending_mm'] == 300000
    assert result['missing_mm'] == 0
    assert result['mapped_mm'] == 400000


def test_verified_scope_is_clipped_to_plan():
    plan = [dict(chain='UP-B', start_mm=2100000, end_mm=2300000)]
    segments = [segment('R1', 'UP-B', 2000000, 2400000),
                down(segment('R2', 'UP-B', 2000000, 2400000))]
    result = balance(plan, segments, [])
    assert (result['planned_mm'], result['verified_mm']) == (200000, 200000)
    assert result['verified_parts'] == [
        dict(chain='UP-B', start_mm=2100000, end_mm=2300000, up_runs=['R1'], down_runs=['R2'])]


def test_interval_helpers():
    assert union([(0, 7), (7, 10), (1, 2), (19, 19)]) == [[0, 10]]
    assert intersect([(0, 10)], [(4, 6), (8, 12)]) == [[4, 6], [8, 10]]
    assert subtract([(0, 10)], [(2, 5), (9, 11)]) == [[0, 2], [5, 9]]
