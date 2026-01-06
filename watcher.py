import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube"]

CHANNEL_ID = os.environ["YT_CHANNEL_ID"]
TARGET_PLAYLIST_ID = os.environ["YT_PLAYLIST_ID"]

STATE_FILE = "state.json"  # guarda último vídeo processado


def load_creds():
    token_json = os.environ["GOOGLE_TOKEN_JSON"]
    info = json.loads(token_json)

    creds = Credentials.from_authorized_user_info(info, SCOPES)

    # Se expirou, renova via refresh_token
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "Credenciais inválidas e sem refresh_token. Refaça o bootstrap local para gerar token.json."
            )
    return creds


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_video_id": None}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def get_uploads_playlist_id(youtube, channel_id: str) -> str:
    resp = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("CHANNEL_ID não encontrado. Verifique YT_CHANNEL_ID.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_latest_uploads(youtube, uploads_playlist_id: str, max_results: int = 10):
    resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist_id,
        maxResults=max_results,
    ).execute()
    return resp.get("items", [])


def add_video_to_playlist(youtube, playlist_id: str, video_id: str):
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def main():
    creds = load_creds()
    youtube = build("youtube", "v3", credentials=creds)

    uploads_playlist_id = get_uploads_playlist_id(youtube, CHANNEL_ID)
    items = list_latest_uploads(youtube, uploads_playlist_id, max_results=10)

    state = load_state()
    last_seen = state.get("last_video_id")

    new_video_ids = []
    for it in items:
        video_id = it["snippet"]["resourceId"]["videoId"]
        if video_id == last_seen:
            break
        new_video_ids.append(video_id)

    # adiciona do mais antigo -> mais novo
    for video_id in reversed(new_video_ids):
        add_video_to_playlist(youtube, TARGET_PLAYLIST_ID, video_id)
        print(f"Adicionado: {video_id}")
        state["last_video_id"] = video_id
        save_state(state)

    if not new_video_ids:
        print("Nenhum vídeo novo.")


if __name__ == "__main__":
    main()
