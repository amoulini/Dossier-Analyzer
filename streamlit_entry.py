"""Bootstrap Streamlit secrets from the environment, then exec the Streamlit server.

Cloud Run and other hosts often inject OAuth and GCP settings as env vars instead of mounting
``.streamlit/secrets.toml``. Streamlit's ``st.login`` still expects ``[auth]`` in that file, so
this script writes it (and optional ``[gcp]``) before starting Streamlit.

Auth (Google OIDC) — set these when using ``st.login`` without a secrets file:
  STREAMLIT_AUTH_REDIRECT_URI e.g. https://YOUR-SERVICE-XXXX.run.app/oauth2callback
  STREAMLIT_AUTH_COOKIE_SECRET  long random string (store in Secret Manager on Cloud Run)
  STREAMLIT_AUTH_CLIENT_ID      OAuth client ID from Google Cloud Console
  STREAMLIT_AUTH_CLIENT_SECRET OAuth client secret

Optional:
  STREAMLIT_AUTH_SERVER_METADATA_URL (default: Google well-known OpenID URL)

Fallback names if the STREAMLIT_AUTH_* vars are unset:
  AUTH_REDIRECT_URI, AUTH_COOKIE_SECRET, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

GCP section (optional; app also reads GCS_BUCKET_NAME from the environment directly):
  GCS_BUCKET_NAME, GCS_MAX_BATCH_UPLOAD_MB

If ``STREAMLIT_AUTH_CLIENT_ID`` / ``GOOGLE_CLIENT_ID`` is unset, no file is written and any
existing ``.streamlit/secrets.toml`` (e.g. local dev) is left unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _e(name: str, *fallbacks: str) -> str:
    for k in (name, *fallbacks):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def _toml_basic_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_secrets_toml(path: Path) -> None:
    redirect = _e(
        "STREAMLIT_AUTH_REDIRECT_URI",
        "AUTH_REDIRECT_URI",
    )
    cookie = _e(
        "STREAMLIT_AUTH_COOKIE_SECRET", 
        "AUTH_COOKIE_SECRET"
    )
    client_id = _e(
        "STREAMLIT_AUTH_CLIENT_ID",
        "GOOGLE_CLIENT_ID",
    )
    client_secret = _e(
        "STREAMLIT_AUTH_CLIENT_SECRET",
        "GOOGLE_CLIENT_SECRET",
    )
    meta = _e(
        "STREAMLIT_AUTH_SERVER_METADATA_URL",
    ) or "https://accounts.google.com/.well-known/openid-configuration"

    if not client_id:
        return

    missing = [n for n, v in [
        ("STREAMLIT_AUTH_REDIRECT_URI or AUTH_REDIRECT_URI", redirect),
        ("STREAMLIT_AUTH_COOKIE_SECRET or AUTH_COOKIE_SECRET", cookie),
        ("STREAMLIT_AUTH_CLIENT_ID or GOOGLE_CLIENT_ID", client_id),
        ("STREAMLIT_AUTH_CLIENT_SECRET or GOOGLE_CLIENT_SECRET", client_secret),
    ] if not v]
    if missing:
        sys.stderr.write(
            "streamlit_entry: OAuth enabled via client id but missing: "
            + ", ".join(missing)
            + "\n",
        )
        sys.exit(1)

    lines = [
        "# Generated at container start from environment variables — do not commit.",
        "[auth]",
        f"redirect_uri = {_toml_basic_str(redirect)}",
        f"cookie_secret = {_toml_basic_str(cookie)}",
        f"client_id = {_toml_basic_str(client_id)}",
        f"client_secret = {_toml_basic_str(client_secret)}",
        f"server_metadata_url = {_toml_basic_str(meta)}",
    ]

    bucket = _e("GCS_BUCKET_NAME")
    max_batch = (os.environ.get("GCS_MAX_BATCH_UPLOAD_MB") or "").strip()
    if bucket or max_batch:
        lines.append("")
        lines.append("[gcp]")
        if bucket:
            lines.append(f"bucket_name = {_toml_basic_str(bucket)}")
        if max_batch:
            try:
                lines.append(f"max_batch_upload_mb = {int(max_batch)}")
            except ValueError:
                pass

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    secrets_path = Path(os.environ.get("STREAMLIT_SECRETS_FILE", ".streamlit/secrets.toml"))
    if not secrets_path.is_absolute():
        secrets_path = Path.cwd() / secrets_path

    force = (os.environ.get("STREAMLIT_SECRETS_FROM_ENV") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    has_oauth_env = bool(_e("STREAMLIT_AUTH_CLIENT_ID", "GOOGLE_CLIENT_ID"))
    if force and not has_oauth_env:
        sys.stderr.write(
            "streamlit_entry: STREAMLIT_SECRETS_FROM_ENV is set but "
            "STREAMLIT_AUTH_CLIENT_ID / GOOGLE_CLIENT_ID is missing.\n",
        )
        sys.exit(1)
    if has_oauth_env:
        _write_secrets_toml(secrets_path)

    port = (os.environ.get("PORT") or "8501").strip()
    argv = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        "--server.address=0.0.0.0",
        f"--server.port={port}",
        "--server.headless=true",
    ]
    os.execvp(sys.executable, argv)


if __name__ == "__main__":
    main()
