from __future__ import annotations

import shutil
import subprocess

import pytest

from tinytalk.eval.oracle import oracle_pass


ENV_CORRECT = (
    "awk -F= 'NR==FNR {if ($1 !~ /^#/ && $1 != \"\") seen[$1]=1; next} "
    "$1 !~ /^#/ && $1 != \"\" && !($1 in seen) {print FNR, $1}' .env .env.example"
)
ENV_BROKEN = (
    "awk -F= 'NR==FNR {if ($1 !~ /^#/ && $1 != \"\" && $1 != \" \") {keys[$1] = NR}; next} "
    "{if ($1 in keys == 0 && $1 !~ /^#/ && $1 != \"\" && $1 != \" \") "
    "{print \"Line \" keys[$1] \": \" $1}}' .env.example .env"
)

LOG_CORRECT = (
    "awk '$9 !~ /^2/ {split($4, a, \":\"); m=a[2] \":\" a[3]; "
    "if (m >= \"14:00\" && m <= \"14:15\") seen[m SUBSEP $1]=1} "
    "END {for (k in seen) {split(k, a, SUBSEP); count[a[1]]++} "
    "for (m in count) print m, count[m]}' access.log"
)
LOG_BROKEN = (
    "grep '14:[0-1][0-5]:' access.log | grep -v '\" 2[0-9][0-9] ' | "
    "awk '{print $1, substr($4, 14, 5)}' | sort | uniq -c"
)

YAML_CORRECT = r"""python3 - <<'PY'
from pathlib import Path
import re

for path in Path(".").rglob("*.yaml"):
    if any(part in {"charts", "vendor"} for part in path.parts):
        continue
    for line in path.read_text().splitlines():
        match = re.search(r"image:\s*['\"]?([^'\"\s]+)", line)
        if not match:
            continue
        image = match.group(1)
        last = image.rsplit("/", 1)[-1]
        tag = last.rsplit(":", 1)[1] if ":" in last else None
        if tag is None or tag == "latest":
            print(image)
PY"""
YAML_YQ_CORRECT = (
    "find . -path './charts' -prune -o -path './vendor' -prune -o -name '*.yaml' -print0 | "
    "xargs -0 yq -r '.. | .image? // empty' | "
    "awk '{last=$0; sub(/^.*\\//, \"\", last); if (last !~ /:/ || last ~ /:latest$/) print $0}'"
)
YAML_BROKEN = (
    "find . -name '*.yaml' -not -path './charts/*' -not -path './vendor/*' -print0 | "
    "xargs -0 awk -F: '/image:/ {print $2}'"
)

K8S_CORRECT = r"""kubectl get secrets --all-namespaces -o json | python3 -c '
import datetime as dt
import json
import sys

now = dt.datetime.now(dt.timezone.utc)
for item in json.load(sys.stdin)["items"]:
    if item["type"] != "kubernetes.io/tls":
        continue
    created = dt.datetime.strptime(
        item["metadata"]["creationTimestamp"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=dt.timezone.utc)
    if (now - created).days > 90:
        print(item["metadata"]["namespace"], item["metadata"]["name"])
'"""
K8S_BROKEN = (
    "kubectl get secrets --all-namespaces -o json | jq -r '.items[] | "
    "select(.type == \"kubernetes.io/tls\") | "
    "select((now - (.metadata.creationTimestamp | fromdateiso8601)) > 7776000) | "
    "\"\\(.metadata.namespace) \\(.metadata.name) \\(.metadata.creationTimestamp)\" | @tsv'"
)
K8S_FIELD_SELECTOR_CORRECT = (
    "kubectl get secrets -A --field-selector type=kubernetes.io/tls -o json | "
    "jq -r --arg cutoff \"$(date -v-90d +%Y-%m-%dT%H:%M:%SZ)\" "
    "'.items[] | select(.metadata.creationTimestamp < $cutoff) | "
    "[.metadata.namespace, .metadata.name, .metadata.creationTimestamp] | @tsv'"
)

