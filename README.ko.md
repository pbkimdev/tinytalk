<div align="center">

# TinyTalk

[English](README.md) · **[한국어](README.ko.md)**

![말로 요청한 일을 검토 가능한 명령으로 바꾸는 TinyTalk](demo.gif)

**하고 싶은 일을 말하면 명령 하나를 돌려줍니다. 실행은 사용자가 결정합니다.**

TinyTalk은 자연어 요청을 지금 쓰는 컴퓨터에 맞는 셸 명령으로 바꿉니다. 명령을 검증하고
입력창에 넣은 뒤 멈춥니다. 읽고, 고치고, 실행하거나 버리면 됩니다.

</div>

```text
? 여기서 제일 큰 폴더 5개만 보여줘

du -sh ./* 2>/dev/null | sort -hr | head -n 5
          [safe] 현재 디렉터리에서 용량이 가장 큰 항목 5개를 보여줍니다.
```

TinyTalk은 일부러 terminal agent보다 작은 제품으로 만들었습니다.

- **제안만 하고 실행하지 않습니다.** 생성한 명령은 언제나 사용자에게 돌아옵니다.
- **내 컴퓨터를 기준으로 확인합니다.** 명령을 보여 주기 전에 셸 문법, 설치된 바이너리,
  확인할 수 있는 long flag와 일부 native dry-run을 검사합니다.
- **위험도를 눈에 보이게 다룹니다.** 위험한 명령은 주석 처리해서 넣습니다. 실행하려면 사용자가
  직접 주석을 지워야 합니다.
- **원하는 모델을 연결할 수 있습니다.** Claude·Codex 로그인, AWS Bedrock, Azure OpenAI,
  OpenAI-compatible API, 로컬 모델을 같은 방식으로 씁니다.

## 1분 안에 시작하기

### 1. 설치

```sh
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/pbkimdev/tinytalk/main/scripts/install.sh | sh
```

설치 스크립트는 macOS·Linux와 arm64·x86_64에 맞는 release를 고릅니다. release에 checksum이
있으면 검증하고, `tt`를 `~/.local` 아래에 설치합니다. 셸 rc 파일은 동의를 받은 뒤에만
수정하며, 기존 TinyTalk 설정은 덮어쓰지 않습니다.

대화형 터미널에서 설치하면 끝에 `tt setup`이 열립니다. 세 단계뿐입니다.

1. 명령 설명에 쓸 언어를 고릅니다. 해당 UI 번역이 있으면 이후 마법사도 그 언어로 바뀝니다.
2. 동의를 받아 zsh widget을 연결합니다.
3. 사용할 모델 provider를 연결합니다.

어느 단계든 건너뛸 수 있고, `tt setup`을 다시 실행하면 언제든 바꿀 수 있습니다.

> `?` 인터랙션은 zsh widget입니다. 일반 `tt "..."` 명령은 bash를 비롯한 다른 셸에서도
> 동작합니다.

### 2. 새 셸 열기

빈 입력창에서 `?`를 누릅니다. TinyTalk badge가 보이면 **prompt mode**입니다. 하고 싶은 일을
입력하고 Enter를 누르세요. 검증된 명령이 요청을 대신해 입력창에 들어옵니다. TinyTalk이 두 번째
Enter를 대신 누르지는 않습니다.

아직 widget을 연결하지 않았다면 CLI로 똑같이 확인할 수 있습니다.

```sh
tt "이 디렉터리 아래 100MB 넘는 파일 찾아줘"
```

### 3. 명령 확인하기

모든 결과에는 최종 위험도가 붙습니다.

| 등급 | TinyTalk의 처리 방식 |
|---|---|
| `safe` | 읽기 전용 명령을 검토할 수 있게 입력창에 넣습니다. |
| `caution` | 상태를 바꿀 수 있는 명령임을 분명히 표시합니다. |
| `destructive` | 명령을 주석 처리하고 경고합니다. 사용자가 직접 주석을 지워야 실행할 수 있습니다. |

TinyTalk도 틀릴 수 있습니다. 위험도와 검증 절차는 단순한 실수를 줄여 주지만, 생성된 셸 명령을
신뢰할 수 있는 코드로 바꾸지는 않습니다. 실행하기 전에 읽어 보세요.

## 모델 고르기

