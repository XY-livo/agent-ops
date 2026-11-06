#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""events.py —— 事件流内核（append-only，零依赖）

一句话：**状态不存，全部由事件推出来；视图永远可重建。**

三条硬规矩：
  1. 只追加，不改写：一条事件落盘后永不修改（要改就再发一条）
  2. 视图是投影：任务板/队列/digest 都是算出来的，随时可 `board --write` 重建
  3. 同一件事不许记两次：业务键（biz_key）重复 → 直接拒

用法：
    export OPS_HOME=$PWD/.ops        # 数据目录（默认 ./.ops）
    python3 src/events.py append-file e.json
    python3 src/events.py append '{"kind":"task.created",...}'   # 仅 bash；Windows 请用 append-file
    python3 src/events.py list [--cor T001] [--kind task.created] [--tail 20]
    python3 src/events.py state --cor T001
    python3 src/events.py board [--write]
    python3 src/events.py lease
    python3 src/events.py digest
    python3 src/events.py verify
    python3 src/events.py selftest

为什么 Windows 上必须 append-file：
  PowerShell 会把命令行里的双引号剥掉，JSON 直接坏掉。
  **结构化数据一律写文件、传路径**——这条是踩出来的，不是讲究。
"""
import json
import os
import re
import sys
import hashlib
import datetime

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')     # GBK 控制台打印中文会崩
    except Exception:
        pass

HOME = os.environ.get('OPS_HOME') or os.path.join(os.getcwd(), '.ops')
SIDE = os.environ.get('OPS_SIDE', 'local')          # local / cloud：分片用
SHARD = os.path.join(HOME, '_events.%s.jsonl' % SIDE)
BOARD = os.path.join(HOME, 'BOARD.md')

KINDS = {
    'task.created', 'task.claimed', 'task.completed', 'task.blocked', 'task.dropped',
    'note', 'check.passed', 'check.failed', 'lease.renewed',
}
REQUIRED = ['id', 'at', 'cor', 'kind', 'source', 'ver']
# 每类事件的 payload 必填项（schema 在入口，不做考古）
SCHEMA = {
    'task.created': ['title'],
    'task.claimed': ['by', 'lease_until', 'fence'],
    'task.completed': ['by', 'evidence'],
    'task.blocked': ['why'],
    'task.dropped': ['why'],
    'lease.renewed': ['lease_until', 'fence'],
}
AT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$')


def now_iso():
    d = datetime.datetime.now().astimezone()
    return d.isoformat(timespec='seconds')


def load():
    """读所有分片（按分片顺序 + 行号，不按时间排：同一秒可能有多条）。"""
    out, bad = [], []
    for side in ('local', 'cloud'):
        p = os.path.join(HOME, '_events.%s.jsonl' % side)
        if not os.path.exists(p):
            continue
        for i, line in enumerate(open(p, encoding='utf-8'), 1):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                e['_side'] = side
                e['_line'] = i
                out.append(e)
            except Exception:
                bad.append((side, i))
    return out, bad


def validate(ev, check_payload=True):
    errs = []
    if not isinstance(ev, dict):
        return ['不是对象']
    for k in REQUIRED:
        if k not in ev:
            errs.append('缺必填 %s' % k)
    if ev.get('kind') not in KINDS:
        errs.append('kind 不在枚举：%s' % ev.get('kind'))
    if 'at' in ev and not AT_RE.match(str(ev['at'])):
        errs.append('at 必须带时区（例 2026-11-06T10:00:00+08:00）')
    if check_payload:
        need = SCHEMA.get(ev.get('kind'), [])
        pl = ev.get('payload') or {}
        for k in need:
            if k not in pl or pl[k] in ('', None):
                errs.append('%s 的 payload 缺 %s' % (ev.get('kind'), k))
    return errs


def biz_key(ev):
    """幂等键：同一件事重复提交要能被认出来。
    口径：kind + cor + 关键取值（不同的 kind 用不同的键，别共用一个）。"""
    k = ev.get('kind', '')
    cor = ev.get('cor', '')
    pl = ev.get('payload') or {}
    if k == 'task.created':
        return 'created|%s|%s' % (cor, pl.get('title', ''))
    if k == 'task.claimed':
        return 'claimed|%s|fence=%s' % (cor, pl.get('fence', ''))
    if k == 'task.completed':
        return 'completed|%s' % cor
    if k in ('task.blocked', 'task.dropped'):
        return '%s|%s' % (k, cor)
    if k == 'lease.renewed':
        return 'renew|%s|fence=%s' % (cor, pl.get('fence', ''))
    return ''


def append(ev):
    os.makedirs(HOME, exist_ok=True)
    errs = validate(ev)
    if errs:
        return False, '拒收：' + '；'.join(errs)
    evs, _ = load()
    for e in evs:
        if e.get('id') == ev.get('id'):
            return False, '拒收：id 重复 %s' % ev['id']
    bk = biz_key(ev)
    if bk:
        for e in evs:
            if biz_key(e) == bk:
                return False, '拒收：同一件事已记过（%s，已有 %s）' % (bk, e.get('id'))
    with open(SHARD, 'a', encoding='utf-8') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    return True, '写入 %s' % os.path.basename(SHARD)


def state_of(cor):
    evs, _ = load()
    rows = [e for e in evs if e.get('cor') == cor]
    if not rows:
        return None
    rows.sort(key=lambda e: (e.get('_side'), e.get('_line')))
    st = {'cor': cor, 'events': len(rows), 'created': None, 'owner': None,
          'state': '未开工', 'evidence': '', 'why': '', 'lease_until': None, 'fence': None}
    for e in rows:
        k = e.get('kind')
        pl = e.get('payload') or {}
        if k == 'task.created':
            st['created'] = e.get('at')
            st['state'] = '未开工'
        elif k == 'task.claimed':
            st['owner'] = pl.get('by')
            st['state'] = '进行中'
            st['lease_until'] = pl.get('lease_until')
            st['fence'] = pl.get('fence')
        elif k == 'lease.renewed':
            st['lease_until'] = pl.get('lease_until')
            st['fence'] = pl.get('fence')
        elif k == 'task.completed':
            st['state'] = '已结'
            st['evidence'] = pl.get('evidence', '')
        elif k == 'task.blocked':
            st['state'] = '挂起'
            st['why'] = pl.get('why', '')
        elif k == 'task.dropped':
            st['state'] = '已丢'
            st['why'] = pl.get('why', '')
    return st


def all_tasks():
    evs, _ = load()
    seen, out = [], []
    for e in evs:
        c = e.get('cor')
        if c and c not in seen:
            seen.append(c)
    for c in seen:
        s = state_of(c)
        if s:
            out.append(s)
    return out


def expired(s):
    if s.get('state') != '进行中' or not s.get('lease_until'):
        return False
    try:
        until = datetime.datetime.fromisoformat(s['lease_until'])
        return until < datetime.datetime.now().astimezone()
    except Exception:
        return False


def board_text():
    rows = all_tasks()
    d = digest()
    lines = [
        '# 任务板（视图 · 由事件流重建）',
        '',
        '> 本文件是**投影**，不是真相。真相在 `_events.*.jsonl`。',
        '> 重建：`python3 src/events.py board --write`',
        '> state_digest = `%s`（未结 %d / 进行中 %d / 共 %d）' % (
            d['digest'], d['open'], d['running'], d['tasks']),
        '',
        '| 任务 | 状态 | 归属 | 租约到期 | 证据 |',
        '|---|---|---|---|---|',
    ]
    for s in rows:
        lines.append('| %s | %s%s | %s | %s | %s |' % (
            s['cor'], s['state'], '（过期）' if expired(s) else '',
            s.get('owner') or '', (s.get('lease_until') or '')[11:16], (s.get('evidence') or '')[:40]))
    if not rows:
        lines.append('| （空） | | | | |')
    return '\n'.join(lines) + '\n'


def digest():
    rows = all_tasks()
    open_n = len([s for s in rows if s['state'] in ('未开工', '进行中', '挂起')])
    running = len([s for s in rows if s['state'] == '进行中'])
    raw = '%d|%d|%d' % (open_n, len(rows), running)
    return {'digest': hashlib.sha256(raw.encode()).hexdigest()[:8],
            'open': open_n, 'tasks': len(rows), 'running': running}


def verify():
    evs, bad = load()
    errs = []
    seen = {}
    for e in evs:
        errs += ['%s#%d %s' % (e['_side'], e['_line'], x) for x in validate(e)]
        i = e.get('id')
        if i in seen:
            errs.append('id 重复：%s' % i)
        seen[i] = 1
    return len(evs), bad, errs


def cmd_append(ev):
    ok, msg = append(ev)
    print(('✓ ' if ok else '✗ ') + msg)
    return 0 if ok else 1


def main():
    a = sys.argv[1:]
    if not a or a[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    c = a[0]

    if c == 'append-file':
        p = a[1] if len(a) > 1 else ''
        if not p or not os.path.exists(p):
            print('✗ 找不到文件：%s' % p)
            return 2
        return cmd_append(json.load(open(p, encoding='utf-8')))

    if c == 'append':
        if len(a) < 2:
            print('✗ 缺 JSON（Windows 请改用 append-file）')
            return 2
        return cmd_append(json.loads(a[1]))

    if c == 'list':
        evs, _ = load()
        cor = a[a.index('--cor') + 1] if '--cor' in a else ''
        kind = a[a.index('--kind') + 1] if '--kind' in a else ''
        tail = int(a[a.index('--tail') + 1]) if '--tail' in a else 30
        rows = [e for e in evs if (not cor or e.get('cor') == cor) and (not kind or e.get('kind') == kind)]
        for e in rows[-tail:]:
            print('%s  %-16s %-6s %s' % (str(e.get('at'))[5:19].replace('T', ' '),
                                         e.get('kind'), e.get('cor'), (e.get('payload') or {}).get('title', '')))
        print('共 %d 条' % len(rows))
        return 0

    if c == 'state':
        cor = a[a.index('--cor') + 1] if '--cor' in a else ''
        s = state_of(cor)
        if not s:
            print('没有这条链：%s' % cor)
            return 3
        print(json.dumps(s, ensure_ascii=False, indent=1))
        return 0

    if c == 'board':
        txt = board_text()
        if '--write' in a:
            os.makedirs(HOME, exist_ok=True)
            open(BOARD, 'w', encoding='utf-8').write(txt)
            print('✓ 已写入 %s' % BOARD)
        print(txt)
        return 0

    if c == 'lease':
        rows = [s for s in all_tasks() if s['state'] == '进行中']
        if not rows:
            print('没有进行中的任务')
            return 0
        for s in rows:
            print('%-10s %-8s 租约到 %s  fence=%s %s' % (
                s['cor'], s.get('owner'), (s.get('lease_until') or '')[11:16],
                s.get('fence'), '已过期' if expired(s) else '生效中'))
        return 0

    if c == 'digest':
        print(json.dumps(digest(), ensure_ascii=False))
        return 0

    if c == 'verify':
        n, bad, errs = verify()
        print('事件 %d 条；坏行 %d；问题 %d' % (n, len(bad), len(errs)))
        for e in errs[:10]:
            print('  ✗ ' + e)
        return 0 if not (bad or errs) else 3

    if c == 'selftest':
        return selftest()

    print('未知命令：%s' % c)
    return 2


def selftest():
        import tempfile
        import shutil
        global HOME, SHARD
        d = tempfile.mkdtemp(prefix='ops-selftest-')
        HOME = d
        SHARD = os.path.join(HOME, '_events.local.jsonl')
        fails = []

        def ok(cond, what):
            if not cond:
                fails.append(what)

        t = datetime.datetime.now().astimezone()
        iso = t.isoformat(timespec='seconds')
        later = (t + datetime.timedelta(minutes=30)).isoformat(timespec='seconds')

        e1 = {'id': 'E1', 'at': iso, 'cor': 'T1', 'kind': 'task.created', 'source': 'me', 'ver': 1,
              'payload': {'title': '写一页说明'}}
        ok(append(e1)[0], '创建任务')
        ok(not append(dict(e1, id='E1b'))[0], '同一件事重复提交应被拒（biz_key）')
        e2 = {'id': 'E2', 'at': iso, 'cor': 'T1', 'kind': 'task.claimed', 'source': 'me', 'ver': 1,
              'payload': {'by': 'agent', 'lease_until': later, 'fence': 1}}
        ok(append(e2)[0], '认领')
        ok(not append({'id': 'E3', 'at': iso, 'cor': 'T1', 'kind': 'task.completed', 'source': 'me',
                       'ver': 1, 'payload': {'by': 'agent'}})[0], '缺 evidence 应被拒')
        ok(append({'id': 'E4', 'at': iso, 'cor': 'T1', 'kind': 'task.completed', 'source': 'me',
                   'ver': 1, 'payload': {'by': 'agent', 'evidence': 'BOARD.md#L3'}})[0], '完成')
        ok(not append({'id': 'E5', 'at': iso, 'cor': 'T1', 'kind': 'task.completed', 'source': 'me',
                       'ver': 1, 'payload': {'by': 'agent', 'evidence': 'x'}})[0], '完成事件不可重复记')
        s = state_of('T1')
        ok(s['state'] == '已结', '状态应由事件推出=已结，实际 %s' % s['state'])
        ok('T1' in board_text(), '板子应包含 T1')
        n, bad, errs = verify()
        ok(not bad and not errs, 'verify 应无问题')
        shutil.rmtree(d, ignore_errors=True)
        if fails:
            print('✗ 自测失败：')
            for f in fails:
                print('  - ' + f)
            return 1
        print('✓ 自测通过：追加 / 幂等拒收 / 必填校验 / 状态推导 / 视图重建 / 完成不可重复 / verify')
        return 0


if __name__ == '__main__':
    sys.exit(main())
