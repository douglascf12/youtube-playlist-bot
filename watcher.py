import os
import json
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

# ============================================================
# CONFIGURAÇÕES
# ============================================================

SCOPES = ["https://www.googleapis.com/auth/youtube"]

STATE_FILE = "state.json"

MAX_VIDEOS_PER_CHANNEL = 2      # proteção de quota
MAX_VIDEO_AGE_DAYS = 150         # ignora vídeos antigos


# ============================================================
# AUTH
# ============================================================

def load_creds():
    info = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(info, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("Credenciais OAuth inválidas")

    return creds


# ============================================================
# STATE
# ============================================================

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_processed(state, channel_id, video_id):
    return video_id in state.get(channel_id, {}).get("processed", [])


def mark_processed(state, channel_id, video_id):
    state.setdefault(channel_id, {}).setdefault("processed", []).append(video_id)
    state[channel_id]["processed"] = state[channel_id]["processed"][-300:]


# ============================================================
# HELPERS
# ============================================================

def is_recent(published_at_iso):
    published = datetime.fromisoformat(
        published_at_iso.replace("Z", "+00:00")
    )
    return datetime.now(timezone.utc) - published <= timedelta(days=MAX_VIDEO_AGE_DAYS)


# ============================================================
# YOUTUBE API – READ
# ============================================================

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


def get_liked_videos(youtube):
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
        if not page_token:
            break

    return liked


def get_all_playlist_video_ids(youtube, playlist_ids):
    """
    Retorna um set com TODOS os video_ids já presentes
    em qualquer playlist monitorada
    """
    all_videos = set()

    for playlist_id in playlist_ids:
        page_token = None

        while True:
            resp = youtube.playlistItems().list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token
            ).execute()

            for item in resp.get("items", []):
                all_videos.add(item["contentDetails"]["videoId"])

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return all_videos


# ============================================================
# YOUTUBE API – WRITE
# ============================================================

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


# ============================================================
# PROCESSAMENTO
# ============================================================

def process_channel(
    youtube,
    channel_id,
    playlist_id,
    state,
    liked_videos,
    existing_playlist_videos
):
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)
    items = list_latest_uploads(youtube, uploads_playlist_id)

    added = 0

    for item in items:
        snippet = item["snippet"]
        video_id = snippet["resourceId"]["videoId"]
        published_at = snippet["publishedAt"]

        # ❌ já processado
        if is_processed(state, channel_id, video_id):
            continue

        # ❌ já está em alguma playlist monitorada
        if video_id in existing_playlist_videos:
            print(f"[{channel_id}] Ignorado (já em playlist): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        # ❌ vídeo antigo
        if not is_recent(published_at):
            print(f"[{channel_id}] Ignorado (antigo): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        # ❌ vídeo curtido
        if video_id in liked_videos:
            print(f"[{channel_id}] Ignorado (já curtido): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        # ✅ adicionar
        try:
            add_video_to_playlist(youtube, playlist_id, video_id)
            print(f"[{channel_id}] Adicionado em {playlist_id}: {video_id}")

            mark_processed(state, channel_id, video_id)
            existing_playlist_videos.add(video_id)
            save_state(state)

            added += 1
            if added >= MAX_VIDEOS_PER_CHANNEL:
                break

        except HttpError as e:
            if e.resp.status == 403:
                print("⚠️ Quota excedida, encerrando execução.")
                save_state(state)
                return
            raise

    if added == 0:
        print(f"[{channel_id}] Nenhum vídeo elegível")


# ============================================================
# MAIN
# ============================================================

def main():
    channel_playlist_map = json.loads(
        os.environ["YT_CHANNEL_PLAYLIST_MAP"]
    )

    creds = load_creds()
    youtube = build("youtube", "v3", credentials=creds)

    state = load_state()
    liked_videos = get_liked_videos(youtube)

    # 🔥 NOVO: carregar TODOS os vídeos já existentes nas playlists
    playlist_ids = set(channel_playlist_map.values())
    existing_playlist_videos = get_all_playlist_video_ids(
        youtube,
        playlist_ids
    )

    for channel_id, playlist_id in channel_playlist_map.items():
        process_channel(
            youtube=youtube,
            channel_id=channel_id,
            playlist_id=playlist_id,
            state=state,
            liked_videos=liked_videos,
            existing_playlist_videos=existing_playlist_videos
        )


if __name__ == "__main__":
    main()
