# Codex + tmux 사용량 화면

```bash
python3 /설치/경로/codex-tmux/codex_tmux.py -C /프로젝트/경로
```

Codex 인자를 그대로 전달하며, `-C`를 생략하면 Codex의 기본 작업 디렉터리 규칙을
따릅니다. 대화형 실행마다 별도 소켓의 전용 tmux
서버를 만들고 연결합니다. 일반 `tmux ls` 목록이나 다른 실행과 섞이지 않으며,
사용자 tmux 설정과 세션 복원 플러그인은 로드하지 않습니다.
Codex는 pane 하나를 사용하고 사용량은 최하단 한 줄 status bar에 표시합니다.

## 기록 스크롤

전용 tmux 서버에서는 마우스를 활성화하며, 접두키 없이 기록을 탐색할 수 있습니다.

| 입력 | 일반 대화 화면 | 대체 화면 (`Ctrl+T` 대화 기록, diff 등) |
|---|---|---|
| 휠 위/아래 | tmux 기록을 5줄씩 스크롤 | Codex에 위/아래 화살표 키 전달 |
| Page Up | tmux 복사 모드에 진입해 한 페이지 위로 | Codex에 Page Up 전달 |
| Page Down | 복사 모드에서 한 페이지 아래로 | Codex에 Page Down 전달 |
| Esc | tmux 복사 모드 종료 | Codex의 기존 동작 |

복사 모드에서 최신 출력까지 내려오면 자동으로 종료합니다. 일반 화면의 최신
위치에서는 휠 아래와 Page Down이 입력창에 전달되지 않습니다. 이미 복사 모드에
들어간 경우에는 대체 화면 여부와 관계없이 tmux 기록 탐색을 우선합니다.
화면 구분은 `Ctrl+T` 키 자체가 아니라 tmux의 `alternate_on` 상태를 사용합니다.
일반 화면에서 탐색할 수 있는 범위는 tmux가 보관한 스크롤백에 한정됩니다.

## 요구 사항

- Linux (`/proc`의 프로세스 부모 관계와 `fcntl` 파일 잠금 사용)
- Python 3.11 이상, `tmux`, 훅을 지원하는 `codex` 실행 파일
- `codex_tmux.py`와 `usage.py`를 같은 폴더에 보관

Python 외부 패키지나 `codex-usage-bar` 설치는 필요하지 않습니다.
네트워크/API 조회 없이 `$CODEX_HOME/sessions`의 로컬 JSONL 로그를 읽습니다.
`CODEX_HOME` 기본값은 `~/.codex`입니다.

## 설치

GitHub에서 최신 소스를 받아 설치합니다. `curl`, `tar`가 추가로 필요하며 Git은
필요하지 않습니다.

```bash
curl -fsSL https://raw.githubusercontent.com/minsoft1115/codex-tmux/main/install-remote.sh | bash
```

설치 후 실행합니다.

```bash
codex-tmux                 # 현재 폴더에서 실행
codex-tmux -C ~/my-project # 특정 프로젝트에서 실행
codex-tmux resume --last   # 최근 세션 재개
codex-tmux resume <ID>     # 지정한 세션 재개
codex-tmux --detach        # 연결하지 않고 세션 생성
```

`~/.local/share/codex-tmux/`에 프로그램을 복사하고 `~/.local/bin/codex-tmux`를
실행 파일로 등록합니다. 다른 셸에서도 `codex-tmux` 명령을 사용할 수 있으며,
원본 프로젝트 폴더를 이동해도 설치된 명령은 유지됩니다. 업데이트는 위 설치 명령을
다시 실행하면 됩니다. Python 외부 패키지는 설치하지 않습니다.
`~/.local/bin`이 PATH에 없으면 설치 출력에 나오는 PATH 설정을 적용하세요.

설치 옵션은 `bash -s --` 뒤에 전달합니다.

```bash
curl -fsSL https://raw.githubusercontent.com/minsoft1115/codex-tmux/main/install-remote.sh | bash -s -- --prefix "$HOME/.local"
```

원격 설치기는 소스 압축파일을 임시 폴더에 내려받아 기존 `install.sh`를 실행하고,
종료 시 임시 폴더를 정리합니다. 다운로드나 압축 해제가 실패하면 설치를 중단합니다.
기본 소스는 `main`이며, `CODEX_TMUX_REF`로 태그 또는 커밋을 지정할 수 있습니다.
설치기와 소스를 모두 고정하려면 아래 `COMMIT_SHA`를 같은 실제 커밋 ID로 바꾸세요
(해당 커밋에 `install-remote.sh`가 있어야 합니다).

```bash
curl -fsSL https://raw.githubusercontent.com/minsoft1115/codex-tmux/COMMIT_SHA/install-remote.sh | CODEX_TMUX_REF=COMMIT_SHA bash
```

