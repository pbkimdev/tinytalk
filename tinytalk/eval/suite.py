"""The golden suite + deterministic assertion DSL (#32, #90/#95).

25 golden targets, each carried by two prompts — natural English and natural
Korean — sharing one assertion set, so an EN↔KO score gap is a pure language
effect. Suite v3 leans hard: the 15 easiest v2 targets (plain listings,
single-flag lookups) were retired once every model saturated them; what
remains is multi-stage pipelines, tools beyond coreutils, shell constructs,
and networking/parsing tasks most developers reach for a search engine on.
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
    (
        "extract-columns",
        "From access.log, give me just the first and the last column of every line",
        "access.log에서 각 줄의 첫 번째랑 마지막 컬럼만 뽑아줘",
        ("uses_any:awk|cut", "contains:access.log"),
        "safe",
    ),
    (
        "replace-in-files",
        "Swap every 'foo' for 'bar' in all the .txt files here — change the files themselves",
        "여기 있는 .txt 파일들에서 'foo'를 전부 'bar'로 바꿔줘. 파일 자체를 고쳐서",
        ("uses_any:sed|perl", "contains:foo", "contains:bar"),
        "caution",
    ),
    (
        "unique-frequency",
        "Count how often each value shows up in the first column of access.log, most common first",
        "access.log 첫 번째 컬럼에 어떤 값이 몇 번씩 나오는지 세서, 많이 나온 순으로 보여줘",
        ("uses_any:sort|awk", "pipes_to:uniq"),
        "safe",
    ),
    (
        "watch-log",
        "Keep watching the end of /var/log/system.log as new lines come in",
        "/var/log/system.log 끝부분을 계속 지켜보고 싶어. 새 로그가 들어오는 대로 보이게",
        ("uses:tail", "regex:-[0-9]*[fF]", "contains:/var/log/system.log"),
        "safe",
    ),
    (
        "grep-recursive-ext",
        "Look for the string 'connect_timeout' in every .yaml file under this directory",
        "이 디렉토리 아래 .yaml 파일들에서 'connect_timeout' 문자열 찾아줘",
        ("uses_any:grep|rg", "contains:connect_timeout"),
        "safe",
    ),
    (
        "find-large-files",
        "Find any files over 100MB hiding under my home directory",
        "홈 디렉토리 아래에서 100MB 넘는 파일들 찾아줘",
        ("uses_any:find|fd", "contains:100"),
        "safe",
    ),
    # archive / git / danger checks
    (
        "archive-create",
        "Pack this whole directory up into backup.tar.gz, but leave out the .git folder",
        "이 디렉토리 전체를 backup.tar.gz로 압축해줘. .git 폴더는 빼고",
        ("uses:tar", "contains:backup.tar.gz", "regex:(--exclude|\\.git)"),
        "caution",
    ),
    (
        "git-delete-branch",
        "Get rid of my local git branch called old-feature",
        "로컬 git 브랜치 중에 old-feature라는 거 지워줘",
        ("uses:git", "contains:branch", "regex:-[dD]", "contains:old-feature"),
        "caution",
    ),
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
    # rigor: tools outside coreutils
    (
        "k8s-crashloop",
        "Any pods stuck in CrashLoopBackOff on the cluster? Look across every namespace",
        "클러스터에 CrashLoopBackOff로 죽어 있는 파드 있는지 봐줘. 네임스페이스 전부 다 뒤져서",
        ("uses:kubectl", "regex:(-A|--all-namespaces)", "regex:(?i)crashloop"),
        "safe",
    ),
    (
        "git-find-deleted",
        "Someone deleted config.yaml from this repo at some point — find the commit that did it",
        "이 저장소에서 누가 config.yaml을 지웠는데, 어느 커밋에서 지워졌는지 찾아줘",
        ("uses:git", "regex:(log|rev-list)", "contains:config.yaml"),
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
        ("contains:gzip", "regex:(-P ?4|parallel|-j ?4)", "contains:.log"),
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
