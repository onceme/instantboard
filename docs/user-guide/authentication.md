# 认证与登录

## SSO 认证

支持 5 种主流单点登录方式，**默认仅启用 Google 和 GitHub**，其他提供商可通过环境变量按需启用。

| 提供商 | 协议 | 默认启用 | 配置要求 |
|--------|------|:--------:|---------|
| **Google** | OAuth 2.0 | ✅ | Google Cloud Console 创建 OAuth Client |
| **GitHub** | OAuth 2.0 | ✅ | GitHub Settings → Developer → OAuth App |
| **Azure AD** | OpenID Connect | ❌ | Azure Portal 注册应用 |
| **Apple** | Sign-In with Apple | ❌ | Apple Developer 创建 Service ID + ES256 密钥 |
| **Facebook** | OAuth 2.0 | ❌ | Facebook App Dashboard 创建应用 |

> 所有提供商的代码实现均已保留，通过 `ENABLED_SSO_PROVIDERS` 环境变量控制启用/禁用，这是**配置驱动**而非代码删除。

**SSO 配置示例**：

```bash
# .env 文件
# 默认只启用 Google 和 GitHub
ENABLED_SSO_PROVIDERS=google,github

# 启用 Azure AD
ENABLED_SSO_PROVIDERS=google,github,azure_ad

# 启用所有提供商
ENABLED_SSO_PROVIDERS=google,github,azure_ad,apple,facebook
```

**查询已启用的提供商**（前端可用此接口动态显示登录按钮）：

```bash
curl http://localhost:8000/api/v1/auth/sso/providers
# {"enabled_providers": ["google", "github"]}
```

**认证架构**：
- JWT 双 Token 方案：Access Token (1h) + Refresh Token (7d)
- 纯 Bearer Token 认证，后端**不设置任何 Cookie**（无 `Set-Cookie`）
- Access Token：存前端 localStorage，通过 `Authorization: Bearer <token>` 请求头传递
- Refresh Token：同样存前端 localStorage，调用 `POST /api/v1/auth/refresh` 时以 JSON body 提交
- 单次使用轮换：每次刷新后旧 Refresh Token 立即进入 Redis 黑名单，返回新的 token 对
- Redis Token 黑名单（按 jti 记录，TTL 为 token 剩余有效期；登出时 access + refresh 均失效）
- SSE 连接复用 Access Token 作为 `token` query 参数（短时效，随 Access Token 过期）

> ⚠️ 安全提示：两个 token 均存于 localStorage，页面一旦被 XSS 注入，token 可被脚本窃取。
> 现有缓解：Vue 默认转义、Access Token 短时效、Refresh 轮换 + 黑名单、
> 本地管理员入口 `/ibadmin` 会话隔离。完整风险评估见
> [安全设计](../dev-guide/design/security.md) §3.2 / §3.6。
>
> ⚠️ **未实现**：规划中的 XSS 缓解"DOMPurify"与"CSP"尚未落地——DOMPurify 不在前端依赖中，全站也没有任何 Content-Security-Policy 响应头（现有安全响应头仅 X-Frame-Options / X-Content-Type-Options / X-XSS-Protection / Referrer-Policy）。

**角色权限**：

| 角色 | 权限 |
|------|------|
| `admin` | 管理租户内用户/分类/数据源 + 所有功能 |
| `member` | 使用功能 + 管理个人自选列表 |
| `viewer` | 仅查看 |

各提供商的凭据变量说明见[配置说明](configuration.md)；生产环境的 SSO 配置步骤见[生产部署](deployment.md)。

## 本地管理员登录 (/ibadmin)

当 SSO 提供商不可用或未配置时（例如私有化部署没有第三方 OAuth 凭据），可通过本地管理员入口 `/ibadmin` 登录，对应后端接口 `POST /api/v1/auth/admin/login`。

**用途**：运维兜底的管理入口。登录成功后获得与 SSO 相同结构的 JWT 双 Token（`role=admin`，归属 system 租户），可访问监控仪表盘等管理功能。

**配置方式**（`.env`）：

| 变量 | 说明 |
|------|------|
| `ADMIN_EMAIL` | 允许登录的管理员邮箱 |
| `ADMIN_PASSWORD_HASH` | 管理员密码的 bcrypt 哈希（**只存哈希，不存明文**） |
| `ADMIN_PASSWORD` | 明文密码便捷项，**仅非生产环境生效**（启动时自动哈希并告警）；生产环境禁止使用，必须配置 `ADMIN_PASSWORD_HASH` |

两者都配置时本地管理员登录才会启用（`admin_login_enabled`）；未配置时接口返回 `503 ADMIN_LOGIN_DISABLED`。

**生成密码哈希**：

```bash
make gen-admin-hash PASS='你的密码'
# 或交互式（不留明文到 shell 历史）：
make gen-admin-hash
```

**与普通用户的隔离语义**（详见 [管理员登录设计](../dev-guide/design/admin-login.md)）：

- 本地管理员与 SSO 用户按**登录入口隔离**，是 `users` 表中互不关联的记录（`sso_provider='local'` vs 具体提供商；system 租户 vs default 租户），**即使邮箱相同也不合并**；
- SSO 登录的邮箱回退匹配**仅限 default 租户且排除 `local` 记录**，任何 SSO 登录都无法接管管理员账号；
- `users` 表**不含密码字段**，校验对象是环境变量中的哈希；
- 撤销方式：从 `.env` 删除 `ADMIN_EMAIL` / `ADMIN_PASSWORD_HASH` 并重启，存量本地会话将无法续期（refresh kill-switch）。
