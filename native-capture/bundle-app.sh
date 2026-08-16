#!/bin/bash
# 组装 MeetingEZ Capture.app 并签名。
#
# 用法:
#   ./bundle-app.sh                     # ad-hoc 签名（每次构建身份变化，重编译后需重新授权屏幕录制）
#   MEETINGEZ_SIGN_IDENTITY="MeetingEZ" ./bundle-app.sh
#                                       # 用指定证书签名（身份稳定，重编译不重新要权限）
#
# 一次性创建自签名证书（推荐，Keychain Access → 证书助理 → 创建证书）：
#   名称: MeetingEZ、类型: 代码签名、勾选"让我覆盖这些默认值" → 签名算法 SHA-256，
#   并手动勾选"代码签名"扩展。之后用 MEETINGEZ_SIGN_IDENTITY="MeetingEZ" 运行本脚本。
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="MeetingEZ Capture"
BINARY="meetingez-capture"
OUT_DIR="build"
APP_BUNDLE="${OUT_DIR}/${APP_NAME}.app"

echo "==> swift build -c release"
swift build -c release

echo "==> 组装 ${APP_BUNDLE}"
rm -rf "${APP_BUNDLE}"
mkdir -p "${APP_BUNDLE}/Contents/MacOS" "${APP_BUNDLE}/Contents/Resources"
cp ".build/release/${BINARY}" "${APP_BUNDLE}/Contents/MacOS/${BINARY}"
cp Info.plist "${APP_BUNDLE}/Contents/Info.plist"

IDENTITY="${MEETINGEZ_SIGN_IDENTITY:--}"
echo "==> codesign (identity: ${IDENTITY})"
codesign --force --sign "${IDENTITY}" "${APP_BUNDLE}"
codesign --verify --verbose=1 "${APP_BUNDLE}"

cat <<TIP

完成: ${APP_BUNDLE}

建议移动到固定路径后使用（TCC 授权与路径/签名身份绑定，路径固定可避免重复授权）:
  mv "${APP_BUNDLE}" ~/Applications/
  open ~/Applications/"${APP_NAME}.app"

首次运行需要在 系统设置 → 隐私与安全性 → 屏幕录制 中授权。
TIP
