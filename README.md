# 스캔 번역기 v2

vFlat으로 스캔한 PDF를 Google Drive 폴더에 올리면, 클라우드에서 한국어로 번역해 같은 Drive에 넣어 줍니다. 노트북을 켤 필요가 없습니다.

```
휴대폰 vFlat ──공유──▶ Drive/Reading/01_Scan
                              │  (5분마다 확인)
                        Apps Script ──실행──▶ GitHub Actions
                                                 │ ① Claude가 페이지 이미지를 직접 읽음 (손글씨·밑줄 제거)
                                                 │ ② 책 전체 용어집 생성
                                                 │ ③ 페이지별 번역 (병렬)
                                                 │ ④ 원문과 1:1 페이지로 조판
Drive/Reading/02_Translated/○○_KO.pdf ◀──────────┘   + 완료 메일
```

## 무엇이 달라졌나

| | 이전 | 지금 |
|---|---|---|
| 실행 | 노트북으로 옮기고 PowerShell 실행 | 폰에서 Drive에 공유하면 끝 |
| 글자 인식 | Tesseract (손글씨가 섞이면 깨짐) | Claude 비전 — 인쇄 활자만 골라 읽음 |
| 용어 일관성 | 고정 용어 5개 + 앞 페이지 끝부분 | 책 전체를 먼저 읽고 용어집·고유명사 표기 자동 생성 |
| 페이지 경계 | 잘린 문장이 어색하게 나뉨 | 잘린 문장은 시작 페이지에서 완결 번역 |
| 조판 | 맑은 고딕, 단일 스타일 | 나눔명조, 제목·인용·각주 구분, 강조 표시, PDF 목차(북마크) |
| 페이지 대응 | 넘치면 페이지 추가 | 항상 원문 n쪽 = 번역 n쪽 (넘치면 그 쪽만 글자 축소) |
| 중단 시 | 처음부터 | 끝난 페이지는 건너뛰고 이어서 |

참고문헌·색인 페이지는 원문 페이지가 그대로 들어가고, 그림 페이지는 원문 그림 아래에 번역 캡션이 붙습니다. 하단에는 원서에 인쇄된 쪽번호가 표시됩니다.

---

## 처음 한 번 설정 (약 20분)

