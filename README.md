# DCCon Downloader for Mac

PySide6 + Qt WebEngine 기반 macOS용 디시콘 다운로더입니다.

- https://dccon.dcinside.com/ 전용 내장 브라우저
- 상세 레이어 감지 및 한 묶음 ZIP 저장
- 원본 PNG/GIF 보존, 순번 파일명, 최상위 폴더 구조

## 로그인

앱 안의 사이트에서 `로그인`을 누르면 디시인사이드 로그인 화면으로 이동합니다.
로그인·SSO 인증 및 디시콘 복귀 주소(`sign.dcinside.com`, `sso.dcinside.com`, `mall.dcinside.com`)를 허용하며,
허용된 새 창 링크도 같은 앱 화면에서 엽니다. 디시콘으로 돌아가려면 상단 `홈`을 누르세요.
그 외 외부 사이트 이동은 계속 차단합니다.

로그인 세션은 앱을 사용하는 동안만 유지됩니다. 앱을 종료하면 쿠키가 삭제되며,
Safari나 Chrome의 로그인 정보는 가져오지 않습니다.

## 내 디시콘 저장

`내 디시콘`에서 묶음을 클릭해 상세창을 열면 주소가 그대로여도 제목과 개수를 감지합니다.
로그인 후 `mall.dcinside.com`으로 돌아오면, 내부 프레임의 디시콘 본문을 같은 세션에서 직접 엽니다.
이때 앱 주소는 `dccon.dcinside.com`으로 바뀌며 로그인 쿠키는 그대로 유지됩니다.
하단 `현재 디시콘 ZIP 저장`을 눌러 현재 열린 묶음을 저장하세요.
다른 묶음을 열면 감지 정보가 바뀌며, 상세창을 닫거나 아직 로딩 중이면 저장 버튼이 비활성화됩니다.
목록 썸네일·대표 이미지·숨겨진 항목은 제외하고, 상세 목록의 순서와 반복 이미지는 유지합니다.

## 실행

Python 3.10 이상이 필요합니다. 저장소를 내려받은 뒤 프로젝트 폴더에서 실행하세요.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m dccon.app
```

## 테스트

```bash
pytest
```

## 패키징

```bash
pyside6-deploy --help
```

> Gatekeeper: 코드 서명/공증 없이 빌드 시 개인용 실행 빌드로 사용.
