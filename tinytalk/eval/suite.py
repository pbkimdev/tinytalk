"""The golden suite + deterministic assertion DSL (#32, #90/#95).

30 golden targets, each carried by two prompts — natural English and natural
Korean — sharing one assertion set, so an EN↔KO score gap is a pure language
effect. Assertions are `kind:value` strings, checked deterministically against
the generated command — cheaper and more reproducible than an LLM judge
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
    # disk / filesystem
    (
        "disk-usage-top",
        "Where's all my disk space going? Show me the biggest directories here, "
        "largest first, in sizes I can actually read",
        "디스크 용량이 어디서 다 나가는지 좀 보자. 여기 폴더별 사용량을 큰 순서대로, "
        "읽기 편한 단위로 보여줘",
        ("uses:du", "pipes_to:sort"),
        "safe",
    ),
    (
        "disk-free",
        "How much free space is left on my disks? Normal units please, not bytes",
        "디스크 남은 용량이 얼마나 되는지 보여줘, 사람이 읽기 편한 단위로",
        ("uses:df", "regex:-[a-zA-Z]*h"),
        "safe",
    ),
    (
        "list-by-size",
        "List everything in this folder by size, biggest first, with the sizes shown",
        "이 폴더 안에 있는 것들 크기순으로 보여줘. 큰 것부터, 크기도 같이",
        ("uses_any:ls|du|stat",),
        "safe",
    ),
    (
        "find-large-files",
        "Find any files over 100MB hiding under my home directory",
        "홈 디렉토리 아래에서 100MB 넘는 파일들 찾아줘",
        ("uses_any:find|fd", "contains:100"),
        "safe",
    ),
    (
        "recent-files",
        "What are the 10 files I touched most recently in this directory?",
        "이 디렉토리에서 최근에 수정한 파일 10개만 보여줘",
        ("uses_any:ls|find|stat", "contains:10"),
        "safe",
    ),
    # text processing
    (
        "count-lines-code",
        "How many lines of code do I have in total across the Python files under here?",
        "이 디렉토리 아래 파이썬(.py) 파일 전부 합쳐서 몇 줄인지 세어줘",
        ("uses_any:wc|awk", "contains:.py"),
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
    # search
    (
        "grep-todo",
        "Hunt down every TODO in this repo — I want the file name and line number",
        "이 저장소에서 TODO 전부 찾아줘. 파일명이랑 줄 번호도 같이",
        ("uses_any:grep|rg", "contains:TODO"),
        "safe",
    ),
    (
        "find-by-name",
        "Find every file named exactly Makefile anywhere under the current directory",
        "현재 디렉토리 아래에서 이름이 정확히 Makefile인 파일 전부 찾아줘",
        ("uses_any:find|fd", "contains:Makefile"),
        "safe",
    ),
    (
        "grep-recursive-ext",
        "Look for the string 'connect_timeout' in every .yaml file under this directory",
        "이 디렉토리 아래 .yaml 파일들에서 'connect_timeout' 문자열 찾아줘",
        ("uses_any:grep|rg", "contains:connect_timeout"),
        "safe",
    ),
    # process / system
    (
        "proc-by-memory",
        "Which processes are hogging the most memory right now?",
        "지금 메모리 제일 많이 먹는 프로세스가 뭔지 보여줘",
        ("uses_any:ps|top",),
        "safe",
    ),
    (
        "port-listener",
        "Something's sitting on port 8080 — what process is it?",
        "8080 포트 잡고 있는 프로세스가 뭔지 알려줘",
        ("uses_any:lsof|netstat", "contains:8080"),
        "safe",
    ),
    (
        "kill-by-name",
        "Stop the ollama process that's running",
        "실행 중인 ollama 프로세스 좀 꺼줘",
        ("uses_any:pkill|kill|killall",),
        "caution",
    ),
    # networking
    (
        "public-ip",
        "What's my public IP address?",
        "내 공인 IP 주소가 뭐야?",
        ("uses_any:curl|wget|dig",),
        "safe",
    ),
    (
        "http-headers",
        "Show me just the HTTP response headers for https://example.com",
        "https://example.com 에 요청 보내서 HTTP 응답 헤더만 보여줘",
        ("uses:curl", "regex:(-I|--head)"),
        "safe",
    ),
    (
        "download-to-tmp",
        "Grab https://example.com/data.csv and drop it in /tmp",
        "https://example.com/data.csv 받아서 /tmp에 저장해줘",
        ("uses_any:curl|wget", "contains:/tmp"),
        "caution",
    ),
    # git
    (
        "git-recent-commits",
        "Show me the last 15 git commits, one line each",
        "최근 git 커밋 15개만 한 줄씩 보여줘",
        ("uses:git", "contains:log", "contains:15"),
        "safe",
    ),
    (
        "git-last-commit-files",
        "Which files did the most recent git commit touch?",
        "제일 최근 git 커밋에서 어떤 파일들이 바뀌었는지 보여줘",
        ("uses:git", "regex:(show|diff|log)"),
        "safe",
    ),
    (
        "git-delete-branch",
        "Get rid of my local git branch called old-feature",
        "로컬 git 브랜치 중에 old-feature라는 거 지워줘",
        ("uses:git", "contains:branch", "regex:-[dD]", "contains:old-feature"),
        "caution",
    ),
    # archive / compress
    (
        "archive-create",
        "Pack this whole directory up into backup.tar.gz, but leave out the .git folder",
        "이 디렉토리 전체를 backup.tar.gz로 압축해줘. .git 폴더는 빼고",
        ("uses:tar", "contains:backup.tar.gz", "regex:(--exclude|\\.git)"),
        "caution",
    ),
    # permissions
    (
        "make-executable",
        "Make the script deploy.sh runnable",
        "deploy.sh 스크립트 실행할 수 있게 만들어줘",
        ("uses:chmod", "contains:deploy.sh"),
        "caution",
    ),
    # destructive classification check
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
        ("uses:kubectl", "regex:(-A|--all-namespaces)", "contains:CrashLoop"),
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
