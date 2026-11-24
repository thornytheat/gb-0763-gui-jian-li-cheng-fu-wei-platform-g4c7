HEADER = 'event_id,run,kind,at,odo_mm,ref,grade'


def csv_row(run, kind, at, odo, ref, event_id, grade=3):
    return f'{event_id},{run},{kind},{at},{odo},{ref},{grade}'


def anchor(run, at, odo, ref, event_id=None, grade=3):
    return csv_row(run, 'anchor', at, odo, ref, event_id or f'{run}-{ref}', grade)


# UP-B 链标定：B0=2000000、B1=2100000、B2=2300000、B3=2400000。
# 上行车沿 B0→B3 递增；下行车沿 B3→B0 递减（同链同里程，方向相反）。
def up_run(run='U1', tag='u'):
    return '\n'.join([
        HEADER,
        anchor(run, '2026-11-22T00:00:00Z', 0, 'B0', f'{tag}-B0'),
        anchor(run, '2026-11-22T00:01:00Z', 100000, 'B1', f'{tag}-B1'),
        anchor(run, '2026-11-22T00:02:00Z', 300000, 'B2', f'{tag}-B2'),
        anchor(run, '2026-11-22T00:03:00Z', 400000, 'B3', f'{tag}-B3'),
    ])


def down_run(run='D1', tag='d'):
    return '\n'.join([
        HEADER,
        anchor(run, '2026-11-22T00:10:00Z', 0, 'B3', f'{tag}-B3'),
        anchor(run, '2026-11-22T00:11:00Z', 100000, 'B2', f'{tag}-B2'),
        anchor(run, '2026-11-22T00:12:00Z', 300000, 'B1', f'{tag}-B1'),
        anchor(run, '2026-11-22T00:13:00Z', 400000, 'B0', f'{tag}-B0'),
    ])


def archive(client, payload):
    return client.post('/api/runs', json=payload).json['run_id']


def issue(client, run_id):
    return client.post(f'/api/runs/{run_id}/issue').json


def combined_csv():
    return up_run() + '\n' + down_run().split('\n', 1)[1]


def test_single_direction_batch_has_zero_verified(client):
    run_id = archive(client, {'csv': up_run()})
    run = client.get(f'/api/runs/{run_id}').json
    assert run['manifest']['summary']['verified_mm'] == 0
    assert run['stages']['coverage']['pending_mm'] == 400000


def test_late_reverse_run_expands_new_sheet_but_old_sheet_keeps_scope(client):
    # 先归档并签发只有上行车的批次：当时已验范围为 0。
    first_id = archive(client, {'csv': up_run()})
    first_seal = issue(client, first_id)['seal']
    first_packet = client.get(f'/api/runs/{first_id}/export').json['evidence']
    assert first_packet['coverage']['verified_mm'] == 0

    # 迟到的反向车补齐相邻缺口：合并报文产生新批次，新单已验范围扩大到全链。
    combined = up_run() + '\n' + down_run().split('\n', 1)[1]
    second_id = archive(client, {'csv': combined})
    assert second_id != first_id
    second = client.get(f'/api/runs/{second_id}').json
    assert second['manifest']['summary']['verified_mm'] == 400000
    assert second['stages']['coverage']['verified_parts'][0]['up_runs'] == ['U1']
    assert second['stages']['coverage']['verified_parts'][0]['down_runs'] == ['D1']
    issue(client, second_id)
    second_packet = client.get(f'/api/runs/{second_id}/export').json['evidence']
    assert second_packet['coverage']['verified_mm'] == 400000

    # 旧单仍按签发当时的范围核验：证据内容与印章都不变。
    old_again = client.get(f'/api/runs/{first_id}/export').json
    assert old_again['verified'] is True
    assert old_again['seal'] == first_seal
    assert old_again['evidence']['coverage']['verified_mm'] == 0


def test_retry_after_issue_returns_original_sheet_without_new_evidence(client):
    run_id = archive(client, {'csv': combined_csv()})
    first = issue(client, run_id)
    # 模拟无回执后的重试：即使期间又有别的批次归档，原单只取回不新增。
    archive(client, {'csv': up_run('U2')})
    retry = issue(client, run_id)
    assert retry == dict(first, reused=True)
    assert client.get('/api/runs').json['runs'] and retry['seal'] == first['seal']
    assert client.get(f'/api/runs/{run_id}/export').json['verified'] is True


def test_issue_page_renders_verified_scope_from_snapshot(client):
    run_id = archive(client, {'csv': combined_csv()})
    page = client.get(f'/work/issue?run={run_id}').get_data(as_text=True)
    assert '双向已验 <b>400000</b>' in page
    assert 'UP-B · 2000000 → 2400000' in page
    assert '上行 U1 × 下行 D1' in page


def _round_trip_csv(return_run):
    return '\n'.join([
        HEADER,
        anchor('U1', '2026-11-22T00:00:00Z', 0, 'B0', 'go-B0'),
        anchor('U1', '2026-11-22T00:01:00Z', 100000, 'B1', 'go-B1'),
        anchor('U1', '2026-11-22T00:02:00Z', 300000, 'B2', 'go-B2'),
        anchor('U1', '2026-11-22T00:03:00Z', 400000, 'B3', 'go-B3'),
        anchor(return_run, '2026-11-22T01:00:00Z', 0, 'B3', 'rt-B3'),
        anchor(return_run, '2026-11-22T01:01:00Z', 100000, 'B2', 'rt-B2'),
        anchor(return_run, '2026-11-22T01:02:00Z', 300000, 'B1', 'rt-B1'),
        anchor(return_run, '2026-11-22T01:03:00Z', 400000, 'B0', 'rt-B0'),
    ])


def test_distinct_opposite_runs_verify_full_chain_end_to_end(client):
    run_id = archive(client, {'csv': _round_trip_csv('D9')})
    coverage = client.get(f'/api/runs/{run_id}').json['stages']['coverage']
    assert coverage['mapped_mm'] == 400000
    assert coverage['verified_mm'] == 400000
    track = next(row for row in coverage['tracks'] if row['chain'] == 'UP-B')
    assert track['up_runs'] == ['U1'] and track['down_runs'] == ['D9']


def test_same_run_round_trip_not_verified_end_to_end(client):
    # 同一车次往返（回程也挂 U1）：匹配阶段同基点冲突被留缺，没有可互证的反向区间。
    run_id = archive(client, {'csv': _round_trip_csv('U1')})
    coverage = client.get(f'/api/runs/{run_id}').json['stages']['coverage']
    assert coverage['verified_mm'] == 0
    assert coverage['verified_parts'] == []
    # 同 run 护栏由单元测试 test_same_run_round_trip_is_not_mutual_evidence 直接锁定。
