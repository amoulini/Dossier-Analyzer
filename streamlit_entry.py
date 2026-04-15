"""Bootstrap Streamlit secrets from the environment, then exec the Streamlit server.

Cloud Run and other hosts often inject OAuth and GCP settings as env vars instead of mounting
``.streamlit/secrets.toml``. Streamlit's ``st.login`` still expects ``[auth]`` in that file, so
this script writes it (and optional ``[gcp]``) before starting Streamlit.

Whenever this script generates ``secrets.toml``, it **replaces** the file entirely (atomic write),
so a baked-in or volume-mounted copy never wins over the current environment.

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

``STREAMLIT_SECRETS_FROM_ENV`` (``1`` / ``true`` / ``yes``): require a generated file at startup.
 With full OAuth env: write ``[auth]`` and optional ``[gcp]``.
  Without OAuth but with ``GCS_BUCKET_NAME`` or ``GCS_MAX_BATCH_UPLOAD_MB``: write ``[gcp]`` only.
If unset and OAuth client id is absent, any existing ``.streamlit/secrets.toml`` is left unchanged.
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


def _gcp_section_lines() -> list[str]:
    bucket = _e("GCS_BUCKET_NAME")
    max_batch = (os.environ.get("GCS_MAX_BATCH_UPLOAD_MB") or "").strip()
    if not bucket and not max_batch:
        return []
    lines = ["", "[gcp]"]
    if bucket:
        lines.append(f"bucket_name = {_toml_basic_str(bucket)}")
    if max_batch:
        try:
            lines.append(f"max_batch_upload_mb = {int(max_batch)}")
        except ValueError:
            pass
    return lines


def _atomic_replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _sync_secrets_toml_from_env(path: Path, *, force: bool) -> None:
    """Build ``secrets.toml`` from the environment and replace ``path`` (no merge with old file)."""
    header = "# Generated at container start from environment variables — do not commit."

    redirect = _e(
        "STREAMLIT_AUTH_REDIRECT_URI",
        "AUTH_REDIRECT_URI",
    )
    cookie = _e(
        "STREAMLIT_AUTH_COOKIE_SECRET",
        "AUTH_COOKIE_SECRET",
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

    if client_id:
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
            header,
            "[auth]",
            f"redirect_uri = {_toml_basic_str(redirect)}",
            f"cookie_secret = {_toml_basic_str(cookie)}",
            f"client_id = {_toml_basic_str(client_id)}",
            f"client_secret = {_toml_basic_str(client_secret)}",
            f"server_metadata_url = {_toml_basic_str(meta)}",
        ]
        lines.extend(_gcp_section_lines())
        _atomic_replace_text(path, "\n".join(lines) + "\n")
        return

    gcp_only = _gcp_section_lines()
    if force and gcp_only:
        lines = [header, *gcp_only]
        _atomic_replace_text(path, "\n".join(lines) + "\n")
        return

    if force:
        sys.stderr.write(
            "streamlit_entry: STREAMLIT_SECRETS_FROM_ENV is set but no OAuth client id "
            "and no GCS_BUCKET_NAME / GCS_MAX_BATCH_UPLOAD_MB to write [gcp].\n",
        )
        sys.exit(1)


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

    if force or has_oauth_env:
        _sync_secrets_toml_from_env(secrets_path, force=force)

    port = (os.environ.get("PORT") or "8080").strip()
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
