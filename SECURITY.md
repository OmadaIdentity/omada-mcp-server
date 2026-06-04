# Security Policy

## Reporting a Vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in this project, please report it privately:

- **Email:** security@omada.net
- **Subject:** `[MCP Server] Security Vulnerability Report`

Please include:
- A description of the vulnerability and its potential impact
- Steps to reproduce or proof-of-concept (if available)
- Any suggested mitigations

You will receive an acknowledgement within **5 business days**. We aim to provide
a resolution or remediation plan within **30 days** for confirmed vulnerabilities.

## Scope

This policy covers the `omada-mcp-server` codebase. It does not cover:
- The Omada Identity platform itself
- Third-party dependencies (report those to the respective maintainers)
- Issues in your own Azure AD / Entra ID configuration

## Supported Versions

This is a **Technology Preview** release. Security fixes are applied to the
latest version only. We recommend always running the most recent version.

## Security Design Notes

- Authentication uses OAuth 2.0 Authorization Code + PKCE (public client — no client secret).
- Tokens are encrypted at rest using Windows DPAPI and never exposed in tool responses or logs.
- All API communication requires HTTPS.
- No credentials are stored in the repository; configuration is environment-variable based.
