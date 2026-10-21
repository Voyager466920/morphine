<p align="center">
  <img src="app/Resources/AppIcon.png" width="160" alt="Morphine icon">
</p>

<h1 align="center">Morphine</h1>

<p align="center">
  <b>Amphetamine keeps your Mac awake. Morphine puts your AI agent under until its usage refills.</b><br>
  When the 5-hour or weekly usage window is nearly drained, Morphine pauses the agent (spending <b>zero tokens</b> while it waits)<br>
  and wakes it the moment the window resets. Works with Claude Code, Codex, Gemini CLI, Antigravity and Grok Build.
</p>

<p align="center"><a href="#english">English</a> · <a href="#한국어">한국어</a></p>

---

## 한국어

### 이게 뭔가요

- **2% 전에 미리 멈춤:** 에이전트(Claude Code, Codex 등)가 일하는 도중 5시간 한도나 주간 한도의 남은 양이 **2% 아래로 떨어지면**, 다음 도구 호출 직전에 그 자리에서 멈춰요.
- **대기 중 토큰 0:** 멈춘 동안에는 모델을 한 번도 부르지 않아요. hook 프로세스가 리셋 시각까지 `sleep`할 뿐이에요.
- **리셋되면 자동 재개:** 한도가 다시 차면 그대로 이어서 작업해요.
- **메뉴바 표시:** Mac 메뉴바의 주사기 아이콘에서 남은 양, 대기 중인지, 언제 재개하는지 보여줘요. 작동 중엔 **파란색**, 대기 중엔 **회색**이에요.

이름은 병원의 **모르핀 PCA 펌프**에서 왔어요. 버튼을 누르면 한 번 투여되고, 그다음엔 **잠금 시간(lockout)** 동안 아무리 눌러도 안 나와요. 쿨타임이 끝나면 다시 투여돼요.

<p align="center"><img src="app/Resources/menubar-preview.png" width="420" alt="메뉴바 아이콘 상태"></p>

주사기 안 약 양 = 남은 사용량 · 숫자 = 가장 많이 쓴 한도의 사용률 · 파란색 = 작동 중 · 회색 ⏸ = 대기 중 (숫자는 리셋까지 남은 시간)

### 지원 범위

| 도구 | 남은 양 읽는 곳 | 멈추는 방법 | 미리 멈춤 |
|---|---|---|---|
| **Claude Code** | status line의 `rate_limits` (공식) → OAuth usage API | PreToolUse + UserPromptSubmit 훅 | ✅ 5시간·주간 |
| **Codex** | `codex app-server` `account/rateLimits/read` → 세션 로그 | PreToolUse + UserPromptSubmit 훅 | ✅ 5시간·주간 |
| **Antigravity** | 로컬 RPC `GetUserStatus` (실험적) | PreToolUse + PreInvocation 훅 | ✅ 5시간 (주간은 안 나옴) |
| **Gemini CLI** | 세션 파일로 하루 요청 수 **추정** | BeforeTool 훅 / PTY 래퍼 | ⚠️ 추정치라 기본은 표시만 |
| **Grok Build** | 공개된 사용량 정보 없음 | PTY 래퍼 (한도 메시지 감지) | ❌ 걸린 뒤 대기 → 재개 |

어떤 도구든 사용량을 읽지 못하면 **절대 막지 않아요** (항상 통과).

### 설치

**요구사항:** macOS 13 이상, `python3` 3.9 이상 (macOS 기본 탑재). 메뉴바 앱을 빌드하려면 Xcode Command Line Tools가 필요해요 (`xcode-select --install`).

```bash
git clone https://github.com/Voyager466920/morphine.git
cd morphine
./install.sh            # 메뉴바 앱 + CLI + 감지된 모든 도구에 훅 연결
```

- 메뉴바 앱은 `/Applications/Morphine.app`에 설치돼요.
- CLI는 `~/.local/bin/morphine`에 링크돼요.
- 원하는 도구만 연결하려면 `./install.sh claude codex`처럼 이름을 붙이면 돼요.

**Claude Code 플러그인으로 설치**하는 경우 (훅만 필요할 때):

```
/plugin marketplace add Voyager466920/morphine
/plugin install morphine@morphine
```

플러그인으로 설치했다면 사용량을 status line으로 받을 수 있게 한 번만 더 실행하세요: `morphine install claude --no-hooks`.

### 도구별로 한 번만 할 일

