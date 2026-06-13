"""YouTube upload logic for diary.

Handles OAuth 2.0 (one-time browser consent, then cached token with
auto-refresh) and uploads a recording as a private, unlisted-from-search
video to YouTube.
"""

import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

DIARY_DIR = os.path.expanduser("~/diary")
CONFIG_PATH = os.path.join(DIARY_DIR, "config.json")
TOKEN_PATH = os.path.join(DIARY_DIR, "token.json")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class ClientSecretsNotFound(Exception):
    pass


def _load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _get_client_secrets_path():
    config = _load_config()
    path = config.get("client_secrets_path") or os.path.join(
        DIARY_DIR, "client_secrets.json"
    )
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise ClientSecretsNotFound(path)
    return path


def get_credentials():
    """Return valid OAuth credentials, running the consent flow only once."""
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
        return creds

    client_secrets_path = _get_client_secrets_path()
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _save_token(creds):
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())


def upload_video(file_path, title, description="", privacy_status="private",
                  category_id="22", progress_callback=None):
    """Upload a video file to YouTube.

    progress_callback, if given, is called with an int 0-100 as the
    upload progresses.
    """
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            percent = int(status.progress() * 100)
            if progress_callback:
                progress_callback(percent)
            else:
                print(f"Upload progress: {percent}%")

    if progress_callback:
        progress_callback(100)
    else:
        print("Upload progress: 100%")

    return response
