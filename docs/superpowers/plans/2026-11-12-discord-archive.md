# Discord Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. User may instead select superpowers:subagent-driven-development.

**Goal:** 유료 AI 없이 개인 Discord 기록을 검색하고 새 자료를 학습한 기준에 따라 정리한다.

**Architecture:** Discord 어댑터와 순수 분류·검색·보관 상태 로직을 분리한다. SQLite는 자료·학습 예시·작업 상태를 기록하고 로컬 파일은 검증된 보관이 끝날 때까지 유지한다. 기존 글 색인과 새 수집함 자료의 오려두기는 별도 작업이다.

**Tech Stack:** Python 3.11+, discord.py 2.x, SQLite/FTS5, scikit-learn, pypdf, python-docx, Pillow/pytesseract, httpx, BeautifulSoup, pytest. Tesseract 한국어 모델은 선택 설치. 버전은 구현 시 지원 문서 확인 후 고정한다. ALT가 서버 HTML에 전사문을 제공하지 않으면 공개 페이지용 Playwright 어댑터를 검토한다.

**Spec:** docs/superpowers/specs/2026-11-12-discord-archive-design.md

## Global Constraints

- 기존 글과 채널 분류는 유지한다.
- Windows PC에서 실행하며 유료 AI, ALT 유료 구독, 호스팅을 요구하지 않는다.
- 새 채널 생성은 항상 사용자 승인 후 실행한다.
- 사용자의 승인·수정은 확정 예시이고 자동 예측은 자동으로 정답에 편입하지 않는다.
- 실패·파일 업로드 한도 초과·확인 대기 중에는 수집함 원본을 유지한다.
- 일반 사용자 계정 토큰 대신 공식 봇 계정을 쓴다.

## Review Focus

1. 게시 직후 프로세스 종료: 재시작 시 이미 게시된 메시지를 찾아 중복을 피하고 검증 이전 원본을 보존한다. Task 6.
2. 코드·한글 혼합 및 별칭: 한글 키워드와 C++/Python 태그를 검색하고 잘못된 동의어를 합치지 않는다. Tasks 1, 4.
3. 암호화 PDF·손상 문서·OCR 미설치: 실패 이유를 표시하며 파일을 보존한다. Task 2.
4. 공유 링크 리다이렉트·만료: 내부 주소 접근을 차단하고 ALT 추출 실패는 대기 처리한다. Task 3.
5. 삭제된 채널·권한 철회·재시작 후 승인 버튼: 작업을 실패/대기로 유지하고 소유자만 승인한다. Tasks 5–7.

## File Structure and Interfaces

`src/discord_archive/` 아래 기능별 모듈을 둔다.

- `models.py`: Item(id, source_message_id, channel_id, author_id, created_at, body, extracted_text, attachment_ids, source_url), Attachment(id, filename, local_path, sha256, size), Extraction(text, status, reason), Prediction(channel_id, score, tags, model_version, review_required), PublishReceipt(message_ids, attachment_hashes). ID는 문자열이며 nullable 필드는 명시한다. 파일 경로는 Path다.
- `config.py`: load_config(path: Path) -> Config. 토큰은 환경 변수, 서버·소유자·허용 채널·수집함·확인 채널 ID와 크기 상한은 설정 파일.
- `store.py`: Store(db_path: Path), upsert_item(item: Item), record_feedback(item_id: str, channel_id: str, tags: list[str]), search(query: str, tags: list[str], limit: int, offset: int) -> list[Item], transition(job_id: str, expected: str, next_state: str), backup(path: Path). SQLite 트랜잭션으로 상태 변경.
- `tags.py`: canonicalize(tag: str) -> str, resolve_alias(tag: str, aliases: dict[str,str]) -> str.
- `extract.py`: extract_file(path: Path) -> Extraction. 파일별 실패는 반환값으로 표현.
- `alt.py`: fetch_note(url: str) -> AltNote(note_id, title, transcript, source_url). 네트워크/페이지 실패는 구분된 예외.
- `learning.py`: train(examples: list[tuple[str,str]], version: str) -> ModelBundle, predict(text: str, metadata: dict, model: ModelBundle | None) -> Prediction. 모델을 버전별 저장하며 태그 추천은 확인된 사전/키워드 기반.
- `indexing.py`: async index_history(gateway: DiscordGateway, store: Store, channel_ids: list[str]) -> IndexReport.
- `organize.py`: async process_job(job_id: str, store: Store, gateway: DiscordGateway) -> str.
- `discord_gateway.py`: DiscordGateway의 history(channel_id, before), download(attachment), publish(item, channel_id, job_id), reconcile(job_id, channel_id), verify(receipt, item), delete_source(message_id), create_channel(name) 비동기 메서드. 테스트에서는 FakeGateway로 대체.
- `review.py`, `commands.py`, `app.py`: 영속 확인 UI, 검색·설정·학습·상태 명령, 실행 및 재개 조립.
- `tests/`: 모듈별 단위/통합 테스트. `README.md`, `.env.example`, `.gitignore`, `config.example.toml`, `pyproject.toml`, `scripts/start.ps1`: 실행 안내. 데이터·토큰·전사문·모델은 Git 제외.

