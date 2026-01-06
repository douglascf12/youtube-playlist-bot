import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube"]

CHANNEL_PLAYLIST_MAP = json.loads(os.environ["YT_CHANNEL_PLAYLIST_MAP"])
STATE_FILE = "state.json"


def load_creds():
    info = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(info, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("Credenciais inválidas")

    return creds


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


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


def get_liked_videos(youtube, max_results=200):
    """Retorna um set com os videoIds curtidos"""
    liked = set()
    page_token = None

    while True:
        resp = youtube.videos().list(
            part="id",
            myRating="like",
            maxResults=50,
            pageToken=page_token
        ).execute()

        for item in resp.get("items", []):
            liked.add(item["id"])

        page_token = resp.get("nextPageToken")
        if not page_token or len(liked) >= max_results:
            break

    return liked


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


def process_channel(youtube, channel_id, playlist_id, state, liked_videos):
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)
    items = list_latest_uploads(youtube, uploads_playlist_id)

    last_seen = state.get(channel_id)
    new_videos = []

    for it in items:
        video_id = it["snippet"]["resourceId"]["videoId"]

        if video_id == last_seen:
            break

        if video_id in liked_videos:
            print(f"[{channel_id}] Ignorado (já curtido): {video_id}")
            continue

        new_videos.append(video_id)

    for video_id in reversed(new_videos):
        add_video_to_playlist(youtube, playlist_id, video_id)
        print(f"[{channel_id}] Adicionado em {playlist_id}: {video_id}")
        state[channel_id] = video_id
        save_state(state)

    if not new_videos:
        print(f"[{channel_id}] Nenhum vídeo novo elegível")


def main():
    creds = load_creds()
    youtube = build("youtube", "v3", credentials=creds)

    state = load_state()
    liked_videos = get_liked_videos(youtube)

    for channel_id, playlist_id in CHANNEL_PLAYLIST_MAP.items():
        process_channel(
            youtube=youtube,
            channel_id=channel_id,
            playlist_id=playlist_id,
            state=state,
            liked_videos=liked_videos
        )


if __name__ == "__main__":
    main()