이미 저장소를 내려받았다면 프로젝트 폴더에서 `./install.sh`로 설치할 수도 있습니다.

설치기는 `.bashrc`를 백업하고 관리 블록에 `codex` → 설치된 `codex-tmux`
alias를 등록합니다. 재설치 시 같은 블록을 갱신하므로 중복되지 않습니다.
이전 설치기의 `codex-tmux` alias 블록도 이 형식으로 교체합니다.
이미 열린 Bash에서는 한 번 실행하세요.

```bash
unalias codex-tmux 2>/dev/null
source ~/.bashrc
```

`--prefix /설치/경로`로 설치 위치를 바꿀 수 있으며 실행 파일은 `bin/`, 프로그램은
`share/codex-tmux/`에 배치됩니다. `--bashrc /경로/bashrc`로 alias를 등록할 파일을
지정할 수 있습니다. 기존 사용자 alias는 삭제하지 않지만, 마지막 관리 블록의
`codex` alias가 우선합니다. `command codex`로 래퍼를 우회할 수 있습니다.

## Codex 인자 전달

```bash
codex resume --last
codex resume <ID> -C ~/project
codex -m <모델> '공백이 있는 프롬프트'
codex exec --json '비대화형 작업'
```

설치 후 새 Bash에서 위 명령은 alias를 통해 실행됩니다. 셸 alias는 Python의
실행 파일 탐색에 적용되지 않으므로 래퍼는 실제 `codex` 실행 파일을 호출합니다.
위치 인자는 모두 Codex에 전달합니다. **기존 `codex-tmux ~/project` 대신
`codex-tmux -C ~/project`를 사용하세요.** 경로만 전달하면 시작 프롬프트가 됩니다.

래퍼 전용 `--codex PATH`, `--detach`, `--tmux-help`는 Codex 인자보다 앞에 둡니다.
첫 Codex 인자 이후의 순서·공백과 `--` 뒤의 프롬프트는 그대로 유지합니다.
`--help`와 `--version`, 비대화형 관리 명령(`exec`, `login`, `mcp`, `completion` 등),
터미널 표준입력이 없는 실행은 tmux 없이 실제 Codex로 전달해 표준입출력과
종료 코드를 유지합니다. `--detach`는 터미널 없이도 대화형 tmux 세션을 만듭니다.

대화형 실행에는 `SessionStart`와 `UserPromptSubmit` 연결 훅을 추가합니다.
CLI에서 같은 이벤트를 설정했다면 마지막 유효 값을 보존해 연결 훅과 병합하고,
같은 무조건 실행 연결 훅이 CLI에 이미 있으면 중복 추가하지 않습니다.
설정 파일·플러그인의 훅은 Codex의 계층별 로더에 맡기며 파일을 수정하지 않습니다
(Codex 0.155.1 기준). 훅 신뢰 승인이나 훅 실행 제한 정책은 그대로 적용됩니다.

## 제거

설치된 명령으로 제거할 수 있습니다. 소스 폴더나 네트워크 연결은 필요하지 않습니다.

```bash
codex-tmux --uninstall
```

프로젝트 폴더에서는 다음 명령으로 제거할 수도 있습니다. 이전 버전으로 설치한
경우에도 사용할 수 있으며, 사용자 지정 경로라면 설치할 때와 같은 prefix를 지정합니다.

```bash
./install.sh --uninstall
./install.sh --uninstall --prefix /설치/경로
```

설치한 실행 파일·프로그램·해당 Python 캐시와 관리하는 Bash alias 블록을 제거합니다.
현재 셸에 남은 alias는 `unalias codex`로 해제하세요. Codex 자체, 세션 기록,
사용자 설정, 소스 저장소와 별도로 추가한 파일은 유지합니다. 실행 중인
codex-tmux 세션은 종료한 뒤 제거하세요.

## 최초 연결

상태줄의 `ctx: prompt`는 최초 세션 연결 대기, `ctx: wait`는 연결 후 사용량
이벤트 대기를 뜻합니다. 첫 메시지를 보낸 뒤 사용량 이벤트가 기록되면
context 수치가 표시됩니다. 연결 전에도 로그에 있는 5h/7d 한도는 표시합니다.

Codex에서 훅 검토 경고가 나오면 `/hooks`에서 `codex_tmux.py _capture`의
SessionStart / UserPromptSubmit 훅을 검토하고 신뢰 처리한 뒤 메시지를 보내세요.
훅 신뢰 우회나 전역 설정 파일 수정은 하지 않습니다. 정책으로 훅이 비활성화된
환경에서는 세션을 연결할 수 없습니다.

