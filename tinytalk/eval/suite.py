"""The golden suite + deterministic assertion DSL (#32, #90/#95).

25 golden targets, each carried by two prompts — natural English and natural
Korean — sharing one assertion set, so an EN↔KO score gap is a pure language
effect. Suite v4 keeps the 25-target shape but retires the 10 most trivially
saturated v3 targets under the current scorer, preserving known discriminators
plus a few anchors. The 10 replacements concentrate on structured parsing,
escape-heavy commands, multi-stage composition, less-common shell tools, and
quoting/whitespace edge cases so frontier low-effort models have real headroom.
Assertions are `kind:value` strings, checked deterministically against the
generated command — cheaper and more reproducible than an LLM judge
(deferred post-v1):

- `uses:<tool>`        — tool appears in command position (not a substring hit)
- `uses_any:a|b|c`     — any of the tools appears in command position
- `pipes_to:<tool>`    — tool appears in command position after the first stage
- `contains:<text>`    — literal substring
- `not_contains:<text>`— literal substring absent
- `regex:<pattern>`    — `re.search` match
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tinytalk.validate import command_words


@dataclass(frozen=True)
class EvalPrompt:
    id: str
    text: str
    assertions: tuple[str, ...]
    expected_danger: str = "safe"
    lang: str = "en"
    target: str = ""


def check_assertion(assertion: str, command: str) -> bool:
    kind, _, value = assertion.partition(":")
    if kind == "uses":
        return value in command_words(command)
    if kind == "uses_any":
        words = command_words(command)
        return any(tool in words for tool in value.split("|"))
    if kind == "pipes_to":
        return value in command_words(command)[1:]
    if kind == "contains":
        return value in command
    if kind == "not_contains":
        return value not in command
    if kind == "regex":
        return re.search(value, command) is not None
    raise ValueError(f"unknown assertion kind: {kind!r}")


# (target, en_text, ko_text, assertions, expected_danger)
_TARGETS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    # text processing
    (
        "count-lines-code",
        "How many lines of code do I have in total across the Python files under here?",
        "이 디렉토리 아래 파이썬(.py) 파일 전부 합쳐서 몇 줄인지 세어줘",
        # Python-file selection, any correct spelling: `.py` glob/--include, `fd -e py`, `rg --type py`.
        ("uses_any:wc|awk", r"regex:\.py\b|\bpy\b"),
        "safe",
    ),
    # danger anchor
    (
        "delete-node-modules",
        "Completely wipe the node_modules folder in this directory",
        "여기 있는 node_modules 폴더 통째로 지워버려",
        ("uses_any:rm|trash", "contains:node_modules"),
        "destructive",
    ),
    # rigor: multi-stage pipelines
    (
        "log-top-errors",
        "Which URLs in access.log keep failing with 500s? Count them up and show me "
        "the 5 worst offenders",
        "access.log에서 500 에러가 제일 많이 나는 URL이 뭔지 궁금해. 몇 번씩 나는지 세서 "
        "최악인 5개만 보여줘",
        ("contains:access.log", "contains:500", "pipes_to:sort", "regex:(uniq -c|awk)"),
        "safe",
    ),
    (
        "csv-columns-transform",
        "Take data.csv, lose the header row, keep only the 2nd and 4th columns, "
        "turn the commas into tabs, and sort the result",
        "data.csv에서 첫 줄 헤더는 빼고, 2번째랑 4번째 컬럼만 뽑아서 쉼표를 탭으로 바꾼 "
        "다음 정렬해줘",
        (
            "contains:data.csv",
            "uses_any:cut|awk",
            "pipes_to:sort",
            "regex:(tail -n \\+2|1d|NR ?> ?1)",
        ),
        "safe",
    ),
    # rigor: shell constructs (a per-file action that doesn't vectorize forces a loop)
    (
        "loop-backup-copies",
        "Go through every .conf file in this directory and make a backup copy of each "
        "one — same name with .bak stuck on the end",
        "이 디렉토리에 있는 .conf 파일 하나하나마다 백업 복사본 만들어줘. 이름 뒤에 .bak만 붙여서",
        ("regex:\\bfor\\b|\\bwhile\\b", "contains:cp", "contains:.bak", "contains:.conf"),
        "caution",
    ),
    # v3 hard: networking / TLS / DNS
    (
        "cert-expiry",
        "When does the TLS certificate on example.com expire? Check it from right here "
        "in the terminal",
        "example.com의 TLS 인증서가 언제 만료되는지 터미널에서 바로 확인해줘",
        ("contains:example.com", "regex:(s_client|x509|enddate|curl -[a-zA-Z]*v)"),
        "caution",
    ),
    (
        "dns-trace",
        "Trace the whole DNS delegation path for example.com, starting from the root servers",
        "example.com의 DNS 위임 경로를 루트 서버에서부터 쭉 추적해줘",
        ("uses:dig", "contains:+trace", "contains:example.com"),
        "safe",
    ),
    (
        "ssh-stream-copy",
        "Send the ./data directory to the host web01 over ssh — without making an "
        "archive file on disk first",
        "./data 디렉토리를 압축 파일로 따로 만들지 말고 ssh로 web01 서버에 바로 보내줘",
        ("regex:(tar[^|]*\\| *ssh|rsync|scp -r)", "contains:web01", "contains:data"),
        "caution",
    ),
    # v3 hard: kubernetes
    (
        "k8s-restart-count",
        "Across all namespaces, which pods have restarted more than 5 times?",
        "네임스페이스 전체에서 5번 넘게 재시작한 파드가 뭐가 있는지 보여줘",
        ("uses:kubectl", "regex:(-A|--all-namespaces)", "regex:(?i)restart", "contains:5"),
        "safe",
    ),
    # v3 hard: structured parsing
    (
        "json-extract",
        "response.json has an items array — print each item's name, one per line",
        "response.json 안에 items 배열이 있는데, 각 item의 name만 한 줄에 하나씩 출력해줘",
        ("contains:response.json", "regex:(jq|python)", "regex:items", "regex:name"),
        "safe",
    ),
    (
        "extract-ips",
        "Pull every unique IPv4 address out of access.log",
        "access.log에 나오는 IPv4 주소들을 중복 없이 전부 뽑아줘",
        (
            "contains:access.log",
            "regex:(?i)(grep|rg|sed|awk|perl)",
            "regex:(sort -u|uniq)",
            "regex:(\\{1,3\\}|\\\\d)",
        ),
        "safe",
    ),
    (
        "ini-section",
        "From config.ini, print just the [database] section — from that header down to "
        "the next section header",
        "config.ini에서 [database] 섹션만 출력해줘. 그 헤더부터 다음 섹션 헤더 전까지만",
        ("uses_any:sed|awk", "contains:config.ini", "contains:database"),
        "safe",
    ),
    (
        "awk-group-sum",
        "In sales.csv, total up the 3rd column separately for each distinct value in "
        "the 1st column",
        "sales.csv에서 1번째 컬럼 값별로 묶어서 3번째 컬럼의 합계를 각각 구해줘",
        ("uses:awk", "contains:sales.csv", "regex:END"),
        "safe",
    ),
    # v3 hard: shell idioms
    (
        "diff-sorted",
        "Compare allow.txt and deny.txt as sorted sets — and don't write any temp files",
        "allow.txt랑 deny.txt를 정렬해서 비교해줘. 임시 파일은 만들지 말고",
        ("uses_any:diff|comm", "regex:<\\(", "contains:allow.txt", "contains:deny.txt"),
        "safe",
    ),
    (
        "parallel-compress",
        "Gzip all the .log files in this directory, running 4 compressions at a time "
        "in parallel",
        "이 디렉토리의 .log 파일들을 gzip으로 압축해줘. 한 번에 4개씩 병렬로 돌려서",
        ("contains:gzip", "regex:(-P ?4|parallel|-j ?4)", r"regex:(\.log\b|\blog\b)"),
        "caution",
    ),
    # v4 hard: structured data, quoting, policy checks, and richer shell composition
    (
        "jsonl-p95-routes",
        "From requests.jsonl, compute the p95 latency_ms per route for 5xx responses and show the top 3 slowest routes",
        "requests.jsonl에서 5xx 응답만 골라 route별 latency_ms p95를 계산하고 가장 느린 3개 route를 보여줘",
        (
            "contains:requests.jsonl",
            "uses_any:jq|python|awk",
            "regex:(latency_ms|duration_ms)",
            "regex:(route|path)",
            "regex:(p95|95|0\\.95|percentile)",
            "regex:(5[0-9][0-9]|5xx)",
            "regex:(head|-n ?3|top|\\[0:3\\])",
        ),
        "safe",
    ),
    (
        "csv-quoted-join",
        "Join orders.csv to customers.csv by customer_id and sum amount by customer email; the CSVs may contain quoted commas",
        "orders.csv와 customers.csv를 customer_id로 조인해서 customer email별 amount 합계를 구해줘. CSV 안에는 따옴표로 감싼 쉼표가 있을 수 있어",
        (
            "contains:orders.csv",
            "contains:customers.csv",
            "regex:(python3?|mlr|xsv|csvsql|csvjoin)",
            "regex:(customer_id|email)",
            "regex:(amount|sum|total)",
        ),
        "safe",
    ),
    (
        "yaml-image-policy",
        "Across Kubernetes YAML files, list container images whose tag is latest or missing; skip charts/ and vendor/",
        "Kubernetes YAML 파일들에서 태그가 latest이거나 아예 없는 container image를 찾아줘. charts/랑 vendor/는 빼고",
        (
            "regex:(image|containers)",
            "regex:(latest|tag)",
            "regex:(charts|vendor)",
            "regex:(python3?|yq)",
            "regex:(-g|--glob|--exclude|-prune|continue)",
        ),
        "safe",
    ),
    (
        "env-missing-keys",
        "Compare .env.example with .env and print each missing required key with its .env.example line number, preserving order and ignoring comments and blanks",
        ".env.example과 .env를 비교해서 로컬에 빠진 필수 키와 .env.example의 줄 번호를 같이 보여줘. 순서는 유지하고 주석과 빈 줄은 무시해",
        (
            "contains:.env.example",
            "contains:.env",
            "uses_any:awk|python|grep",
            "regex:(set\\(|-F|seen|while)",
            "regex:(#|^\\^#|blank|NF)",
            "regex:(grep -n|nl |enumerate)",
            "not_contains:sort",
        ),
        "safe",
    ),
    (
        "git-conflict-markers",
        "For files currently conflicted in git, count how many conflict marker blocks each file contains",
        "git에서 현재 충돌 난 파일마다 conflict marker 블록이 몇 개인지 세어줘",
        (
            "uses:git",
            "regex:(diff|ls-files)",
            "regex:(<<<<<<<|>>>>>>>)",
            "regex:(grep|rg|awk|python3?)",
        ),
        "safe",
    ),
    (
        "rsync-delete-dry-run",
        "Preview which remote files would be deleted when syncing ./site to web01:/srv/site; do not change anything",
        "./site를 web01:/srv/site로 동기화하면 원격에서 지워질 파일이 뭔지 미리 보여줘. 실제 변경은 하지 말고",
        (
            "uses:rsync",
            "contains:site",
            "contains:web01",
            "regex:(--delete|--del)",
            "regex:(--dry-run| -[A-Za-z]*n[A-Za-z]*\\b)",
            "regex:(deleting|grep|awk|sed)",
        ),
        "caution",
    ),
    (
        "k8s-old-tls-secrets",
        "Across all namespaces, list TLS secrets older than 90 days with namespace, name, and creation timestamp",
        "전체 네임스페이스에서 90일 넘은 TLS secret을 namespace, name, creation timestamp와 함께 보여줘",
        (
            "uses:kubectl",
            "regex:(-A|--all-namespaces)",
            "regex:(secret|secrets)",
            "regex:(kubernetes.io/tls|tls)",
            "contains:90",
            "regex:(jq|awk|python|jsonpath)",
        ),
        "safe",
    ),
    (
        "log-window-uniq-ip",
        "In access.log, count unique IPs per minute from 14:00 through 14:15, excluding 2xx responses",
        "access.log에서 14:00부터 14:15까지 분 단위 unique IP 수를 세어줘. 2xx 응답은 제외하고",
        (
            "contains:access.log",
            "uses_any:awk|python|perl",
            "regex:(14:00|14:15|14)",
            "regex:(2[0-9][0-9]|200|2xx|\\^2)",
            "regex:(sort -u|uniq|set\\(|seen\\[)",
        ),
        "safe",
    ),
    (
        "find-hardlink-groups",
        "Find files under this tree that are hardlinks to the same inode and print each inode group together",
        "이 트리 아래에서 같은 inode를 공유하는 hardlink 파일들을 찾아 inode 그룹별로 묶어서 보여줘",
        (
            "uses_any:find|fd",
            "regex:(-links|stat|inode|%i|inum)",
            "uses_any:awk|sort|uniq|python",
            "regex:(group|\\$1|inode|%i)",
        ),
        "safe",
    ),
    (
        "tar-safe-extract-logs",
        "Extract only var/log/*.log from backup.tar.gz into ./logs, stripping the first two path components and not overwriting existing files",
        "backup.tar.gz에서 var/log/*.log만 ./logs로 풀어줘. 앞의 경로 두 단계는 제거하고 기존 파일은 덮어쓰지 말고",
        (
            "uses:tar",
            "contains:backup.tar.gz",
            "contains:logs",
            "regex:(var/log|\\.log)",
            "regex:(--strip-components|--transform| -s)",
            "regex:(--keep-old-files|--skip-old-files|-[A-Za-z]*k[A-Za-z]*)",
        ),
        "caution",
    ),
)

SUITE: tuple[EvalPrompt, ...] = tuple(
    EvalPrompt(
        f"{target}-{lang}",
        text,
        assertions,
        expected_danger,
        lang=lang,
        target=target,
    )
    for (target, en_text, ko_text, assertions, expected_danger) in _TARGETS
    for lang, text in (("en", en_text), ("ko", ko_text))
)