JSONL_P95_CORRECT = r"""python3 - <<'PY'
import json, math
from collections import defaultdict

latencies = defaultdict(list)
with open("requests.jsonl") as fh:
    for line in fh:
        row = json.loads(line)
        if 500 <= row["status"] <= 599:
            latencies[row["route"]].append(row["latency_ms"])
rows = []
for route, values in latencies.items():
    values.sort()
    rows.append((route, values[math.ceil(0.95 * len(values)) - 1]))
for route, p95 in sorted(rows, key=lambda row: row[1], reverse=True)[:3]:
    print(route, p95)
PY"""
JSONL_P95_BROKEN = r"""python3 - <<'PY'
import json
for line in open("requests.jsonl"):
    row = json.loads(line)
    print(row["route"], row["latency_ms"])
PY"""

CSV_JOIN_CORRECT = r"""python3 - <<'PY'
import csv
from collections import defaultdict

with open("customers.csv", newline="") as fh:
    emails = {row["customer_id"]: row["email"] for row in csv.DictReader(fh)}
totals = defaultdict(float)
with open("orders.csv", newline="") as fh:
    for row in csv.DictReader(fh):
        totals[emails[row["customer_id"]]] += float(row["amount"])
for email, total in totals.items():
    print(email, f"{total:.2f}")
PY"""
CSV_JOIN_BROKEN = (
    "awk -F, 'NR==FNR {email[$1]=$3; next} FNR>1 {sum[email[$2]]+=$3} "
    "END {for (e in sum) print e, sum[e]}' customers.csv orders.csv"
)

HARDLINK_CORRECT = r"""python3 - <<'PY'
import os
from collections import defaultdict

groups = defaultdict(list)
for root, _, files in os.walk("."):
    for name in files:
        path = os.path.join(root, name)
        st = os.stat(path)
        if st.st_nlink > 1:
            groups[st.st_ino].append(path)
for inode, paths in groups.items():
    if len(paths) > 1:
        print(inode, *sorted(paths))
PY"""
HARDLINK_INODE_HEADER_CORRECT = (
    "find . -type f -links +1 -exec stat -f '%i %N' {} \\; | sort -n | "
    "awk '{if($1!=p){if(p!=\"\")print \"\";print \"inode \"$1\":\"};print \"  \"$2;p=$1}'"
)
HARDLINK_BROKEN = "find . -type f -print"

COUNT_LINES_CORRECT = "find . -name '*.py' -print0 | xargs -0 wc -l | tail -n 1 | awk '{print $1}'"
COUNT_LINES_FD_CORRECT = "fd -e py . | xargs wc -l | tail -n 1 | awk '{print $1}'"
COUNT_LINES_BROKEN = "find . -type f -print0 | xargs -0 wc -l | tail -n 1 | awk '{print $1}'"

EXTRACT_IPS_CORRECT = r"grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' access.log | sort -u"
EXTRACT_IPS_RG_CORRECT = r"rg -o '([0-9]{1,3}\.){3}[0-9]{1,3}' access.log | sort -u"
EXTRACT_IPS_BROKEN = r"grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' access.log | head -n 2"

INI_CORRECT = (
    "awk '/^\\[database\\]/{p=1} p && /^\\[/ && $0!=\"[database]\"{exit} p{print}' "
    "config.ini"
)
INI_BROKEN = "grep -A2 '^\\[database\\]' config.ini"

AWK_SUM_CORRECT = "awk -F, 'NR>1 {sum[$1]+=$3} END {for (k in sum) print k, sum[k]}' sales.csv"
AWK_SUM_BROKEN = "awk -F, 'NR>1 {sum[$1]+=$2} END {for (k in sum) print k, sum[k]}' sales.csv"
# sales.csv has a header row; a command that forgets to skip it folds a bogus
# "region" group (amount column sums to 0) into the output.
AWK_SUM_NOSKIP = "awk -F, '{sum[$1]+=$3} END {for (k in sum) print k, sum[k]}' sales.csv"
# "region: 15.00" style output — a trailing colon on the key must still parse.
AWK_SUM_COLON_CORRECT = (
    "awk -F, 'NR>1 {sum[$1]+=$3} END {for (k in sum) printf \"%s: %d\\n\", k, sum[k]}' sales.csv"
)

