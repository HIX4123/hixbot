# Hixbot을 위한 Python vs TypeScript 비교

## Python MVP

이 저장소의 기본 구현은 Python입니다.

장점:

- SQLite와 파일 기반 Wiki 처리가 단순합니다.
- 로컬 LLM, 임베딩, RAG 코드가 작고 읽기 쉽게 유지됩니다.
- 표준 라이브러리 `unittest`로 테스트를 쉽게 작성할 수 있습니다.
- 노트북에서 직접 운영하는 단일 서버 봇에 잘 맞습니다.

트레이드오프:

- `discord.py`는 안정적이지만, 넓은 Discord 생태계에는 JavaScript 예제가
  더 많은 편입니다.
- `mypy` 같은 도구를 나중에 추가하지 않으면 타입 검사는 선택 사항입니다.

## TypeScript 실험 코드

TypeScript는 `spikes/`에 작은 인터페이스 실험 코드 형태로 남겨두었습니다.

장점:

- `discord.js`를 중심으로 한 Discord 생태계가 뛰어납니다.
- 강한 컴파일 시점 타입 검사와 성숙한 Node 배포 흐름을 사용할 수 있습니다.

트레이드오프:

- 이 MVP에서는 로컬 임베딩, SQLite, Markdown 조각화, Qdrant 연결부가
  Python보다 조금 더 장황해질 수 있습니다.
- 빠른 반복 개발에는 아직 Python의 모델/RAG 도구가 더 쉬운 경로입니다.

## 결정

v1은 Python으로 만듭니다. 제공자 인터페이스를 좁게 유지해서, 나중에
봇의 동작을 바꾸지 않고 TypeScript로 옮길 수 있게 합니다.
