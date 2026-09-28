# codex-tmux

Codex CLI에 context 사용량과 5시간·7일 사용 한도를 보여 주는 tmux 상태줄을 추가합니다.
실행마다 전용 tmux 서버를 사용하므로 기존 tmux 세션이나 설정에 영향을 주지 않습니다.
사용량은 로컬 Codex 로그에서 읽으며, 별도의 API 요청이나 Python 외부 패키지는 필요하지 않습니다.

## 요구 사항

- Linux
- Python 3.11 이상, tmux, 훅을 지원하는 Codex CLI
- 원격 설치 시 curl, tar

## 설치 및 업데이트

```bash
curl -fsSL https://raw.githubusercontent.com/minsoft1115/codex-tmux/main/install-remote.sh | bash
```

프로그램은 `~/.local/share/codex-tmux/`, 실행 명령은 `~/.local/bin/codex-tmux`에
설치됩니다. `~/.local/bin`이 PATH에 없으면 설치 안내에 따라 추가하세요.
저장소를 내려받은 경우에는 `./install.sh`로 설치할 수도 있습니다.

설치기는 `.bashrc`를 백업하고 `codex`를 `codex-tmux`로 연결하는 alias를 등록합니다.
새 Bash를 열거나 현재 셸에서 다음을 실행하세요.

```bash
source ~/.bashrc
```

다른 셸에서는 `codex-tmux`를 직접 실행하세요. Bash에서 래퍼를 우회하려면
`command codex`를 사용합니다.

업데이트는 같은 설치 명령을 다시 실행한 뒤 codex-tmux를 종료하고 새로 실행하면 됩니다.
설치 경로나 Bash 설정 파일은 `--prefix PATH`, `--bashrc PATH`로 지정할 수 있습니다.
원격 설치에서는 `bash -s --` 뒤에 옵션을 붙입니다.

## 사용법

```bash
codex-tmux                          # 현재 폴더에서 실행
codex-tmux -C ~/my-project           # 프로젝트 지정
codex-tmux resume --last             # 최근 대화 재개
codex-tmux resume <ID>               # 지정한 대화 재개
codex-tmux -m <모델> '프롬프트'        # Codex 옵션 전달
```

Bash alias가 적용돼 있으면 `codex`로도 동일하게 실행할 수 있습니다.
작업 폴더는 `-C`로 지정하세요. 옵션 없이 전달한 경로는 프롬프트로 처리됩니다.
`exec`, `login`, `--help` 같은 비대화형 명령은 tmux 없이 Codex로 전달됩니다.

래퍼 전용 옵션은 Codex 인자보다 앞에 둡니다.

| 옵션 | 설명 |
|---|---|
| `--codex PATH` | 실행할 Codex 바이너리 지정 |
| `--detach` | 연결하지 않고 세션 생성, 재접속 명령 출력 |
| `--tmux-help` | 래퍼 도움말 표시 |

일반 실행은 터미널 연결과 색상 확인 후 Codex를 시작합니다. 색상 응답이 없으면
짧은 대기 후 그대로 실행합니다. `--detach`는 연결 없이 시작하므로 입력창 배경색이
표시되지 않을 수 있습니다.

## 사용량 표시

상태줄은 세션 ID 앞 8자리, context 사용률, 5시간·7일 사용 한도를 표시하고
약 5초마다 갱신합니다. 화면 너비에 맞춰 막대 길이가 조절됩니다.

- `ctx: prompt`: 세션 연결 대기. 첫 메시지를 보내세요.
- `ctx: wait`: 연결 후 사용량 로그 대기.
- `N/A`: 사용 한도 데이터 없음.

훅 검토 경고가 나오면 `/hooks`에서 `codex_tmux.py _capture`의
SessionStart / UserPromptSubmit 훅을 검토하고 신뢰 처리하세요.
훅이 비활성화된 환경에서는 세션을 연결할 수 없습니다. 별도 런처를 사용해 연결되지
않는 경우에는 `--codex`로 실제 Codex 바이너리를 지정하세요.

context는 현재 대화의 최신 토큰 사용량을 기준으로 하며, 입력 중인 텍스트는 포함하지
않습니다. 5시간·7일 한도는 로컬 로그의 최신 값이므로 계정 전환이나 한도 초기화 직후에는
새 사용량이 기록될 때까지 이전 값이 보일 수 있습니다.

| 환경 변수 | 설명 |
|---|---|
| `CODEX_HOME` | Codex 데이터 경로. 기본값 `~/.codex` |
| `NO_COLOR=1` | 상태줄 색상 끄기 |
| `CODEX_TMUX_BAR_WIDTH` | 막대 최대 폭, 0–40. 0은 막대 생략 |

## 기록 스크롤

tmux 접두키 없이 기록을 탐색할 수 있습니다.

| 입력 | 일반 대화 화면 | 대화 기록·diff 등 대체 화면 |
|---|---|---|
| 마우스 휠 | tmux 기록을 5줄씩 스크롤 | Codex에 위/아래 키 전달 |
| Page Up / Page Down | tmux 기록을 페이지 단위로 탐색 | Codex에 해당 키 전달 |
| Esc | tmux 복사 모드 종료 | Codex의 기존 동작 |

일반 화면에서 위로 스크롤하면 복사 모드로 들어가며, 최신 출력까지 내려오면
자동으로 빠져나옵니다. 탐색 범위는 tmux가 보관한 기록에 한정됩니다.

## 종료와 재접속

Codex가 종료되면 해당 tmux 서버와 임시 파일을 정리합니다. 일반 실행에서는 마지막
화면을 색상과 함께 원래 터미널에 남깁니다. 별도로 재접속한 경우에는 적용되지 않습니다.
Ctrl+C가 작업 중단으로 처리되면 Codex는 계속 실행됩니다.

터미널을 닫거나 tmux 연결만 끊으면 Codex는 백그라운드에 남습니다.
`--detach`가 출력한 `attach:` 명령으로 다시 연결할 수 있습니다.
일반 `tmux ls`에는 전용 서버의 세션이 표시되지 않습니다.
resume에서 다른 곳에서 사용 중이라는 메시지가 나오면, 기존 세션에 다시 연결해
Codex를 정상 종료한 뒤 재시도하세요.

## 제거

실행 중인 세션을 종료한 뒤 다음 명령을 실행하세요.

```bash
codex-tmux --uninstall
unalias codex  # 현재 Bash에 남은 alias 해제
```

설치한 프로그램과 관리하는 alias를 제거합니다. Codex 자체와 대화 기록, 사용자 설정은 유지합니다.

## 개발

```bash
python3 codex_tmux.py -C ~/my-project
python3 -B -m unittest discover -v
```

테스트는 가짜 Codex와 임시 tmux 서버를 사용하며 외부 API를 호출하지 않습니다.
