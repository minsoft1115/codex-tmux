# Codex + tmux 사용량 화면

```bash
python3 /설치/경로/codex-tmux/codex_tmux.py /프로젝트/경로
```

경로를 생략하면 현재 폴더를 사용합니다. 실행마다 별도 소켓의 전용 tmux
서버를 만들고 연결합니다. 일반 `tmux ls` 목록이나 다른 실행과 섞이지 않으며,
사용자 tmux 설정과 세션 복원 플러그인은 로드하지 않습니다.
Codex는 pane 하나를 사용하고 사용량은 최하단 한 줄 status bar에 표시합니다.

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
codex-tmux ~/my-project    # 특정 프로젝트에서 실행
codex-tmux --detach        # 연결하지 않고 세션 생성
```

`~/.local/share/codex-tmux/`에 프로그램을 복사하고 `~/.local/bin/codex-tmux`를
실행 파일로 등록합니다. Bash alias 없이 다른 셸에서도 사용할 수 있으며,
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

이전 설치기가 `.bashrc`에 등록한 관리 블록은 백업 후 제거합니다. 이미 열린
Bash에는 이전 alias가 남을 수 있으므로 한 번 실행하세요.

```bash
unalias codex-tmux 2>/dev/null
hash -r
```

`--prefix /설치/경로`로 설치 위치를 바꿀 수 있으며 실행 파일은 `bin/`, 프로그램은
`share/codex-tmux/`에 배치됩니다. `--bashrc /경로/bashrc`는 이전 alias를 제거할
설정 파일을 지정합니다. 별도로 만든 alias는 수정하지 않습니다.

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

한 실행은 최초 연결된 세션에 고정됩니다. `/new` 등으로 다른 세션을 사용하려면
이 스크립트로 새 실행을 여세요.

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
로그는 실행 종료 시 임시 폴더와 함께 삭제됩니다.

Codex가 실제 종료되거나 pane 종료가 확인되면 전용 tmux 서버와 임시 폴더를
정리합니다. 기존 tmux 서버와 다른 실행은 유지됩니다. Ctrl+C를 Codex가
작업 중단으로 처리하고 계속 실행 중이면 창도 유지됩니다.

Detach는 종료가 아니므로 Codex와 표시기가 계속 실행됩니다. `--detach`가 출력하는
`attach:` 명령으로 다시 연결할 수 있습니다. 강제 kill이나 tmux 서버 강제 종료 시
`/tmp/codex-tmux-*` 임시 폴더가 남을 수 있습니다. 이 도구는 인증 정보를 저장하지 않습니다.

## 옵션과 검증

```bash
# 실제 바이너리 지정
python3 codex_tmux.py /프로젝트/경로 --codex /절대/경로/codex

# 연결하지 않고 생성: 서버 연결 명령과 runtime 폴더 출력
python3 codex_tmux.py /프로젝트/경로 --detach

# 외부 모듈 및 API 요청 없이 테스트
python3 -B -m unittest -v test_codex_tmux test_install_remote
```

테스트는 로그 증분 읽기·손상·교체·삭제, 세션별 context와 전체 한도 분리,
화면 너비별 렌더링, 동시 훅과 중첩 프로세스 거부, 표시기 오류 격리를 검증합니다.
모의 Codex와 임시 tmux 서버로 tmux 안팎 실행의 서버 격리, 단일 pane,
기존 서버 설정 유지, Ctrl+C 및 정상 종료 후 정리도 검증합니다.
실제 Codex의 훅 신뢰 UI는 자동 테스트 범위에 포함하지 않습니다.
원격 설치 테스트는 다운로드를 로컬 압축파일로 대체하여 옵션 전달, 재설치,
이전 alias 정리, 다운로드·압축 해제·설치 실패와 임시 폴더 정리를 검증합니다.
