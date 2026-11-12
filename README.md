# Meta 광고 소재 분석 에이전트

Meta 광고를 **이미지 소재 단위**로 모아서 Claude가 각 이미지에 라벨을 붙이고,
라벨별 성과 비교와 소재별 진단을 HTML 대시보드 한 장으로 만듭니다.

| 프로필 (`CREATIVE_PROFILE`) | 핵심 전환 | 라벨 축 | 진단 |
|---|---|---|---|
| `b2b` (기본) | 랜딩페이지 조회 · 조회당 비용 · 랜딩 도달률 | 메시지 앵글 · 타깃 페르소나 · 포맷 · 텍스트량 · 톤 | 증액 / 감액검토 / 클릭률개선 / 랜딩도달개선 / 모니터링 |
| `commerce` | 구매 매출 · ROAS | 구도 · 배경 · 텍스트량 · 할인표기 · 톤 · 모델 | 증액 / 감액검토 / 클릭률개선 / 객단가검토 / 모니터링 |

- **랜딩 도달률** = 랜딩페이지 조회 ÷ 링크 클릭. 낮으면 랜딩 로딩 속도, 광고와 랜딩의 메시지 불일치, 실수 클릭을 의심합니다.
- 핵심 전환을 가입 등으로 바꾸려면 `.env`에 `META_CONVERSION_ACTION=complete_registration`.
  `python -m creative_agent check`가 계정에서 잡히는 이벤트 목록을 보여줍니다.
- 메타의 원본 액션 데이터를 그대로 저장하므로, 전환 이벤트를 바꿔도 다시 수집할 필요가 없습니다(`run --skip-collect`).

```
① Meta Insights API (광고×일 단위 성과)  ─┐
② Meta Ads API (소재 이미지 다운로드)     ─┤→ SQLite(data/creative.db)
③ Claude 비전 라벨링 (새 이미지만, 해시 캐시) ┘
④ 집계·진단 (analyze.py)  →  ⑤ Claude 코멘트(발견·다음 액션)  →  out/latest.html
```

## 빠른 시작

```bash
pip install -r requirements.txt
python -m creative_agent demo          # 가짜 데이터로 대시보드 미리보기 (API 키 불필요)
```

## 실제 계정 연결

1. `.env.example` → `.env` 복사 후 입력
   - `META_ACCESS_TOKEN`: 비즈니스 관리자 › 시스템 사용자 › 토큰 생성 (`ads_read` 권한)
   - `META_AD_ACCOUNT_ID`: `act_` 로 시작
   - `META_PURCHASE_ACTION`: 구매로 셀 action_type (기본 `omni_purchase`, 없으면 `purchase` 순으로 사용)
   - `ANTHROPIC_API_KEY`
2. 실행
   ```bash
   python -m creative_agent run --days 7
   python -m creative_agent run --since 2026-11-01 --until 2026-11-10
   python -m creative_agent run --skip-collect --no-ai   # DB 데이터로 표만 다시 생성
   ```
3. 매일 갱신 (cron 예시, 매일 오전 7:10)
   ```
   10 7 * * * cd /path/to/meta-creative-agent && python -m creative_agent run --days 14 >> run.log 2>&1
   ```

## 커스터마이즈 (`creative_agent/config.py`)

- `PROFILES`: 프로필별 라벨 축·값(`dimensions`), 판단 기준 문구(`guide`), 목록 태그(`tags`).
  라벨은 프로필별로 따로 저장되므로 축 값을 바꿨다면 `data/creative.db`의 `labels` 테이블을 비우고 다시 실행하세요.
- `DIAGNOSIS`: 진단 임계값(계정 평균 대비 배수). 최소 광고비는 `.env`의 `MIN_SPEND`(기본 10,000원).

## 참고

- 카탈로그(DPA)·캐러셀처럼 고정 이미지가 하나로 정해지지 않는 광고는 소재 분석에서 빠집니다. 영상은 썸네일 기준으로 라벨링합니다.
- 라벨은 이미지 해시로 캐시되므로 같은 이미지를 여러 광고에 써도 Claude 호출은 1번입니다.
- 모델은 `CLAUDE_MODEL`(기본 `claude-opus-5-5`), 라벨링 effort는 `LABEL_EFFORT`(기본 low), 코멘트는 `INSIGHT_EFFORT`(기본 high).
  안전 분류기 오탐에 대비해 서버측 fallback(`fallbacks="default"`)을 켜 두었습니다.

## 매일 자동 실행 + 친구/팀과 공유 (GitHub Actions + Vercel)