훅은 실행별 임시 폴더에 세션 ID와 transcript 경로를 기록하며 모델에 텍스트를
추가하지 않습니다. 파일 잠금으로 최초 연결을 보호하고, 후속 이벤트에 경로가
없어도 기존 transcript 경로를 유지합니다. 실행한 Codex와 훅 사이에 다른
프로그램이 끼어 있으면 연결을 거부하여 중첩 CLI의 세션 연결을 막습니다.
일반 셸 래퍼는 허용하지만, Node 등 별도 프로세스를 유지하는 런처를 사용하는
경우 `--codex`로 실제 Codex 바이너리를 지정해야 합니다.

같은 Codex 프로세스의 `SessionStart` 훅(`startup`, `resume`, `clear`, `compact`)이
새 세션 ID를 알리면 상태줄도 해당 세션으로 전환합니다. `/clear` 직후 새 토큰
정보가 없으면 context는 `wait`로 표시하고, 다음 토큰 정보가 기록되면 갱신합니다.
5h·7d는 계정 사용 한도이므로 초기화하지 않습니다. 갱신에는 최대 약 5초가 걸립니다.
이전 세션의 늦은 `UserPromptSubmit` 이벤트와 중첩 CLI의 훅은 연결을 바꾸지 않습니다.

## 표시와 설정

- 세션 ID 앞 8자리, context, 5h, 7d를 표시하고 5초마다 갱신합니다.
- context는 `session_meta.id`가 일치하는 로그의 최신 유효한 `token_count` 이벤트에서
  `last_token_usage.total_tokens / model_context_window × 100`으로 계산합니다.
  실시간으로 편집 중인 입력량은 포함하지 않습니다.
- 5h/7d는 같은 `CODEX_HOME`의 세션 로그들에서 각 시간 구간의 최신 유효한 관측값을
  사용합니다. 현재 계정에 API로 확인한 값이 아니므로 계정 전환이나 리셋 직후에는
  새 이벤트가 기록되기 전까지 이전 관측값이 보일 수 있습니다.
- 한도 데이터가 없으면 `N/A`로 표시합니다. 타임스탬프가 없는 이벤트는 날짜가
  있는 이벤트보다 우선하지 않습니다.
- 최초 읽기 후에는 파일별 위치와 집계값을 유지하고 추가된 줄만 읽습니다.
  파일 목록은 갱신마다 확인하며, 삭제·교체·축소된 파일은 캐시를 갱신합니다.
  잘못된 JSON 줄은 건너뛰고, 아직 줄바꿈이 없는 마지막 줄은 다음 갱신에 읽습니다.
- 연결된 tmux 클라이언트 중 가장 좁은 너비에 세 막대를 함께 맞춥니다.
  연결된 클라이언트가 없으면 tmux 창 너비를 사용합니다. 게이지는 `▓▓░░` 형태로
  표시하며 채워진 칸에만 색상을 적용합니다. 배경과 일반 글자는 터미널 기본색을
  따릅니다. 좁은 화면에서는 세션 ID와 여백을 먼저 줄여 세 게이지를 유지하고,
  그래도 공간이 부족하면 비율만 표시합니다. 극도로 좁으면 텍스트가 잘릴 수 있습니다.
- `NO_COLOR=1`로 색상을 끌 수 있습니다. `CODEX_TMUX_BAR_WIDTH=10`으로 막대의
  최대 폭을 지정합니다(0–40, 0은 막대 생략).
- 기존 외부 모듈의 설정 파일은 읽지 않습니다. 고정된 짧은 영문 레이블을 사용하며,
  `USAGE_BAR_LANG=es`, `pt`, `it`에서는 미확인 한도 값을 `N/D`로 표시합니다.

## 오류와 종료

표시기에서 오류가 나도 Codex를 종료하지 않습니다. 상태줄에 오류를 표시하고
다음 갱신 때 재시도하며, 자세한 예외는 실행별 `watcher.log`에 남깁니다.
로그가 256 KiB를 넘으면 다음 감시 주기에 비워 누적을 제한합니다.
로그는 실행 종료 시 임시 폴더와 함께 삭제됩니다.

Codex가 실제 종료되거나 pane 종료가 확인되면 전용 tmux 서버와 임시 폴더를
정리합니다. 기존 tmux 서버와 다른 실행은 유지됩니다. Ctrl+C를 Codex가
작업 중단으로 처리하고 계속 실행 중이면 창도 유지됩니다.

일반 실행으로 연결한 상태에서 Codex가 종료되면, 종료 직전 pane의 마지막
화면 텍스트를 저장하여 원래 터미널에 그대로 출력합니다. 특정 안내 문구를
검색하거나 내용을 재구성하지 않으며, 줄바꿈과 빈 줄도 유지합니다.
스크롤백은 포함하지 않습니다. pane을 캡처할 수 없으면 추가 출력 없이 정리합니다.
이 기능은 실행 명령이 연결을 기다리고 있는 동안에만 동작하며, detach 후
별도의 `tmux attach-session` 명령으로 연결한 경우에는 적용되지 않습니다.

