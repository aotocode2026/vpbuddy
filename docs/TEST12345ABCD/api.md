# 接入接口 (草案)

- **SSO 发起登录**: `GET /auth/sso/login` 302 跳 IdP 授权页
- **SSO 回调**: `GET /auth/sso/callback?code=...` 校验后回写会话
- **微信二维码**: `GET /auth/wechat/qrcode` 返回二维码 URL 与 ticket
- **微信回调**: `GET /auth/wechat/callback?code=...` 拉取用户信息并回写会话
- **当前会话**: `GET /auth/me` 返回用户 ID 与登录方式 (sso / wechat / password)