PC를 켜 두지 않아도 GitHub 서버가 매일 아침(한국시간 5:47경) 수집 → 라벨링 → 분석을 돌리고,
비밀번호로 암호화된 대시보드를 Vercel에 배포합니다. 보는 사람은 주소 + 비밀번호만 있으면 됩니다.

1. 이 코드를 GitHub **비공개(Private)** 저장소에 올립니다.
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret**에 5개를 등록합니다.

   | 이름 | 값 |
   |---|---|
   | `META_ACCESS_TOKEN` | 메타 시스템 사용자 토큰 |
   | `META_AD_ACCOUNT_ID` | `act_` + 광고계정 숫자 |
   | `ANTHROPIC_API_KEY` | Claude API 키 |
   | `VERCEL_TOKEN` | vercel.com → Account Settings → Tokens 에서 만든 토큰 |
   | `DASHBOARD_PASSWORD` | 대시보드를 열 때 쓸 비밀번호 (친구에게 알려줄 것) |

3. **Actions** 탭 → `매일 소재 분석` → **Run workflow**로 첫 실행을 합니다.
   마지막 단계 로그의 `Production: https://...vercel.app` 주소가 공유할 주소입니다.

- 루트의 `vercel.json`은 버셀이 이 저장소를 직접 빌드하지 않도록 막습니다. 배포는 Actions가 완성된 대시보드(`site/`)만 올립니다.
  버셀에서 이 저장소를 Import해서 생긴 프로젝트는 필요 없으니 지워도 됩니다.
- 대시보드 데이터는 AES-256으로 암호화되어 배포되므로, 주소가 알려져도 비밀번호 없이는 볼 수 없습니다.
- 비밀번호를 바꾸려면 `DASHBOARD_PASSWORD` 시크릿만 바꾸고 다시 실행하면 됩니다.
- 선택: **Variables** 탭에 `VERCEL_PROJECT`(버셀 프로젝트 이름, 기본 `creative-dashboard`),
  `META_CONVERSION_ACTION`, `MIN_SPEND`를 넣어 설정을 바꿀 수 있습니다.
- 로컬에서 같은 사이트를 만들어 보려면: `DASHBOARD_PASSWORD=... python -m creative_agent run --site site`

## Amplitude 연동 (가입·체험·활성화)

`AMPLITUDE_API_KEY`, `AMPLITUDE_SECRET_KEY`(앰플리튜드 Settings → Projects → General)를 GitHub 시크릿(로컬은 `.env`)에 넣으면
대시보드에 **퍼널(링크 클릭 → 랜딩 → 가입 → 체험 → 활성화)**, 소재별 **가입·가입당 비용·활성화**, **유입 채널** 패널이 생깁니다.

| 설정 (GitHub Variables 또는 .env) | 기본값 | 설명 |
|---|---|---|
| `AMPLITUDE_REGION` | `us` | EU 데이터센터면 `eu` |
| `AMPLITUDE_SIGNUP_EVENT` | `sign_up` | 가입 이벤트 |
| `AMPLITUDE_TRIAL_EVENT` | `trial_started` | 체험 시작 이벤트 |
| `AMPLITUDE_ACTIVATION_EVENT` | `brand_filter_changed` | 무료체험 **후에** 해야 활성화로 치는 이벤트(브랜드 선택) |
| `AMPLITUDE_TRIAL_DAYS` | `5` | 무료체험 기간 — 이 안에 브랜드를 선택해야 활성화 |
| `AMPLITUDE_PAID_EVENT` | (없음) | 결제 완료 이벤트. 넣으면 '유료 전환' 단계가 생김 |
| `AMPLITUDE_PAID_WINDOW_DAYS` | `14` | 무료체험 시작 후 며칠 안에 결제해야 유료 전환으로 칠지 |
| `AMPLITUDE_AD_PROPERTY` | `gp:initial_utm_content` | 광고와 잇는 사용자 속성 |
| `AMPLITUDE_SOURCE_PROPERTY` | `gp:initial_utm_source` | 유입 채널 사용자 속성 |

- 소재별 연결은 메타 광고 URL 매개변수의 `utm_content`가 **광고 이름(`{{ad.name}}`, 현재 설정) 또는 광고 ID**일 때 됩니다.
- `utm_source`가 `lead_mail`(모바일 광고 → 'PC로 링크 받기' 메일 → PC 가입)인 가입은 메타 가입으로 집계합니다.
  다른 경로도 메타로 묶으려면 GitHub Variables에 `AMPLITUDE_META_SOURCES=lead_mail,다른값`.