### 0. API 키 새로 발급
이전 코드에 키가 그대로 들어 있었으므로 [Console](https://console.anthropic.com/settings/keys)에서 **기존 키를 삭제**하고 새 키를 만드세요. 새 키는 아래 5단계에서 GitHub Secrets에만 넣습니다.

### 1. Drive 폴더 만들기
**내 드라이브** 안에 만드세요 (기존 `다른 컴퓨터` 아래 폴더는 폰에서 업로드가 안 될 수 있습니다).
```
내 드라이브/Reading/01_Scan
내 드라이브/Reading/02_Translated
```
각 폴더를 브라우저에서 열면 주소가 `drive.google.com/drive/folders/1AbC...` 입니다. `folders/` 뒤의 값이 **폴더 ID**입니다. 두 개 모두 메모해 두세요.

### 2. GitHub 저장소 만들기
1. GitHub에서 **Private** 저장소 `scan-translator` 생성
2. **Add file → Upload files**로 이 폴더의 파일을 올립니다 (`translator/` 폴더, `job.py`, `local_run.py`, `requirements.txt`, `.gitignore`, `README.md`)
3. 워크플로 파일은 Windows에서 숨김 폴더 문제가 있으니 웹에서 직접 만듭니다: **Add file → Create new file** → 파일 이름 칸에 `.github/workflows/translate.yml` 입력 → 내용 붙여넣기 → Commit

### 3. GitHub 토큰 (Apps Script가 번역을 실행할 때 사용)
Settings → Developer settings → **Fine-grained tokens** → Generate new token
- Repository access: **Only select repositories** → `scan-translator`
- Permissions → Repository permissions → **Contents: Read and write**
- 만료일은 최대로. 만료되면 번역 요청이 실패하니 달력에 표시해 두세요.

### 4. Apps Script
1. [script.google.com](https://script.google.com) → 새 프로젝트 (이름: `스캔 번역기`)
2. 기본 `Code.gs` 내용을 지우고 `apps-script/Code.gs` 붙여넣기
3. ⚙️ 프로젝트 설정 → **"appsscript.json" 매니페스트 파일 표시** 체크 → 편집기에 나타난 `appsscript.json`에 `apps-script/appsscript.json` 내용 붙여넣기
4. `Code.gs` 맨 위 `CONFIG`에 1단계 폴더 ID 두 개 입력 (저장소 이름이 다르면 `GITHUB_REPO`도)
5. ⚙️ 프로젝트 설정 → **스크립트 속성** → `GITHUB_TOKEN` = 3단계 토큰
6. 편집기 위쪽에서 함수 `setup` 선택 → ▶ 실행 → 권한 승인 (“확인되지 않은 앱” 경고 → 고급 → 이동)
7. 실행 로그에 나온 **긴 비밀키**를 복사 (GAS_SECRET)
8. **배포 → 새 배포** → 유형: 웹 앱 → 실행 사용자: **나** / 액세스 권한: **모든 사용자** → 배포 → **웹 앱 URL** 복사 (GAS_URL)

> 코드를 고친 뒤에는 **배포 관리 → ✏️ → 버전: 새 버전**으로 다시 배포해야 반영됩니다.

### 5. GitHub Secrets
저장소 → Settings → Secrets and variables → Actions → **New repository secret** 세 개:

| 이름 | 값 |
|---|---|
| `ANTHROPIC_API_KEY` | 0단계 새 키 |
| `GAS_URL` | 4-8 웹 앱 URL |
| `GAS_SECRET` | 4-7 비밀키 |

### 6. 테스트
몇 쪽짜리 짧은 PDF를 폰에서 `01_Scan`에 올리고 5~10분 기다립니다. GitHub 저장소의 **Actions** 탭에서 진행 상황을 볼 수 있고, 끝나면 메일이 옵니다.

---

## 매일 쓰는 법

1. vFlat에서 스캔 → PDF로 내보내기 → **공유 → Drive → Reading/01_Scan**
   - 파일 이름이 번역본 이름이 됩니다 (`Fleury_ch3.pdf` → `Fleury_ch3_KO.pdf`). 올리기 전에 이름을 정해 두면 편합니다.
2. 완료 메일의 링크, 또는 태블릿 Drive 앱의 `02_Translated`에서 열기

## 용어 고치기
- `Reading/glossary.txt` — 모든 책에 적용되는 고정 용어. `원어 = 번역어` 한 줄씩.
- 책마다 자동 생성된 용어집은 `02_Translated/_work/<이름>_용어집.txt`에 저장됩니다. 마음에 안 드는 번역어를 `glossary.txt`에 추가하고 다시 번역하면 반영됩니다 (용어가 바뀌면 전사는 재사용하고 번역만 다시 합니다).

## 다시 번역하기
- Apps Script에서 `retranslate` 함수의 `NAME`에 파일 이름을 넣고 실행
- 완전히 처음부터 하려면 `02_Translated/_work/<이름>.work.json`을 지운 뒤 실행
- 실패 메일을 받았다면 메일의 링크 → **Re-run jobs** (끝난 페이지부터 이어서 처리)

## 노트북에서 직접 테스트 (선택)
```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python local_run.py "C:\경로\스캔.pdf" --pages 1-5
```
같은 폴더에 `스캔_p1-5_KO.pdf`가 생기고, 중간 결과는 `_work\`에 남습니다.

## 조정할 수 있는 것 (`translator/config.py`)
- `MODEL` — 기본은 최신 Sonnet. 특히 어려운 책만 Opus로 돌리고 싶으면 여기서 변경
- `WORKERS` — 동시 처리 페이지 수 (속도 제한 오류가 잦으면 2~3으로)
- `BASE_FONT_PT`, `LINE_HEIGHT`, 여백 — 번역본 글자 크기와 행간
- 강조(원문 이탤릭)는 적갈색 글자로 표시됩니다 (`render.py`의 `i { color }`)

## 문제 해결
- **Actions가 시작되지 않음** → Apps Script 실행 기록에서 `checkNewScans` 오류 확인. `GitHub 실행 요청 실패 401/404`면 토큰 권한·만료·저장소 이름 확인
- **`Apps Script 응답이 JSON이 아닙니다`** → 웹 앱 액세스 권한이 “모든 사용자”인지, 새 버전으로 배포했는지 확인
- **`Drive ... 403`** → `appsscript.json`이 제대로 들어갔는지 확인 후 `setup` 다시 실행·재배포
- **`credit balance` / `번역 실패 페이지가 너무 많습니다`** → API 크레딧 확인 후 Re-run
