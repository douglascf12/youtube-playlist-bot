"""
bootstrap.py — geração do token OAuth2 para o youtube-playlist-bot.

Rode localmente UMA VEZ para autenticar e gerar o valor do Secret GOOGLE_TOKEN_JSON:

    python3 bootstrap.py

Pré-requisito: ter o arquivo client_secret.json na mesma pasta.
(Baixe em: Google Cloud Console → APIs & Services → Credentials → seu OAuth 2.0 Client)
"""

import pathlib
import sys

SCOPES = ["https://www.googleapis.com/auth/youtube"]
SECRET_FILE = "client_secret.json"


def main() -> None:
    if not pathlib.Path(SECRET_FILE).exists():
        print(f"[ERRO] Arquivo '{SECRET_FILE}' não encontrado.")
        print("Baixe em: Google Cloud Console → APIs & Services → Credentials")
        print("Selecione seu OAuth 2.0 Client ID → Download JSON → salve como client_secret.json")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("[ERRO] Dependência ausente. Instale com:")
        print("  pip install google-auth-oauthlib")
        sys.exit(1)

    print("Abrindo navegador para autenticação com o Google...")
    print("Faça login com a conta que tem acesso às playlists.\n")

    flow = InstalledAppFlow.from_client_secrets_file(SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    token_json = creds.to_json()

    print("\n" + "=" * 60)
    print("TOKEN GERADO COM SUCESSO!")
    print("=" * 60)
    print("\nCopie o valor abaixo e cole no Secret GOOGLE_TOKEN_JSON do GitHub:\n")
    print(token_json)
    print("\n" + "=" * 60)
    print("GitHub → Settings → Secrets and variables → Actions → GOOGLE_TOKEN_JSON")
    print("=" * 60)

    # Salva localmente também para referência
    output = pathlib.Path("token_output.json")
    output.write_text(token_json, encoding="utf-8")
    print(f"\nToken também salvo em: {output.resolve()}")
    print("(não commite este arquivo — ele já está no .gitignore)")


if __name__ == "__main__":
    main()