### Task 1: 저장·검색·태그의 로컬 기반

**Files:** models.py, config.py, store.py, tags.py, pyproject.toml, .gitignore, config.example.toml, .env.example; tests/test_store.py, tests/test_tags.py, tests/test_config.py.
**Interfaces:** 위 공통 데이터 모델 및 Store/태그 인터페이스를 제공한다.

- [ ] 실패 테스트 작성: 한글 본문 `최소자승법` 검색 시 원문 ID 반환, 같은 source ID 두 번 색인해도 1건, `파이썬`/`python`은 승인 별칭 `Python`으로 검색, 두 태그 AND 검색은 둘 다 가진 자료만 반환, SQL/FTS 특수 문자는 구문 오류 없이 처리. 소유자/서버 설정 누락은 시작 실패, 토큰은 예외 메시지에 미노출.
- [ ] `python -m pytest tests/test_store.py tests/test_tags.py tests/test_config.py -q` 실행, 구현 부재로 실패 확인.
- [ ] 공통 모델, SQLite 마이그레이션·작업 상태·학습 예시·FTS·태그 연결, 설정 로딩 구현. 한국어 부분 문자열은 매개변수화된 LIKE 보조 검색으로 지원. 별칭 대소문자 정규화와 의미 병합 승인 분리. 초기 로컬 저장 상한은 5 GiB, 파일 상한은 25 MiB이며 Discord 실제 상한이 더 낮으면 그 값을 적용, 설정으로 변경 가능.
- [ ] 위 명령 PASS 확인. `git add`에는 해당 파일만 지정해 커밋. Git 쓰기 제한 시 파일 작업을 유지하고 차단 사실 보고; 다른 경로로 권한 우회하지 않는다.

### Task 2: 보존 가능한 파일 추출

**Files:** extract.py; tests/test_extract.py와 합성 fixtures.
**Interfaces:** extract_file(Path) -> Extraction을 제공하고 Item.extracted_text에 쓸 결과를 반환한다.

- [ ] 실패 테스트 작성: UTF-8 한글 코드/텍스트 본문 보존, 텍스트 PDF와 DOCX에서 문장 추출, 이미지/스캔 PDF의 OCR 어댑터 호출, Tesseract 미설치·암호 PDF·손상 DOCX는 `review_required`와 이유 반환. 미지원 DOC/HWP/오디오는 실행하지 않음. 확장자와 실제 형식 불일치, 큰 파일도 원본은 변경되지 않음.
- [ ] `python -m pytest tests/test_extract.py -q`로 실패 확인.
- [ ] 추출기와 선택 OCR 구현. PDF 페이지 수·처리 시간 제한, 임시 파일 정리, 파일명 경로 이탈 방지. 실패를 빈 텍스트 성공으로 기록하지 않는다.
- [ ] 같은 명령 PASS 확인 후 해당 변경 커밋.

### Task 3: ALT 공개 공유 노트 수집