DIFF_SORTED_CORRECT = "diff <(sort allow.txt) <(sort deny.txt)"
DIFF_SORTED_BROKEN = "diff allow.txt deny.txt || true"
# comm(1) is explicitly allowed by the prompt; -3 prints file-1-only unindented
# and file-2-only tab-indented. -12 prints the intersection, which is wrong.
DIFF_SORTED_COMM_CORRECT = "comm -3 <(sort allow.txt) <(sort deny.txt)"
DIFF_SORTED_COMM_INTERSECTION = "comm -12 <(sort allow.txt) <(sort deny.txt)"

# rg with no path argument searches the current directory (stdin is /dev/null,
# not a pipe), the same as it would in the user's shell.
COUNT_LINES_RG_NOPATH = "rg --type py -l . | xargs wc -l | tail -n 1 | awk '{print $1}'"

CSV_COLUMNS_CORRECT = r"""awk -F, 'NR > 1 {print $2 "\t" $4}' data.csv | sort"""
CSV_COLUMNS_BROKEN = r"""awk -F, 'NR > 1 {print $2 "\t" $4}' data.csv"""

JSON_EXTRACT_CORRECT = "jq -r '.items[].name' response.json"
JSON_EXTRACT_BROKEN = "jq -r '.items[].id' response.json"

DELETE_NODE_MODULES_CORRECT = "rm -rf node_modules"
DELETE_NODE_MODULES_BROKEN = "rm -rf node_modules package.json"

LOOP_BACKUP_COPIES_CORRECT = 'for f in *.conf; do cp "$f" "$f.bak"; done'
LOOP_BACKUP_COPIES_BROKEN = 'for f in *; do cp "$f" "$f.bak"; done'

PARALLEL_COMPRESS_CORRECT = (
    "find . -maxdepth 1 -name '*.log' -type f -print0 | "
    "xargs -0 -P 4 -n 1 gzip -k"
)
PARALLEL_COMPRESS_BROKEN = "gzip -k api.log"

TAR_SAFE_EXTRACT_LOGS_CORRECT = (
    "mkdir -p logs && "
    "tar -xzf backup.tar.gz -C logs --strip-components=2 -k 'var/log/*.log'"
)
TAR_SAFE_EXTRACT_LOGS_BROKEN = (
    "mkdir -p logs && "
    "tar -xzf backup.tar.gz -C logs --strip-components=2 'var/log/*'"
)


def _has_working_yq() -> bool:
    yq = shutil.which("yq")
    if yq is None:
        return False
    return subprocess.run([yq, "--version"], capture_output=True).returncode == 0


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("env-missing-keys", ENV_CORRECT),
        ("log-window-uniq-ip", LOG_CORRECT),
        ("yaml-image-policy", YAML_CORRECT),
        ("k8s-old-tls-secrets", K8S_CORRECT),
        ("jsonl-p95-routes", JSONL_P95_CORRECT),
        ("csv-quoted-join", CSV_JOIN_CORRECT),
        ("find-hardlink-groups", HARDLINK_CORRECT),
        ("count-lines-code", COUNT_LINES_CORRECT),
        ("extract-ips", EXTRACT_IPS_CORRECT),
        ("ini-section", INI_CORRECT),
        ("awk-group-sum", AWK_SUM_CORRECT),
        ("diff-sorted", DIFF_SORTED_CORRECT),
        ("csv-columns-transform", CSV_COLUMNS_CORRECT),
        ("json-extract", JSON_EXTRACT_CORRECT),
        ("delete-node-modules", DELETE_NODE_MODULES_CORRECT),
        ("loop-backup-copies", LOOP_BACKUP_COPIES_CORRECT),
        ("parallel-compress", PARALLEL_COMPRESS_CORRECT),
        ("tar-safe-extract-logs", TAR_SAFE_EXTRACT_LOGS_CORRECT),
    ],
)
def test_correct_commands_pass(target: str, command: str):
    assert oracle_pass(target, command) is True


