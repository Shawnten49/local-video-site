# Security Policy

## Default administrator

An empty database initializes a first administrator:

```text
username: admin
password: 123456
```

The account is marked as requiring a password change. Until the password is
changed, only the account password page is available.

LAN mode rejects a still-pending default administrator unless the unsafe
override `LOCAL_VIDEO_ALLOW_DEFAULT_ADMIN_LAN=1` is explicitly enabled.

Change the password immediately after the first login. For shared deployments,
override both bootstrap values before starting the service.

## Runtime secrets

Never commit:

- `.env`
- databases
- browser profiles
- cookies
- logs
- deletion verification files
- media or generated thumbnails
- signing keys or certificates

Report security issues privately rather than opening a public issue.