- **활성화 = 무료체험 시작(`trial_started`) 후에 브랜드 선택(`brand_filter_changed`)까지 한 사용자**입니다.
  기준: 무료체험 시작 후 **5일(체험 기간)** 안에 브랜드를 선택. 유료 전환은 결제 이벤트를 지정하면 켜집니다.
  앰플리튜드 Funnel API(순서 있음)로, 그 기간에 무료체험을 시작한 사람을 기준으로 셉니다.
  무료체험 신청 수도 함께 보여줍니다.
- 같은 이름의 광고가 여럿이면 가입 수를 광고비 비중으로 나눠 붙입니다.

## 가입 계정별 활동

앰플리튜드 Export API로 최근 30일 원본 이벤트를 받아(이틀 지난 날은 `data/amp_export/`에 캐시) 가입 계정(user_id)마다
상태(체험 중 D-n · 이탈 위험 · 체험 종료 · 유료) · 오늘/어제 방문 · 최근 7일 방문일 · 유입 · 한 줄 요약을 보여줍니다.

- 이탈 위험 = 체험 기간 중인데 2일 이상 방문 없음
- 이메일은 전체로 표시합니다. 가리려면 GitHub Variables에 `SHOW_FULL_ACCOUNT=0`.
- 테스트·내부 계정은 저장소의 `exclude_accounts.txt`에 적으면 표에서 빠집니다(전체 이메일 / `앞글자***@도메인` / `@도메인`).
- 자동 실행은 하루 3번(한국시간 05:47 · 12:47 · 18:47). 앰플리튜드 원본 데이터는 1~2시간 늦게 들어옵니다.

## 갱신 주기와 가입 즉시 갱신

- 자동 실행: 한국시간 05:47, 그리고 09~21시 2시간마다(:47분). AI 코멘트는 하루 첫 실행 때 한 번만 만들고 같은 날은 재사용(`--ai-refresh`로 강제 생성).
- **가입·체험 시작 즉시 갱신**: 앰플리튜드 웹훅 → GitHub `repository_dispatch`(`amplitude_event`) → 약 4~5분 뒤 대시보드 반영.
  웹훅 본문의 `client_payload.user_id` 사용자는 원본 데이터 지연(1~2시간)을 기다리지 않고 User Activity API로 바로 보충합니다(3일간 유지).

웹훅 설정(앰플리튜드 Data → Destinations → Webhook):
- URL: `https://api.github.com/repos/chelseadoer-bot/fedit_mkt/dispatches` (POST)
- 헤더: `Authorization: Bearer <GitHub fine-grained 토큰>`, `Accept: application/vnd.github+json`
  - 토큰: GitHub → Settings → Developer settings → Fine-grained tokens, 저장소 `fedit_mkt`만, 권한 **Contents: Read and write**
- 보낼 이벤트: `sign_up`, `trial_started`
- 본문: `{"event_type": "amplitude_event", "client_payload": {"user_id": "<이벤트의 user_id>", "event": "<event_type>"}}`

## 콘텐츠 캘린더

대시보드 '콘텐츠 캘린더' 탭에서 채널(공식 인스타·스레드·광고·기타)별 콘텐츠를 날짜에 기획하고, 자동 갱신 때 성과를 붙입니다.

- 저장: 대시보드 → 버셀 서버리스 함수(`/api/calendar`) → 이 저장소의 `content/calendar.json` (GitHub API, 비밀번호 확인)
  - GitHub 시크릿 `CALENDAR_GH_TOKEN`: fine-grained 토큰, 저장소 `fedit_mkt`만, **Contents: Read and write**
- 성과 연결: 항목의 '링크'에 인스타·스레드 게시물 주소 또는 광고 이름(ID). 링크가 없으면 같은 날 같은 채널 게시물이 하나일 때 자동 연결
  - 인스타그램: 메타 시스템 사용자에 페이지·인스타 계정을 할당하고 토큰에 `instagram_basic`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement` 권한
    (기존 `META_ACCESS_TOKEN`을 그대로 쓰거나 `INSTAGRAM_ACCESS_TOKEN` 시크릿을 따로)
  - 스레드: `THREADS_ACCESS_TOKEN` 시크릿(장기 토큰, 60일) — 7일마다 자동 갱신해 다음 실행에 이어 씀
- 기획 없이 올린 게시물은 캘린더에 점선으로 보이고, 눌러서 바로 기획 항목으로 등록할 수 있습니다.