provider를 추가하거나 바꾸고 지울 때는 `tt auth`를 실행합니다. 마법사가 관리하는 **slot**은
`primary`와 선택 사항인 `fallback` 두 개입니다. primary가 실패하거나 검증을 통과하지 못한
명령을 만들면, TinyTalk은 실제 도구 정보로 grounding을 보강해 fallback에서 한 번 더 시도할 수
있습니다.

| Provider 경로 | 이런 경우에 적합합니다 | 인증 방식 |
|---|---|---|
| Claude Agent SDK | Claude Code를 이미 쓰고 있을 때 | 기존 `claude` 로그인 또는 `ANTHROPIC_API_KEY` |
| OpenAI Codex Agent SDK | Codex를 이미 쓰고 있을 때 | 로컬 Codex CLI 로그인 |
| AWS Bedrock | 조직에서 AWS를 사용할 때 | AWS 표준 credential chain 또는 named profile |
| OpenAI-compatible HTTP | 호스팅 API나 로컬 서버가 있을 때 | API key, 또는 keyless 로컬 서버 |
| Anthropic-compatible HTTP | Anthropic-compatible endpoint를 직접 호출할 때 | API key |
| Azure OpenAI | Azure에 deployment가 있을 때 | endpoint, API version, deployment name, API key |

마법사는 설정을 쓰기 전에 credential이나 모델 검색을 시험합니다. TinyTalk이 직접 받는 API
secret은 `config.toml`이 아니라 OS keyring에 저장합니다. Bedrock은 AWS credential chain을,
Agent SDK 경로는 각 CLI 로그인을 그대로 사용합니다.

### Claude