**Files:** alt.py; tests/test_alt.py; fixtures/alt_shared.html(사용자 본문 대신 합성 전사).
**Interfaces:** fetch_note(url) -> AltNote. 결과는 Item으로 변환해 공통 처리한다.

- [ ] 실패 테스트 작성: `/note/<uuid>`와 `/ko/note/<uuid>`는 같은 ID, 제목·시간·전사 보존, 로그인/오류 페이지만 있으면 실패. 404/타임아웃/다른 호스트 리다이렉트/localhost URL은 자료 성공으로 처리하지 않음.
- [ ] `python -m pytest tests/test_alt.py -q` 실패 확인.
- [ ] ALT 공식 도메인·허용 경로만 수집하는 추출기 구현. 연결 시간/응답 크기 제한 및 리다이렉트마다 호스트 검증. 사용자가 준 실제 링크로 읽기 전용 실험을 수행하고 자동 추출 성공 여부 기록. 정적 HTML 실패 시 공개 렌더링 텍스트 어댑터 사용 가능성을 시험; 제한 우회 없이 실패로 끝나면 TXT 첨부 대안 안내.
- [ ] 같은 테스트 PASS 및 실제 링크 결과 확인. 원문을 Git에 넣지 않고 커밋.

### Task 4: 확인 결과로 학습하는 분류기

**Files:** learning.py; tests/test_learning.py.
**Interfaces:** train/predict 인터페이스를 제공. Store.record_feedback만 확정 학습 데이터로 사용.

- [ ] 실패 테스트 작성: 무학습 상태는 확인 대기, 한 클래스만 존재해도 충돌 없음, 사용자 수정은 다음 학습에 반영, 자동 예측은 예시 수 증가 없음, 새 채널 추가 후 재학습 가능, 드문 클래스는 자동 이동 금지, C++와 Python 태그 구분.
- [ ] `python -m pytest tests/test_learning.py -q` 실패 확인.
- [ ] 문자 n-gram TF-IDF와 로지스틱 분류를 기본으로 구현, 확정 자료만으로 분리 평가. 사용자 검증 표본이 부족한 분류는 잠금. 기본 자동화 후보 기준은 채널별 검증 정밀도 95% 이상, 검증 예시 20개 이상, 예측 점수 0.90 이상; 이는 안전한 시작값이며 테스트 자료로 정확도 보장을 주장하지 않음. 초기 채널 단서로 추천은 가능하지만 자동화 통과 근거로 쓰지 않음. 태그는 확정 사전과 본문 키워드 매칭으로 추천.
- [ ] 테스트 PASS 확인. 모델 버전과 평가 결과 저장, 손상/구버전 모델 로드 시 확인 대기로 폴백. 커밋.

### Task 5: 기존 기록 전체 색인과 재개

**Files:** discord_gateway.py의 읽기 부분, indexing.py; tests/test_indexing.py.
**Interfaces:** index_history 및 DiscordGateway.history/download 제공, Store와 추출기 소비.

- [ ] 실패 테스트 작성: Deleted User 포함 작성자 무관 색인, 허용 채널 외 접근 없음, 과거 2페이지 수집 중 중단 후 누락 없이 재개, 중복 1건, 봇/정리/확인 채널 재수집 제외, 파일 실패 기록, 기존 글 publish/delete 호출 0회. 권한 철회는 채널별 실패 기록 후 다른 채널 진행.
- [ ] `python -m pytest tests/test_indexing.py -q` 실패 확인.
- [ ] 현재 Discord 공식 문서로 history/첨부 다운로드/Message Content 설정·권한·요청 제한 확인 후 읽기 어댑터 구현. 체크포인트는 처리 트랜잭션 후 저장, 파일 상한·총량 검사. 초기 통계와 미분석 사유 보고를 구현.
- [ ] 테스트 PASS 확인 후 커밋. 실제 전체 색인은 봇 설정 뒤 실행하며 색인과 자동 이동을 연결하지 않음.

### Task 6: 검증 후 삭제하는 정리 상태 머신

