#!/usr/bin/env bash
# 60 秒 demo：从零到「可重建的任务板」
set -e
cd "$(dirname "$0")/.."
export OPS_HOME="$(pwd)/.ops-demo"
rm -rf "$OPS_HOME"; mkdir -p "$OPS_HOME"
E="python3 src/events.py"

now=$(date -Iseconds)
later=$(date -d '+30 min' -Iseconds)

echo "① 建一条任务"
cat > /tmp/ev1.json <<JSON
{"id":"E-001","at":"$now","cor":"T001","kind":"task.created","source":"owner","ver":1,
 "payload":{"title":"给收尾脚本加视图重建"}}
JSON
$E append-file /tmp/ev1.json

echo
echo "② 认领（带租约与栅栏）"
cat > /tmp/ev2.json <<JSON
{"id":"E-002","at":"$now","cor":"T001","kind":"task.claimed","source":"owner","ver":1,
 "payload":{"by":"eng","lease_until":"$later","fence":1}}
JSON
$E append-file /tmp/ev2.json

echo
echo "③ 完成一次，再记一次（第二次应被拒）"
cat > /tmp/ev3.json <<JSON
{"id":"E-003","at":"$now","cor":"T001","kind":"task.completed","source":"eng","ver":1,
 "payload":{"by":"eng","evidence":"BOARD.md#L3"}}
JSON
$E append-file /tmp/ev3.json

cat > /tmp/ev4.json <<JSON
{"id":"E-004","at":"$now","cor":"T001","kind":"task.completed","source":"eng","ver":1,
 "payload":{"by":"eng","evidence":"再记一次"}}
JSON
$E append-file /tmp/ev4.json || true

echo
echo "④ 视图（完全由事件推出来）"
$E board

echo "⑤ 校验与摘要"
$E verify
$E digest

echo
echo "✓ demo 结束。数据目录：$OPS_HOME"
echo "  试试把 _events.local.jsonl 的最后一行删掉再跑 board —— 板子会退回「进行中」，因为它只是投影。"