[Claude Code](https://docs.anthropic.com/en/docs/claude-code/getting-started)를 설치하고 로그인한 뒤,
`tt auth`에서 **Claude Agent SDK**를 고릅니다. TinyTalk release build는 이 provider를 처음
선택할 때 현재 버전에 맞는 Claude add-on을 내려받습니다.

```sh
claude
tt auth
```

### Codex

[Codex CLI](https://github.com/openai/codex)를 설치하고 ChatGPT로 로그인한 뒤,
**OpenAI Codex Agent SDK**를 고릅니다.

```sh
codex login
tt auth
```

### AWS Bedrock

먼저 평소 쓰는 AWS credential chain이 동작하는지 확인하고 **AWS Bedrock**을 고릅니다.
TinyTalk은 region과 선택 사항인 profile을 받은 뒤 사용할 수 있는 model ID를 찾습니다. 검색이
막힌 환경에서는 model ID를 직접 입력할 수도 있습니다. Bedrock 지원은 처음 설정할 때 내려받는
버전별 add-on입니다.

```sh
aws sso login --profile my-profile   # SSO profile을 쓴다면
tt auth
```

Bedrock의 모델 접근 권한과 inference profile은 모델·region마다 다릅니다. 마법사가 찾은 ID를
사용하고, 맨 model ID가 거부되면 AWS의 [model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)와
[inference profile](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html)
문서를 확인하세요.

### 로컬 모델

`tt auth`에서 **OpenAI-compatible HTTP API**를 고릅니다. 지원하는 Mac·Linux 환경에서는
TinyTalk이 Gemma와 서버를 자동으로 준비해 볼 수 있습니다. runtime이나 모델을 안전하게 준비할
수 없으면 기존 서버 URL을 받는 수동 설정으로 자연스럽게 넘어갑니다.

간단히 연결하기 좋은 서버는 다음 두 가지입니다.

- Apple Silicon에서는 [oMLX](https://github.com/jundot/omlx). 기본 주소는
  `http://localhost:8000/v1`입니다.
- macOS·Linux에서는 [llama.cpp](https://github.com/ggml-org/llama.cpp). 기본 주소는
  `http://localhost:8080/v1`입니다.

이미 준비한 `llama-server`라면 세 단계로 연결할 수 있습니다.

```sh
llama-server -hf <owner>/<gguf-repo>:<quant> --port 8080
curl -s http://localhost:8080/v1/models
tt auth
```

마법사에서 기존 서버를 직접 연결하는 경로를 고르고 `http://localhost:8080/v1`을 입력합니다.
keyless 서버라면 API key는 비워 두고, `/v1/models`가 알려 준 모델을 선택하세요.

## 평소에 쓰는 명령

### Prompt mode

- 빈 입력창에서 `?`를 누르면 prompt mode로 들어갑니다.
- 빈 prompt에서 `?`나 Backspace를 누르면 나옵니다.
- Enter를 누르면 요청을 보내고, 같은 입력창으로 명령을 돌려받습니다.
- prompt mode에서 위·아래 방향키로 지난 TinyTalk 명령을 불러옵니다.

### CLI

```sh
tt "열려 있는 포트와 프로세스 보여줘"       # 명령 하나 생성
tt --json "가장 최근 파일 5개 보여줘"       # 구조화된 출력
tt history                                    # 지난 결과 탐색
tt ground                                     # grounding snapshot 확인
tt ground --refresh                           # 지금 다시 만들기
tt prompt "이름이 같은 파일 찾아줘"         # 모델 prompt 출력, 모델 호출 없음
tt config explanation off                     # 한 줄 설명 숨기기
tt setup                                      # 언어, widget, provider 다시 설정
tt auth                                       # primary와 fallback 관리
tt upgrade                                    # 최신 release 설치
tt uninstall                                  # 앱 데이터와 keyring 항목 제거
```

`tt history`는 터미널에서 `fzf`를 찾으면 picker를 열고, 아니면 평문 목록을 출력합니다. 기록은
`XDG_STATE_HOME` 아래 날짜별 JSONL로 저장하며, 문제를 진단할 수 있도록 실패한 요청도 남깁니다.

### 모델에 전달하는 정보

TinyTalk은 사용자의 요청, 현재 작업 디렉터리, OS·셸·설치된 명령·캐시한 도구 버전으로 만든
grounding 요약을 모델에 보냅니다. `TT_SESSION_CONTEXT`가 설정되어 있으면 해당 텍스트를
redact한 뒤 session context로 추가합니다. 제품 요청 경로에서는 임의의 파일을 읽거나, 정보를
모으려고 생성 명령을 실행하지 않습니다.

모델을 호출하지 않고 실제 prompt를 확인할 수 있습니다.

```sh
tt prompt "가장 큰 로그 파일 찾아줘"
```

## 검증 방식

모델의 응답은 사용자에게 돌아오기 전에 다음 단계를 통과해야 합니다.

1. `zsh -n` 또는 사용 가능한 POSIX shell로 명령을 **parse**합니다.
2. 명령 위치에 나온 **바이너리**가 현재 컴퓨터에 있는지 확인합니다.
3. 실제 help text가 있을 때 **long flag**를 확인합니다. 문서가 없다는 이유만으로 거부하지는
   않습니다.
4. 일부 단일 `rsync`, `git`, `npm`, `kubectl` 작업은 도구가 제공하는 **native dry-run**으로
   확인합니다.
5. 명령과 redirect를 해석해 **위험도**를 분류합니다. 최종 등급은 모델이 말한 것보다 안전한
   쪽으로 낮아질 수 없습니다.

명령이 탈락하면 TinyTalk은 검증 문제와 관련 도구 help를 넣어 한 번 더 시도합니다. fallback이
있다면 두 번째 tier에서 사용합니다. 끝까지 통과한 제안이 없으면 오류를 돌려주고 셸 입력창은
건드리지 않습니다.

## 설정

기본 설정 파일은 `~/.config/tinytalk/config.toml`입니다. `TT_CONFIG`나 `--config PATH`로
바꿀 수 있습니다. provider는 `tt auth`로 설정하는 편이 안전하지만, 파일 자체는 평범한 TOML입니다.

```toml
[defaults]
backend = "primary"
escalation_backend = "fallback"  # 선택
posture = "hybrid"               # local | hybrid | cloud
language = "ko"
explanation = true

[backends.primary]
kind = "openai-compat"
base_url = "http://localhost:11434/v1"
model = "your-model-id"

[backends.fallback]
kind = "claude-agent-sdk"
model = "your-claude-model-id"
effort = "low"

[cache]
enabled = true
```

지원하는 backend kind는 `openai-compat`, `anthropic-compat`, `claude-agent-sdk`,
`codex-agent-sdk`, `bedrock`, `azure-openai`입니다. `primary`, `fallback` 같은 이름은 사용자가
정하는 config alias이고, 실제 protocol은 `kind`가 고릅니다.

| 환경 변수 | 용도 |
|---|---|
| `TT_CONFIG` | 다른 config 파일을 사용합니다. |
| `TT_SESSION_CONTEXT` | 호출자가 제공한 session context를 redact해 추가합니다. |
| `XDG_CONFIG_HOME` | config root를 바꿉니다. |
| `XDG_CACHE_HOME` | grounding과 exact-match cache root를 바꿉니다. |
| `XDG_STATE_HOME` | history root를 바꿉니다. |

## 설치, 업데이트, 삭제

재현 가능한 설치가 필요하면 release를 고정합니다.

```sh
TT_VERSION=v0.2.0rc9 curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/pbkimdev/tinytalk/main/scripts/install.sh | sh
```

설치 옵션은 `--yes`, `--no-rc`, `--bin-dir DIR`, `--version TAG`입니다. flag를 pipe로 넘길
때는 `sh -s -- --version TAG` 형태가 필요하므로, 위의 `TT_VERSION` 방식이 더 간단합니다.

```sh
tt upgrade
tt uninstall                    # shell rc block은 직접 지우도록 남겨 둡니다

# binary, data, keyring 항목, installer가 만든 rc block까지 제거:
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/pbkimdev/tinytalk/main/scripts/uninstall.sh | sh
```

Release build는 Bedrock이나 Claude를 선택했을 때만 해당 add-on을 받습니다. checksum을 확인한
버전별 add-on은 `${XDG_DATA_HOME:-~/.local/share}/tinytalk/addons/`에 저장합니다. Air-gapped
환경에서는 다른 컴퓨터에서 같은 release의 archive와 `.sha256` 파일을 받은 뒤 이 디렉터리에
풀 수 있습니다.

소스에서 실행하려면 다음과 같이 설치합니다.

```sh
git clone https://github.com/pbkimdev/tinytalk.git
cd tinytalk
uv tool install .
```

## Benchmark

TinyTalk suite는 backend마다 같은 25개 작업을 자연스러운 영어와 한국어로 요청합니다. 서로 다른
두 질문을 채점합니다.

- **Strict pass:** 응답이 TinyTalk contract를 지키고, 명령이 parse되며, 설치된 바이너리를 쓰고,
  정해진 command-shape assertion을 만족하는가?
- **Execution oracle:** fixture가 준비된 18개 target에서, 격리된 eval sandbox 안의 출력이나
  파일 상태가 정답과 같은가?

가장 최근에 커밋된 field run은 **2026-07-05 suite v4**입니다. Sonnet 5는 strict pass 92%,
oracle이 적용된 결과에서 81%를 기록했습니다. 로컬 Gemma 4 세 가지는 strict pass 58–68%,
oracle 적용 결과 44–56%였습니다. 중요한 결과는 그 차이입니다. 그럴듯해 보이는 명령도 fixture에
실제로 적용하면 실패할 수 있습니다.

[Interactive report](docs/bench/2026-07-05/index.html),
[analysis dashboard](docs/bench/2026-07-05/dashboard.html), [suite contract](docs/bench/SUITE-V4.md),
[재현 runbook](docs/bench/RUNBOOK.md)을 참고하세요. 명령 실행은 명시적인 eval harness의 격리된
환경에서만 일어납니다. TinyTalk 제품 경로는 여전히 생성 명령을 실행하지 않습니다.

## 문제 해결

- **`?`가 문자로 입력됩니다:** `tt setup`을 실행하고 새 zsh를 여세요. `tt`가 `PATH`에 있는지,
  `eval "$(tt init zsh)"`가 오류 없이 로드되는지 확인합니다.
- **Config나 backend가 없습니다:** `tt setup` 또는 `tt auth`를 실행하세요. starter config에는
  가짜 provider를 일부러 넣지 않습니다.
- **로컬 서버에서 모델이 보이지 않습니다:** `tt auth`를 다시 실행하기 전에
  `curl -s <base-url>/models`를 확인하세요.
- **Bedrock 검색이 실패합니다:** AWS 로그인을 갱신하고 region/profile을 확인하거나, 마법사의
  수동 model-ID 경로를 사용하세요.
- **명령이 계속 거부됩니다:** 도구를 설치하거나 업데이트했다면 `tt ground --refresh`를
  실행하세요. `tt prompt "..."`로 prompt를, `tt history`로 기록된 결과를 확인할 수 있습니다.
- **설명이 거슬립니다:** `tt config explanation off`를 사용하세요. 위험도 표시는 남습니다.

## 기여하기

TinyTalk은 `uv`로 관리하는 Python 3.11+ 프로젝트입니다.

```sh
uv sync
uv run pytest
uv run ruff check .
uv run tt --help
```

작업을 시작하기 전에 [AGENTS.md](AGENTS.md)를 읽어 주세요. 모든 변경은 GitHub issue와 승인된
계획에서 시작합니다. 명시적인 격리 eval harness 밖에서는 contributor tooling도 생성 명령을
자동 실행하지 않습니다.
