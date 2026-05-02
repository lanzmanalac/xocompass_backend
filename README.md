### Auth & Admin Console Configuration (Phase 0+)

Starting with the Admin Console feature, the backend requires the following
environment variables in addition to `DATABASE_URL` and `CORS_ALLOWED_ORIGINS`:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `JWT_SECRET_KEY` | **yes** | — (boot fails) | HMAC signing key for access tokens. Generate with `openssl rand -hex 32`. Rotate quarterly. |
| `JWT_ALGORITHM` | no | `HS256` | JWT signature algorithm. Do not change unless you know why. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | `15` | Access-token TTL. Lower = safer; higher = fewer refresh round-trips. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | no | `7` | Refresh-token TTL. Rotated on every use. |
| `INVITE_TOKEN_EXPIRE_HOURS` | no | `72` | Lifetime of a pending invitation link. |
| `FRONTEND_BASE_URL` | **yes** | — (admin invites fail) | Used to build the invite URL returned to admins. |
| `ENVIRONMENT` | no | `development` | One of `development`, `staging`, `production`. Gates debug routes. |

**Generating a JWT secret:**

```bash
openssl rand -hex 32
```

**On Google Cloud Run**, every secret listed above (especially `JWT_SECRET_KEY`)
must be stored in **Secret Manager** and mounted as an environment variable on
the service revision — never baked into the container image, never set via
plaintext `--set-env-vars`. The Cloud Run service account needs the
`roles/secretmanager.secretAccessor` IAM role on each secret.

Example mount (gcloud CLI):

```bash
gcloud run services update xocompass-backend \
  --region=asia-southeast1 \
  --update-secrets=JWT_SECRET_KEY=xocompass-jwt-secret:latest
```