**Files:** organize.py, discord_gateway.py의 게시/검증/삭제 부분; tests/test_organize.py.
**Interfaces:** process_job은 상태 문자열 반환. 상태: received, extracted, review_pending, ready, publishing, published, verified, deleting, done, failed. 삭제 실패는 deleting에서 재시도.

- [ ] 실패 테스트 작성: 보관 파일 SHA 불일치·첨부 상한·디스크 부족·게시 일부 실패는 원본 삭제 0회; 완전 검증 후 삭제 1회; 게시 직후 크래시 재개 시 reconcile로 중복 방지; 삭제 직후 크래시는 원본 부재를 완료로 수렴; 검증 후 목적지 삭제 시 다시 보존 실패 처리; 같은 메시지 중복 이벤트는 작업 1건.
- [ ] `python -m pytest tests/test_organize.py -q` 실패 확인.
- [ ] 상태 전이와 작업별 잠금/게시 식별 마커 구현. 긴 본문은 Unicode/코드블록을 고려해 분할하고 전체 본문 파일도 보관. Discord 첨부를 다시 다운로드해 해시 확인. 원작성자·원래 날짜 표시, 멘션 자동 실행 방지. 사용자가 명시 승인한 수집함만 삭제 가능. 불확실한 게시 결과는 확인 대기이며 파괴적 재시도 금지.
- [ ] 테스트 PASS 확인 후 커밋.

### Task 7: 소유자 확인·검색·실행 UI

**Files:** review.py, commands.py, app.py, scripts/start.ps1, README.md; tests/test_commands.py, tests/test_review.py, tests/test_recovery.py.
**Interfaces:** /검색(query), /태그(tags), /색인, /학습, /상태, /재수집(note_id), /백업, /태그별칭 명령. DB job ID를 영속 UI 식별자로 사용. 확인·새 채널·태그 변경은 소유자만 가능.

- [ ] 실패 테스트 작성: 다른 사용자/서버의 승인 거부, 재시작 후 기존 확인 버튼 유효, 이중 클릭 한 번만 처리, 삭제 채널 선택 시 보류, 새 채널 승인 없이 생성 0회, 복수 태그 결과·페이지 넘김·원문 링크, 역할/멘션 포함 제목 무해화. 오프라인 동안 쌓인 수집함 자료 재개 및 백업 복원 후 검색·학습 결과 일치.
- [ ] `python -m pytest tests/test_commands.py tests/test_review.py tests/test_recovery.py -q` 실패 확인.
- [ ] UI·명령·부팅 재개·SQLite backup API를 구현. Windows 실행 안내에 Python/선택 OCR 설치, 봇 생성 및 필요한 권한, 로컬 설정, 절전 설정 안내, 지원 파일 목록, 데이터 백업/복원, ALT 실패 대안 포함. 토큰 입력은 사용자가 로컬에서 하며 채팅/Git에 요구하지 않음. 복원은 앱 종료 상태에서 수행.
- [ ] 전체 `python -m pytest -q`와 `python -m compileall src` PASS 확인. 설정 예시로 토큰 없이 로컬 진단 명령 동작 확인. 실제 봇 연결 시 사용자가 지정한 테스트 수집함에서 임시 자료로 전체 흐름 검증 후 기존 채널 읽기 색인 실행. 라이브 검증 미수행은 명시.
- [ ] 변경 파일만 커밋하고 설정된 원격 저장소로 push. 저장소 미지정 또는 권한 차단은 업로드 성공으로 보고하지 않음.

## Self-review

설계의 기존 자료 보존, 학습 피드백, 파일 추출, ALT, 키워드/태그 검색, 안전한 오려두기, 재시작, 소유자 권한, 백업 및 무료 실행을 Tasks 1–7에 매핑했다. 공통 데이터 모델과 인터페이스 이름을 위에서 고정했고 외부 서비스 호출은 어댑터로 분리했다. 실제 Discord와 ALT의 동작은 가짜 어댑터 테스트와 별도 라이브 검증으로 구분한다. 문서만 작성한 상태이며 제품 코드는 아직 없다.
