#!/bin/bash
# Finder 더블클릭용 실행 스크립트 — 터미널이 열리고 디시콘 저장기를 실행합니다.
set -u

cd "$(dirname "$0")"

fail() {
  echo ""
  echo "❌ 실패: $1"
  echo "이 창을 닫고 다시 시도하세요."
  read -p "종료하려면 Enter를 누르세요... " _dummy
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail "python3을 찾을 수 없습니다. https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치하세요."

if [ ! -x ".venv/bin/python" ]; then
  echo "📦 처음 실행이라 가상환경(.venv)을 만듭니다..."
  python3 -m venv .venv || fail "가상환경 생성 실패"
  NEED_INSTALL=1
else
  NEED_INSTALL=0
fi

VENV_PY=".venv/bin/python"

if [ "$NEED_INSTALL" -eq 0 ]; then
  if ! "$VENV_PY" -c "import PySide6, dccon" >/dev/null 2>&1; then
    NEED_INSTALL=1
  fi
fi

if [ "$NEED_INSTALL" -eq 1 ]; then
  echo "📥 필요한 패키지를 설치합니다 (PySide6 등, 처음 한 번은 몇 분 걸릴 수 있음)..."
  "$VENV_PY" -m pip install -e "." || fail "패키지 설치 실패"
else
  echo "✅ 환경 OK, 바로 실행합니다."
fi

echo "🚀 디시콘 저장기를 실행합니다..."
"$VENV_PY" -m dccon.app
STATUS=$?

if [ $STATUS -ne 0 ]; then
  fail "앱이 오류 코드 $STATUS 로 종료됨"
fi

echo ""
echo "👋 앱이 종료되었습니다. 이 창은 닫아도 됩니다."