Detach는 종료가 아니므로 Codex와 표시기가 계속 실행됩니다. `--detach`가 출력하는
`attach:` 명령으로 다시 연결할 수 있습니다. 터미널 창이나 접속 클라이언트만
종료해도 같은 정책을 적용합니다.

강제 종료 복구는 다음과 같이 동작합니다.

- Codex 실행 전에 감시기를 시작하고, 실행 명령은 초기화 완료까지 기다립니다.
  감시기 생성 전 worker가 종료되어도 실행 명령이 정리를 맡습니다. 실행 보조
  프로세스가 정리 잠금 안에서 자신의 ID를 등록한 뒤 Codex로 전환하므로,
  프로세스 생성과 ID 기록 사이에 worker가 종료되는 경우도 처리합니다.
- Codex 또는 worker가 종료되면 전용 서버와 임시 폴더를 정리합니다.
  터미널 종료 신호를 무시하고 남은 Codex 본체도 종료합니다.
- 상태바 감시기가 종료되면 worker가 재시작하여 갱신을 재개합니다.
- tmux 서버가 강제 종료되어 소켓이 남아도 프로세스 생존 여부로 판단해 정리합니다.
  소켓이 사라져 종료 명령이 실패하면 기록된 서버 프로세스를 직접 종료하고,
  실제 종료를 확인한 뒤 임시 폴더를 삭제합니다.
- 정리는 운영체제 파일 잠금으로 직렬화합니다. 정리 프로세스가 강제 종료되면
  잠금이 해제되어 감시기가 이어받습니다. 폴더 삭제 직전에는 같은 임시 경로의
  `codex-tmux-trash-<UUID>`로 이름을 바꿉니다. 이후 삭제가 중단되어 내부 실행
  정보가 없어져도 다음 실행에서 해당 정리 대상 폴더를 삭제합니다.
- PID, 프로세스 시작 시각, 부팅 ID를 함께 확인합니다. 종료 신호는 pidfd로 보내
  PID가 재사용된 다른 프로세스를 종료하지 않습니다.
- 모든 관련 프로세스가 함께 종료되면 다음 실행에서 잔여 폴더를 정리합니다.
  현재 사용자 소유의 새 실행 형식만 대상으로 하며, 기록된 프로세스가 하나라도
  살아 있거나 소유권·형식을 확인할 수 없으면 건드리지 않습니다. 이전 버전의
  잔여 폴더는 자동 청소 대상이 아닙니다.

종료 감지는 보통 5초 이내이며, 정리와 명령 타임아웃에 따라 더 걸릴 수 있습니다.
강제 종료 전에 저장하지 못한 응답이나 별도로 분리 실행된 작업의 복구는 보장하지
않습니다. 이 도구는 인증 정보를 저장하지 않습니다. 업데이트된 종료 관리 기능은
codex-tmux를 새로 실행할 때 적용되며, 기존 실행에서 `/clear`만 해서는 적용되지 않습니다.

`test_lifecycle.py`, `test_recovery_edges.py`, `test_startup_recovery.py`는
전용 tmux 서버와 가짜 Codex로 강제 종료, 감시기 재시작,
정리 세 단계의 중단·재개, 연결 해제·재접속, 다음 실행의 잔여물 청소,
다른 세션 격리, 로그 누적 제한과 표시 복구를 검증합니다. 소켓 유실, 초기화 중
worker 종료, 폴더 일부 삭제 후 복구와 교체된 감시기 종료도 포함합니다.

## 옵션과 검증

```bash
# 실제 바이너리 지정
python3 codex_tmux.py --codex /절대/경로/codex -C /프로젝트/경로

# 연결하지 않고 생성: 서버 연결 명령과 runtime 폴더 출력
python3 codex_tmux.py --detach -C /프로젝트/경로

# 외부 모듈 및 API 요청 없이 테스트
python3 -B -m unittest discover -v
```

테스트는 로그 증분 읽기·손상·교체·삭제, 세션별 context와 전체 한도 분리,
화면 너비별 렌더링, 동시 훅과 중첩 프로세스 거부, 표시기 오류 격리를 검증합니다.
모의 Codex와 임시 tmux 서버로 tmux 안팎 실행의 서버 격리, 단일 pane,
기존 서버 설정 유지, Ctrl+C 및 정상 종료 후 정리도 검증합니다.
실제 Codex의 훅 신뢰 UI는 자동 테스트 범위에 포함하지 않습니다.
원격 설치 테스트는 다운로드를 로컬 압축파일로 대체하여 옵션 전달, 재설치,
alias 등록·실행·제거, 다운로드·압축 해제·설치 실패와 임시 폴더 정리를 검증합니다.
인자 보존·CLI 훅 병합·resume 연결과 실제 tmux 클라이언트의 스크롤 입력도 검증합니다.
