# hixbot

Hixbot은 소규모 종합게임방+잡담방을 위한 한국어 LLM Discord 봇입니다.
텍스트 채널을 관찰하고 친한 게임 친구처럼 대화에 참여하며, SQLite와
Qdrant 검색을 기반으로 서버 단위 Markdown Wiki를 관리합니다.

## 이 MVP에 포함된 것

- `MESSAGE_CONTENT` 인텐트를 지원하는 Discord 텍스트 봇.
- Ollama를 통한 기본 로컬 LLM.
- OpenAI 호환 Gemini 엔드포인트를 통한 선택적 대체 응답.
- Ollama를 통한 로컬 임베딩.
- Discord 서버별 Markdown Wiki.
- 채널 설정, TTL 메시지 버퍼, 음소거, 감사 로그, 요약 기준점을 저장하는 SQLite.
- Wiki 조각 검색을 위한 Qdrant 벡터 검색.
- 슬래시 명령어:
  - `/hix status`
  - `/hix mute`
  - `/hix config`
  - `/hix learn start`
  - `/hix learn status`
  - `/hix learn stop`
  - `/hix wiki search`
  - `/hix wiki export`
  - `/hix wiki delete`
  - `/hix persona status`
  - `/hix persona reset`

## 스택 선택

이 MVP는 Python으로 구현되어 있습니다. Discord 이벤트 처리, 로컬 LLM
HTTP 연동, SQLite, RAG 연결부를 작고 단순하게 유지하기 좋기 때문입니다.
동일한 구조를 나중에 옮길 수 있도록 `spikes/`에는 작은 TypeScript
제공자 인터페이스 실험 코드도 포함되어 있습니다.

[docs/stack-comparison.md](docs/stack-comparison.md)를 참고하세요.
Wiki 작성 구조는 [docs/wiki-structure.md](docs/wiki-structure.md)에 정리되어 있습니다.

## 설정

1. 가상환경을 만들고 활성화합니다.

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. 의존성을 설치합니다.

   ```bash
   pip install -e .
   ```

3. Qdrant를 시작합니다.

   ```bash
   docker compose up -d qdrant
   ```

4. Ollama를 설치한 뒤 채팅 모델과 임베딩 모델을 받습니다.

   ```bash
   ollama pull qwen2.5:7b-instruct
   ollama pull nomic-embed-text
   ```

5. `.env.example`을 `.env`로 복사하고 Discord 토큰을 채웁니다.

   ```bash
   cp .env.example .env
   ```

6. Discord Developer Portal에서 인텐트를 활성화합니다.
   - Bot 범위: `bot`
   - Command 범위: `applications.commands`
   - Privileged Gateway Intent: `MESSAGE CONTENT INTENT`

7. 봇을 실행합니다.

   ```bash
   source .env
   python -m hixbot
   ```

## 환경변수

필수:

- `DISCORD_TOKEN`

권장:

- `DISCORD_GUILD_IDS`: 명령어 동기화를 빠르게 하기 위한 쉼표 구분 guild ID 목록.
- `BOT_OWNER_IDS`: 전역 Hixbot 성격 프로필을 조회/초기화할 수 있는 쉼표 구분 Discord user ID 목록.
- `OLLAMA_BASE_URL`: 기본값은 `http://localhost:11434`.
- `OLLAMA_CHAT_MODEL`: 기본값은 `qwen2.5:7b-instruct`.
- `OLLAMA_EMBED_MODEL`: 기본값은 `nomic-embed-text`.
- `GEMINI_API_KEY`: 선택적 대체 응답에 사용.
- `GEMINI_MODEL`: 기본값은 `gemini-3.5-flash`.
- `PRIMARY_PROVIDER`: 기본값은 `ollama`.
- `FALLBACK_PROVIDER`: 기본값은 `gemini`.
- `QDRANT_URL`: 기본값은 `http://localhost:6333`.
- `DATA_DIR`: 기본값은 `./data`.
- `LEARN_BATCH_MESSAGES`: 과거 대화 학습 batch 크기이며 기본값은 `50`.
- `LEARN_SLEEP_SECONDS`: 과거 대화 학습 batch 사이 대기 시간이며 기본값은 `60`.
- `LEARN_HISTORY_TTL_SECONDS`: 과거 대화 학습 원문 TTL이며 기본값은 `21600`.

## 개인정보 기본값

Hixbot은 Discord 원문 메시지를 영구 저장하지 않습니다. 봇이 답변하고
요약할 수 있도록 최근 메시지만 TTL이 있는 SQLite 버퍼에 저장한 뒤,
서버 Wiki 요약과 전역 성격 프로필 요약만 남깁니다. 운영자는 슬래시 명령어로 Wiki
내용을 내보내거나 삭제할 수 있고, 봇 소유자는 전역 성격 프로필을 조회하거나 초기화할 수 있습니다.

## 과거 대화 학습

서버에 처음 봇을 들인 뒤 `/hix learn start`를 실행하면, 봇이 읽을 수 있는
서버 텍스트 채널의 과거 대화를 천천히 훑으며 Wiki 요약을 작성합니다.
작업은 채널별 마지막 message ID를 SQLite에 저장하므로, `/hix learn stop`
이후 다시 `/hix learn start`를 실행해도 처음부터 다시 읽지 않고 저장된
cursor 이후부터 이어서 시작합니다.

- `/hix learn start`: 저장된 cursor 이후부터 서버 전체 과거 대화 학습을 시작합니다.
- `/hix learn status`: 현재 상태, 처리량, 마지막 cursor, 오류를 확인합니다.
- `/hix learn stop`: 현재 batch 정리 후 안전하게 멈춥니다.

## 로컬 테스트

포함된 테스트는 Python `unittest`를 사용하므로 pytest를 설치하지 않아도
실행할 수 있습니다.

```bash
python -m unittest discover -s tests
```
