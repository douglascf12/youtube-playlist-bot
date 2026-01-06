import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube"]

CHANNEL_IDS = json.loads(os.environ["YT_CHANNEL_IDS"])
TARGET_PLAYLIST_ID = os.environ["YT_PLAYLIST_ID"]

STATE_FILE = "state.json"


def load_creds():
    info = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(info, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("Credenciais inválidas ou sem refresh_token")

    return creds


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}  # estado por canal


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def get_uploads_playlist_id(youtube, channel_id):
    resp = youtube.channels().list(
        part="contentDetails",
        id=channel_id
    ).execute()

    items = resp.get("items", [])
    if not items:
        raise RuntimeError(f"Canal não encontrado: {channel_id}")

    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_latest_uploads(youtube, uploads_playlist_id, max_results=10):
    resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist_id,
        maxResults=max_results
    ).execute()
    return resp.get("items", [])


def add_video_to_playlist(youtube, playlist_id, video_id):
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id
                }
            }
        }
    ).execute()


def process_channel(youtube, channel_id, state):
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)
    items = list_latest_uploads(youtube, uploads_playlist_id)

    last_seen = state.get(channel_id)
    new_videos = []

    for it in items:
        video_id = it["snippet"]["resourceId"]["videoId"]
        if video_id == last_seen:
            break
        new_videos.append(video_id)

    for video_id in reversed(new_videos):
        add_video_to_playlist(youtube, TARGET_PLAYLIST_ID, video_id)
        print(f"[{channel_id}] Adicionado: {video_id}")
        state[channel_id] = video_id
        save_state(state)

    if not new_videos:
        print(f"[{channel_id}] Nenhum vídeo novo")


def main():
    creds = load_creds()
    youtube = build("youtube", "v3", credentials=creds)

    state = load_state()

    for channel_id in CHANNEL_IDS:
        process_channel(youtube, channel_id, state)


if __name__ == "__main__":
    main()
