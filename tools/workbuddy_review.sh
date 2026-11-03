#!/usr/bin/env bash
# workbuddy_review.sh — workbuddy 代审的 CLI 包装（Windows + Git Bash 实测）。
# 依赖: 官方独立版 CLI（npm i -g @tencent-ai/codebuddy-code）已扫码登录，
#       WorkBuddy 桌面端在线（提供 127.0.0.1:3425 模型网关，懒启动需等其就绪）。
# 积分: 走 Buddy AI 池（WorkBuddy/CodeBuddy 同账号共享）。
export NO_PROXY='*' no_proxy='*'          # 系统代理间歇抖动，必须绕过

# 挂载桌面版腾讯连接器（自动取最新版本；--plugin-dir 参数对 -p 生效）
WB_PLUGINS="$USERPROFILE/.workbuddy/plugins/cache/workbuddy-builtin"
ARGS=""
for p in tencent-docs-plugin workbuddy-docx workbuddy-pptx workbuddy-sheetagent; do
  V=$(ls -t "$WB_PLUGINS/$p" 2>/dev/null | head -1)
  [ -n "$V" ] && ARGS="$ARGS --plugin-dir $(cygpath -w "$WB_PLUGINS/$p/$V")"
done

exec codebuddy -p $ARGS "$@"
