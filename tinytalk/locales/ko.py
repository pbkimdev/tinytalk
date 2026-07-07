"""Korean (ko) catalog — the first translation shipped for #74.

Keys are the exact English source strings. Prompts and errors use 존댓말; flag names,
env vars, commands, config keys, and provider/product names stay untranslated.
"""

MESSAGES = {
    # --- tt --help (main parser) -----------------------------------------------------
    "Turn plain English at the shell into a real, validated command.": (
        "셸에서 쓴 일상 언어를 실제로 검증된 명령어로 바꿔 줍니다."
    ),
    "commands:\n"
    "  auth        interactively set up a provider backend\n"
    "  config      change a setting in config.toml (e.g. `tt config explanation off`)\n"
    "  eval        benchmark configured backends (see `tt eval publish` for the docs page)\n"
    "  ground      inspect or rebuild the system grounding cache\n"
    "  history     browse and reuse past commands\n"
    '  init zsh    print the zsh integration script (eval "$(tt init zsh)")\n'
    "  prompt      print the assembled model prompt for a request (no model call)\n"
    "  setup       interactively configure TinyTalk step by step\n"
    "  upgrade     download and install the latest tt release\n"
    "  uninstall   remove tt files and keyring entries\n"
    "\n"
    "run `tt <command> --help` for command options": (
        "commands:\n"
        "  auth        프로바이더 백엔드를 대화형으로 설정합니다\n"
        "  config      config.toml의 설정을 변경합니다 (예: `tt config explanation off`)\n"
        "  eval        설정된 백엔드를 벤치마크합니다 (문서 페이지는 `tt eval publish` 참고)\n"
        "  ground      시스템 그라운딩 캐시를 확인하거나 다시 만듭니다\n"
        "  history     과거 명령어를 둘러보고 다시 사용합니다\n"
        '  init zsh    zsh 통합 스크립트를 출력합니다 (eval "$(tt init zsh)")\n'
        "  prompt      요청에 대해 조립된 모델 프롬프트를 출력합니다 (모델 호출 없음)\n"
        "  setup       TinyTalk를 단계별로 대화형 설정합니다\n"
        "  upgrade     최신 tt 릴리스를 내려받아 설치합니다\n"
        "  uninstall   tt 파일과 키링 항목을 제거합니다\n"
        "\n"
        "명령별 옵션은 `tt <command> --help`로 확인하세요"
    ),
    "config file (default: ~/.config/tinytalk)": "설정 파일 (기본값: ~/.config/tinytalk)",
    "backend from config (default: defaults.backend)": (
        "설정에서 사용할 백엔드 (기본값: defaults.backend)"
    ),
    "emit the full suggestion as JSON": "전체 제안을 JSON으로 출력합니다",
    "emit shell-evalable tt_* assignments (used by the zsh widget)": (
        "셸에서 eval 가능한 tt_* 할당문을 출력합니다 (zsh 위젯이 사용)"
    ),
    "what you want to do, in plain English": "하고 싶은 일을 일상 언어로 적어 주세요",
    # --- tt eval --help ---------------------------------------------------------------
    "Benchmark configured backends over the built-in prompt suite.": (
        "내장 프롬프트 스위트로 설정된 백엔드를 벤치마크합니다."
    ),
    "backends to score (default: all)": "점수를 매길 백엔드 (기본값: 전체)",
    "run a subset of the suite (full ids, or bare targets to get every language)": (
        "스위트의 일부만 실행합니다 (전체 id, 또는 모든 언어를 포함하려면 대상 이름만)"
    ),
    "write results to a .json or .csv file": "결과를 .json 또는 .csv 파일로 저장합니다",
    "write a self-contained HTML report of the results": "결과를 단일 HTML 리포트로 저장합니다",
    "re-render --report from a previous --export .json instead of running": (
        "실행하지 않고 이전 --export .json에서 --report를 다시 렌더링합니다"
    ),
    "eval-only: include read-only fixture file previews in scored prompts": (
        "eval 전용: 채점되는 프롬프트에 읽기 전용 픽스처 파일 미리보기를 포함합니다"
    ),
    # --- tt auth --help ---------------------------------------------------------------
    "Interactively set up a provider backend (PRD-provider-setup.md).": (
        "프로바이더 백엔드를 대화형으로 설정합니다 (PRD-provider-setup.md)."
    ),
    # --- tt setup --help ---------------------------------------------------------------
    "Interactively configure TinyTalk step by step.": "TinyTalk를 단계별로 대화형 설정합니다.",
    "print manual setup hints": "수동 설정 힌트를 출력합니다",
    # --- cli diagnostics ---------------------------------------------------------------
    "tt: --report-from requires --report PATH": "tt: --report-from은 --report PATH가 필요합니다",
    "tt auth: cancelled": "tt auth: 취소되었습니다",
    "tt: the written config failed validation: {error}": (
        "tt: 저장된 설정이 검증에 실패했습니다: {error}"
    ),
    "tt: backend {name!r} saved to {path}": "tt: 백엔드 {name!r}을(를) {path}에 저장했습니다",
    "tt: backend {name!r} removed from {path}": "tt: 백엔드 {name!r}을(를) {path}에서 제거했습니다",
    "default backend: {name}": "기본 백엔드: {name}",
    "; fallback: {name}": "; 폴백: {name}",
    'Try it: tt "show me disk usage"': '한번 써 보세요: tt "디스크 사용량 보여줘"',
    "tt: upgraded to {version}": "tt: {version}(으)로 업그레이드했습니다",
    "Remove TinyTalk installed files and keyring entries?": (
        "TinyTalk이 설치한 파일과 키링 항목을 제거할까요?"
    ),
    "tt uninstall: cancelled": "tt uninstall: 취소되었습니다",
    "tt: no history yet": "tt: 아직 히스토리가 없습니다",
    "backend {backend!r} failed: {error}": "백엔드 {backend!r}이(가) 실패했습니다: {error}",
    "backend failed: {error}": "백엔드가 실패했습니다: {error}",
    "no valid command: {error}": "유효한 명령어가 없습니다: {error}",
    # --- tt auth wizard ----------------------------------------------------------------
    "OpenAI-compatible HTTP API (OpenAI itself, Ollama, llama.cpp, ...)": (
        "OpenAI 호환 HTTP API (OpenAI 자체 API, Ollama, llama.cpp 등)"
    ),
    "Anthropic Messages API (raw HTTP, not the Agent SDK)": (
        "Anthropic Messages API (Agent SDK가 아닌 raw HTTP)"
    ),
    "Claude Agent SDK (Claude Code login or ANTHROPIC_API_KEY)": (
        "Claude Agent SDK (Claude Code 로그인 또는 ANTHROPIC_API_KEY)"
    ),
    "OpenAI Codex Agent SDK (local codex CLI login)": (
        "OpenAI Codex Agent SDK (로컬 codex CLI 로그인)"
    ),
    "AWS Bedrock (uses your AWS credentials)": "AWS Bedrock (사용자의 AWS 자격 증명 사용)",
    "Azure OpenAI (endpoint + API key)": "Azure OpenAI (엔드포인트 + API 키)",
    "Which backend do you want to set up?": "어떤 백엔드를 설정할까요?",
    "{slot} — (not set)": "{slot} — (설정 안 됨)",
    "remove fallback": "폴백 제거",
    "Writing a new {slot} will replace the existing one ({current}). Continue?": (
        "새 {slot}을(를) 쓰면 기존 항목({current})을 덮어씁니다. 계속할까요?"
    ),
    "Provider kind:": "프로바이더 종류:",
    'Explanation language (code or name, e.g. "en", "ko"):': (
        '설명 언어 (코드 또는 이름, 예: "en", "ko"):'
    ),
    "  (API key/credentials → OS keychain, not the file)": (
        "  (API 키/자격 증명은 파일이 아닌 OS 키체인에 저장됩니다)"
    ),
    "Write this to {path}?": "이 내용을 {path}에 저장할까요?",
    "Remove the fallback ({what})?": "폴백({what})을 제거할까요?",
    "config entry only": "설정 항목만 있음",
    "The credential test failed.": "자격 증명 테스트에 실패했습니다.",
    "Re-enter and try again": "다시 입력하고 재시도",
    "Abort setup": "설정 중단",
    "Connect an OpenAI-compatible server:": "OpenAI 호환 서버 연결:",
    "Set up local Gemma + server for me (recommended)": "로컬 Gemma와 서버를 자동으로 설정 (권장)",
    "I already have a server — enter its base URL": "이미 서버가 있음 — base URL 직접 입력",
    "tt auth: managed local setup failed ({error}) — falling back to manual setup.": (
        "tt auth: 자동 로컬 설정에 실패했습니다 ({error}) — 수동 설정으로 전환합니다."
    ),
    "Base URL:": "Base URL:",
    "API key (leave blank for a keyless local server):": (
        "API 키 (키 없는 로컬 서버라면 비워 두세요):"
    ),
    "tt auth: credential test against {base_url} failed: {error}": (
        "tt auth: {base_url} 자격 증명 테스트에 실패했습니다: {error}"
    ),
    "API key:": "API 키:",
    "Auth follows the Claude Agent SDK's own convention: an existing `claude` CLI login, "
    "or ANTHROPIC_API_KEY set in your environment. tt manages no secret here.": (
        "인증은 Claude Agent SDK 자체 방식을 따릅니다: 기존 `claude` CLI 로그인 또는 환경 변수 "
        "ANTHROPIC_API_KEY를 사용합니다. tt는 여기서 어떤 비밀 값도 저장하지 않습니다."
    ),
    "tt auth: Claude Agent SDK test call succeeded.": (
        "tt auth: Claude Agent SDK 테스트 호출에 성공했습니다."
    ),
    "tt auth: Claude Agent SDK test call failed: {error}": (
        "tt auth: Claude Agent SDK 테스트 호출에 실패했습니다: {error}"
    ),
    "(log in with `claude` in another terminal, or export ANTHROPIC_API_KEY, then retry)": (
        "(다른 터미널에서 `claude`로 로그인하거나 ANTHROPIC_API_KEY를 export한 뒤 재시도하세요)"
    ),
    "Already logged in via the Codex CLI?": "이미 Codex CLI로 로그인되어 있나요?",
    "OpenAI API key (persists into the Codex CLI's own login, not stored by tt):": (
        "OpenAI API 키 (tt가 아니라 Codex CLI 자체 로그인에 저장됩니다):"
    ),
    "tt auth: codex login failed: {error}": "tt auth: codex 로그인에 실패했습니다: {error}",
    "tt auth: codex model discovery failed: {error}": (
        "tt auth: codex 모델 탐색에 실패했습니다: {error}"
    ),
    "Custom Bedrock runtime endpoint URL (blank = AWS default):": (
        "사용자 지정 Bedrock runtime 엔드포인트 URL (비워 두면 AWS 기본값 사용):"
    ),
    "AWS region:": "AWS 리전:",
    "AWS profile (blank = default credential chain):": (
        "AWS 프로파일 (비워 두면 기본 자격 증명 체인 사용):"
    ),
    "(default AWS credential chain)": "(기본 AWS 자격 증명 체인)",
    "(type a different AWS profile)": "(다른 AWS 프로파일 직접 입력)",
    "AWS profile:": "AWS 프로파일:",
    "AWS profile name (blank = default credential chain):": (
        "AWS 프로파일 이름 (비워 두면 기본 자격 증명 체인 사용):"
    ),
    "tt auth: bedrock credential test failed: {error}": (
        "tt auth: bedrock 자격 증명 테스트에 실패했습니다: {error}"
    ),
    "tt auth: run `{command}` in another terminal, then choose retry.": (
        "tt auth: 다른 터미널에서 `{command}`를 실행한 뒤 재시도하세요."
    ),
    "tt auth: fix the standard AWS credential chain "
    "(env, ~/.aws/credentials, SSO, or IAM role), then choose retry.": (
        "tt auth: 표준 AWS 자격 증명 체인(env, ~/.aws/credentials, SSO 또는 IAM role)을 "
        "수정한 뒤 재시도하세요."
    ),
    "Bedrock model discovery failed.": "Bedrock 모델 탐색에 실패했습니다.",
    "Retry probe": "프로브 재시도",
    "Continue with a manual model id (discovery unavailable)": (
        "수동 모델 ID로 계속하기(탐색 불가)"
    ),
    "Azure OpenAI endpoint (e.g. https://my-resource.openai.azure.com):": (
        "Azure OpenAI 엔드포인트 (예: https://my-resource.openai.azure.com):"
    ),
    "API version (e.g. 2026-01-01-preview):": "API 버전 (예: 2026-01-01-preview):",
    "Deployment name (Azure has no key-only discovery API — type it exactly):": (
        "배포(deployment) 이름 (Azure에는 키만으로 조회하는 API가 없으니 정확히 입력하세요):"
    ),
    "tt auth: Azure OpenAI test call succeeded.": "tt auth: Azure OpenAI 테스트 호출에 성공했습니다.",
    "tt auth: Azure OpenAI test call failed: {error}": (
        "tt auth: Azure OpenAI 테스트 호출에 실패했습니다: {error}"
    ),
    "Model id (no models discovered — type one):": (
        "모델 id (발견된 모델이 없습니다 — 직접 입력하세요):"
    ),
    "(type a different model id)": "(다른 모델 id 직접 입력)",
    "Model:": "모델:",
    "Model id:": "모델 id:",
    "(default — don't set one)": "(기본값 — 설정하지 않음)",
    "Reasoning effort:": "추론 강도(effort):",
    # --- config validation errors -------------------------------------------------------
    "unknown backend {backend!r}; defined backends: {known}": (
        "알 수 없는 백엔드 {backend!r}; 정의된 백엔드: {known}"
    ),
    "no config found at {path}\nRun `tt auth` to set one up, or create it by hand — "
    "a minimal example:\n\n{example}": (
        "{path}에 설정 파일이 없습니다\n`tt auth`로 설정하거나 직접 만들어 주세요 — "
        "최소 예시:\n\n{example}"
    ),
    "invalid TOML in {path}: {error}": "{path}의 TOML이 올바르지 않습니다: {error}",
    '{path}: [defaults] must set backend = "<name>"\nExample:\n\n{example}': (
        '{path}: [defaults]에 backend = "<name>"을 설정해야 합니다\n예시:\n\n{example}'
    ),
    "{path}: [defaults] posture must be one of {valid}; got {posture!r}": (
        "{path}: [defaults] posture는 {valid} 중 하나여야 합니다; 현재 값: {posture!r}"
    ),
    '{path}: [defaults] language must be a string (e.g. "ko"); got {language!r}': (
        '{path}: [defaults] language는 문자열이어야 합니다 (예: "ko"); 현재 값: {language!r}'
    ),
    "{path}: [defaults] explanation must be true or false; got {value!r}": (
        "{path}: [defaults] explanation은 true 또는 false여야 합니다; 현재 값: {value!r}"
    ),
    "{path}: define at least one [backends.<name>] table": (
        "{path}: [backends.<name>] 테이블을 하나 이상 정의해야 합니다"
    ),
    "{path}: [defaults] backend {backend!r} is not defined; defined backends: {known}": (
        "{path}: [defaults] backend {backend!r}이(가) 정의되어 있지 않습니다; "
        "정의된 백엔드: {known}"
    ),
    "{path}: [defaults] escalation_backend {backend!r} is not defined; defined backends: {known}": (
        "{path}: [defaults] escalation_backend {backend!r}이(가) 정의되어 있지 않습니다; "
        "정의된 백엔드: {known}"
    ),
    "{path}: [cache] must be a table": "{path}: [cache]는 테이블이어야 합니다",
    "{where} must be a table": "{where}은(는) 테이블이어야 합니다",
    "{where} kind must be one of {valid}; got {kind!r}": (
        "{where} kind는 {valid} 중 하나여야 합니다; 현재 값: {kind!r}"
    ),
    '{where} must set model = "<model-id>"': '{where}에 model = "<model-id>"를 설정해야 합니다',
    "{where} kind {kind} requires base_url": "{where} kind {kind}에는 base_url이 필요합니다",
    "{where} capabilities must be a list of strings": (
        "{where} capabilities는 문자열 목록이어야 합니다"
    ),
    "{where} unknown capability {capability!r}; valid: {valid}": (
        "{where} 알 수 없는 capability {capability!r}; 유효한 값: {valid}"
    ),
    "{where} api_key_env must be a string": "{where} api_key_env는 문자열이어야 합니다",
    "{where} keyring_account must be a string": "{where} keyring_account는 문자열이어야 합니다",
    "{where} bedrock stored access keys are no longer read; "
    "credentials come from the AWS profile/default chain; re-run `tt auth`": (
        "{where} bedrock 저장된 액세스 키는 더 이상 읽지 않습니다; "
        "자격 증명은 AWS profile/default chain에서 가져옵니다; `tt auth`를 다시 실행하세요"
    ),
    "{where} unknown effort {effort!r}; valid: {valid}": (
        "{where} 알 수 없는 effort {effort!r}; 유효한 값: {valid}"
    ),
    "{where} aws_region must be a string": "{where} aws_region은 문자열이어야 합니다",
    "{where} kind bedrock requires aws_region": "{where} kind bedrock에는 aws_region이 필요합니다",
    "{where} aws_profile must be a string": "{where} aws_profile은 문자열이어야 합니다",
    "{where} bedrock base_url must be a non-empty string": (
        "{where} bedrock base_url은 비어 있지 않은 문자열이어야 합니다"
    ),
    "{where} azure_api_version must be a string": (
        "{where} azure_api_version은 문자열이어야 합니다"
    ),
    "{where} kind azure-openai requires azure_api_version": (
        "{where} kind azure-openai에는 azure_api_version이 필요합니다"
    ),
    "{path}: [prices] must be a table of per-model tables": (
        "{path}: [prices]는 모델별 테이블을 담은 테이블이어야 합니다"
    ),
    "{where} prices must be numbers": "{where} 가격은 숫자여야 합니다",
    # --- branding ------------------------------------------------------------------------
    "TinyTalk — plain English at the shell": "TinyTalk — 셸에서 쓰는 일상 언어",
    # --- tt setup wizard -------------------------------------------------------------------
    "Step 1 of 3 — language": "1/3단계 — 언어",
    "Step 2 of 3 — zsh integration": "2/3단계 — zsh 통합",
    "Step 3 of 3 — provider": "3/3단계 — 프로바이더",
    "Install the tt zsh widget into ~/.zshrc?": "tt zsh 위젯을 ~/.zshrc에 설치할까요?",
    "✓ zsh widget installed in {path}": "✓ zsh 위젯이 {path}에 설치되었습니다",
    "✓ zsh widget already installed in {path}": "✓ zsh 위젯이 이미 {path}에 설치되어 있습니다",
    "Manual zsh setup: {line}": "수동 zsh 설정: {line}",
    "Reconfigure the primary provider?": "기본 프로바이더를 다시 설정할까요?",
    "✓ primary provider configured in {path}": "✓ 기본 프로바이더가 {path}에 설정되었습니다",
    "✓ fallback provider configured in {path}": "✓ 폴백 프로바이더가 {path}에 설정되었습니다",
    "✓ primary provider already configured in {path}": (
        "✓ 기본 프로바이더가 이미 {path}에 설정되어 있습니다"
    ),
    "Provider setup skipped.": "프로바이더 설정을 건너뛰었습니다.",
    "✓ language set to {language} in {path}": "✓ 언어가 {path}에서 {language}(으)로 설정되었습니다",
    "Language setup skipped.": "언어 설정을 건너뛰었습니다.",
    "Summary": "요약",
    "✓ {label}: {path}": "✓ {label}: {path}",
    "Nothing was changed.": "변경된 내용이 없습니다.",
    "You can re-run `tt setup` anytime.": "언제든 다시 `tt setup`을 실행할 수 있습니다.",
    "Run 'tt setup' in a terminal to configure TinyTalk interactively.": (
        "TinyTalk를 대화형으로 설정하려면 터미널에서 'tt setup'을 실행하세요."
    ),
    "Provider setup: run `tt auth` when you are ready.": (
        "프로바이더 설정: 준비되면 `tt auth`를 실행하세요."
    ),
    "Language setup: run `tt setup` in a terminal when you are ready.": (
        "언어 설정: 준비되면 터미널에서 `tt setup`을 실행하세요."
    ),
    "zsh integration": "zsh 통합",
    "primary provider": "기본 프로바이더",
    "fallback provider": "폴백 프로바이더",
    "language": "언어",
}