- **Codex:** 처음 설치한 뒤 `codex` 안에서 `/hooks`를 열고, Morphine 훅 두 개를 **신뢰(trust)** 처리해야 동작해요 (Codex 보안 정책).
- **Grok Build:** `~/.zshrc`에 `alias grok='morphine run --provider grok -- command grok'`를 추가하세요.
- **Gemini CLI:** 정확히 멈추고 싶으면 `alias gemini='morphine run --provider gemini -- command gemini'`처럼 래퍼로 실행하세요.
- **Claude Code:** 이미 쓰던 status line이 있어도 그대로 보존돼요 (Morphine이 감싸서 뒤에 `💉 5h 82%`만 붙여요).

### 자주 쓰는 명령

```bash
morphine status                           # 모든 도구의 사용량
morphine config set threshold_pct 5       # 남은 양 5% 미만에서 대기 (기본 2)
morphine config set windows '["5h"]'      # 5시간 한도만 감시 (기본: 5h, 7d, 1d)
morphine config set keep_awake false      # 대기 중 잠자기 방지 끄기
morphine resume                           # 대기 중인 에이전트 모두 지금 깨우기
morphine run --provider grok -- grok      # 사용량 API가 없는 CLI를 래퍼로 실행
morphine uninstall                        # 모든 훅 제거 (백업 남김)
```

### 알아둘 점

- **2%는 빠듯해요.** 훅은 도구 호출 직전에 확인하는데, 응답 한 번에 2% 넘게 쓰는 경우도 있어요. 무거운 작업이면 5%를 추천해요.
- **대기하는 동안 Mac이 잠들면 안 돼요.** 대기 중에는 `caffeinate -i`로 잠자기를 막아요. 다만 MacBook 덮개를 닫으면 잠들 수 있어요.
- **재개 직후 첫 호출은 조금 비싸요.** 몇 시간 쉬면 프롬프트 캐시가 만료돼서, 재개 후 첫 호출에서 캐시를 다시 쓰기 때문이에요.
- **비공식 경로는 깨질 수 있어요.** Codex app-server, Antigravity RPC, Claude OAuth usage API는 공식 문서에 없어요. 업데이트로 바뀌면 그 도구만 "알 수 없음"이 되고, 에이전트를 막지는 않아요.
- **훅은 오래 대기해요.** 주간 한도를 기다릴 수 있도록 훅 timeout을 8일로 설정해요. 대기 중 에이전트 화면에는 훅이 실행 중인 것으로 보여요.

---

## English

**What it does.** Morphine watches each agent's usage windows. When less than `threshold_pct` (default **2%**) of a 5-hour, weekly or daily window remains, the agent's own hook blocks right before the next tool call or prompt and sleeps until the reset, so **no model calls happen while it waits**. Then the hook returns and the agent continues. A macOS menu bar app shows a syringe whose dose is the remaining usage: blue while agents run, gray with a pause mark and a countdown while one is locked out, like the lockout interval on a morphine PCA pump.

| Agent | Usage source | Pause mechanism | Pauses ahead of the limit |
|---|---|---|---|
| Claude Code | status line `rate_limits` → OAuth usage API | PreToolUse + UserPromptSubmit hooks | ✅ 5h & weekly |
| Codex | `codex app-server` `account/rateLimits/read` → session logs | PreToolUse + UserPromptSubmit hooks | ✅ 5h & weekly |
| Antigravity | local `GetUserStatus` RPC (experimental) | PreToolUse + PreInvocation hooks | ✅ 5h only |
| Gemini CLI | estimated from session files | BeforeTool hook / PTY wrapper | ⚠️ estimate, display-only by default |
| Grok Build | none published | PTY wrapper (detects limit message) | ❌ waits after the limit hits |

**Install**

```bash
git clone https://github.com/Voyager466920/morphine.git && cd morphine && ./install.sh
```

Or as a Claude Code plugin: `/plugin marketplace add Voyager466920/morphine`, then `/plugin install morphine@morphine`, then `morphine install claude --no-hooks` for the status line feed. Codex users must trust the hooks once via `/hooks`.

**Design rules**

- **Unknown usage never blocks.** Adapter errors, timeouts and missing data always let the agent through.
- **One adapter per provider.** Each lives in `morphine/adapters/` and only answers "how much is used, when does it reset". `morphine/guard.py` makes the decision.
- **Shared state for the menu bar.** Lockouts are recorded in `~/.morphine/state.json` and read by the menu bar app. `morphine resume` (or the app's **Wake now** button) ends them early.
- **No dependencies.** Standard-library Python 3.9+ and SwiftUI only.

**Development**

```bash
python3 -m unittest discover tests        # 26 tests, incl. an end-to-end PTY wrapper run
app/scripts/bundle.sh                     # builds dist/Morphine.app
```

### License

MIT © Voyager466920