@pytest.mark.skipif(not _has_working_yq(), reason="yq is optional")
def test_yaml_yq_command_passes_when_available():
    assert oracle_pass("yaml-image-policy", YAML_YQ_CORRECT) is True


def test_count_lines_fd_command_passes_with_inherited_path():
    assert oracle_pass("count-lines-code", COUNT_LINES_FD_CORRECT) is True


def test_extract_ips_rg_command_passes_with_inherited_path():
    assert oracle_pass("extract-ips", EXTRACT_IPS_RG_CORRECT) is True


def test_k8s_field_selector_command_passes():
    assert oracle_pass("k8s-old-tls-secrets", K8S_FIELD_SELECTOR_CORRECT) is True


def test_hardlink_inode_header_command_passes():
    assert oracle_pass("find-hardlink-groups", HARDLINK_INODE_HEADER_CORRECT) is True


def test_awk_group_sum_requires_skipping_the_header():
    # sales.csv now carries a header row; summing without skipping it emits a
    # spurious "region" group and must fail.
    assert oracle_pass("awk-group-sum", AWK_SUM_NOSKIP) is False


def test_awk_group_sum_accepts_colon_formatted_keys():
    assert oracle_pass("awk-group-sum", AWK_SUM_COLON_CORRECT) is True


def test_awk_group_sum_nonnumeric_value_fails_cleanly():
    # A command whose last field is non-numeric (here the product letter) must
    # grade False, not raise out of the comparator.
    assert oracle_pass("awk-group-sum", "awk -F, 'NR>1 {print $1, $2}' sales.csv") is False


def test_diff_sorted_accepts_comm_output():
    assert oracle_pass("diff-sorted", DIFF_SORTED_COMM_CORRECT) is True


def test_diff_sorted_rejects_comm_intersection():
    assert oracle_pass("diff-sorted", DIFF_SORTED_COMM_INTERSECTION) is False


def test_count_lines_rg_no_path_searches_cwd():
    # Regression: without stdin=DEVNULL this hangs to the timeout instead of
    # searching the working directory.
    assert oracle_pass("count-lines-code", COUNT_LINES_RG_NOPATH) is True


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("env-missing-keys", ENV_BROKEN),
        ("log-window-uniq-ip", LOG_BROKEN),
        ("yaml-image-policy", YAML_BROKEN),
        ("k8s-old-tls-secrets", K8S_BROKEN),
        ("jsonl-p95-routes", JSONL_P95_BROKEN),
        ("csv-quoted-join", CSV_JOIN_BROKEN),
        ("find-hardlink-groups", HARDLINK_BROKEN),
        ("count-lines-code", COUNT_LINES_BROKEN),
        ("extract-ips", EXTRACT_IPS_BROKEN),
        ("ini-section", INI_BROKEN),
        ("awk-group-sum", AWK_SUM_BROKEN),
        ("diff-sorted", DIFF_SORTED_BROKEN),
        ("csv-columns-transform", CSV_COLUMNS_BROKEN),
        ("json-extract", JSON_EXTRACT_BROKEN),
        ("delete-node-modules", DELETE_NODE_MODULES_BROKEN),
        ("loop-backup-copies", LOOP_BACKUP_COPIES_BROKEN),
        ("parallel-compress", PARALLEL_COMPRESS_BROKEN),
        ("tar-safe-extract-logs", TAR_SAFE_EXTRACT_LOGS_BROKEN),
    ],
)
def test_broken_commands_fail(target: str, command: str):
    assert oracle_pass(target, command) is False
