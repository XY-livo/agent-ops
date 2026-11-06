#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sanitize.py —— 脱敏扫描器（开源前跑一遍）

做什么：把待开源的目录扫一遍，按规则找出「不该出门的东西」，给出位置和替换建议。
不做：不自动改文件（改错比漏改危险），只出报告；--fix 时按规则替换并留 .bak。

规则分四类：
  1) 绝对路径（D:\\ / C:\\ / /path/…）
  2) 人名/称呼（可配置黑名单）
  3) 凭据（ghp_/sk-/token/密码字段）
  4) 账号与个人标识（QQ 号、手机号、邮箱）

用法：
  python3 sanitize.py <目录>                 # 只扫描，出报告
  python3 sanitize.py <目录> --fix           # 替换 + 备份
  python3 sanitize.py <目录> --names 名单.txt # 额外的人名黑名单（一行一个）
"""
import os
import re
import sys
import shutil

RULES = [
    ('绝对路径-Windows', re.compile(r'[A-Za-z]:\\\\[^\s"\')]+')),
    ('绝对路径-类Unix', re.compile(r'/(?:opt|home|root|Users)/[^\s"\')]+')),
    ('GitHub token', re.compile(r'gh[pousr]_[A-Za-z0-9_]{10,}')),
    ('OpenAI/DeepSeek key', re.compile(r'sk-[A-Za-z0-9_\-]{16,}')),
    ('邮箱', re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')),
    ('手机号', re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')),
    ('QQ号(5-12位纯数字串)', re.compile(r'(?<![\d.])\d{6,12}(?![\d.])')),
]

SKIP_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.pdf', '.zip', '.pyc', '.woff', '.woff2'}
SKIP_DIR = {'.git', 'node_modules', '__pycache__', 'vault'}


def walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIR]
        for f in files:
            if os.path.splitext(f)[1].lower() in SKIP_EXT:
                continue
            yield os.path.join(d, f)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    root = a[0]
    fix = '--fix' in a
    names = []
    if '--names' in a:
        p = a[a.index('--names') + 1]
        names = [x.strip() for x in open(p, encoding='utf-8') if x.strip()]

    hits = []
    for path in walk(root):
        try:
            text = open(path, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        for name, rx in RULES:
            for m in rx.finditer(text):
                line = text.count('\n', 0, m.start()) + 1
                hits.append((path, line, name, m.group(0)))
        for nm in names:
            for m in re.finditer(re.escape(nm), text):
                line = text.count('\n', 0, m.start()) + 1
                hits.append((path, line, '人名:' + nm, nm))

    if not hits:
        print('✓ 没扫到敏感内容（%d 类规则 + %d 个人名）' % (len(RULES), len(names)))
        return 0

    print('扫到 %d 处，按文件汇总：\n' % len(hits))
    byfile = {}
    for p, ln, kind, val in hits:
        byfile.setdefault(p, []).append((ln, kind, val))
    for p, rows in sorted(byfile.items()):
        print('● ' + os.path.relpath(p, root))
        for ln, kind, val in rows[:12]:
            print('   L%-4d %-18s %s' % (ln, kind, val[:60]))
        if len(rows) > 12:
            print('   …另 %d 处' % (len(rows) - 12))

    if not fix:
        print('\n（只报告，未修改。要替换加 --fix；替换前会谈清单让你确认）')
        return 3

    # --fix：只处理"可直接替换"的三类，人名与路径按占位符改写
    REPL = [
        (re.compile(r'[A-Za-z]:\\\\Users\\\\[^\\\\\s]+'), '<HOME>'),
        (re.compile(r'[A-Za-z]:\\\\[^\s"\')]+'), '<PATH>'),
        (re.compile(r'/(?:opt|home|root)/[^\s"\')]+'), '<PATH>'),
    ]
    changed = 0
    for p in byfile:
        s = open(p, encoding='utf-8', errors='replace').read()
        bak = p + '.bak-sanitize'
        if not os.path.exists(bak):
            shutil.copyfile(p, bak)
        for nm in names:
            s = s.replace(nm, 'OWNER')
        for rx, rep in REPL:
            s = rx.sub(rep, s)
        open(p, 'w', encoding='utf-8').write(s)
        changed += 1
    print('\n✓ 已替换 %d 个文件（备份 .bak-sanitize）。**凭据/QQ/邮箱必须人工处理**，脚本不动它们。' % changed)
    return 0


if __name__ == '__main__':
    sys.exit(main())
