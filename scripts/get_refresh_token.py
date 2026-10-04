"""Eenmalig: haal een Strava refresh token op met leesrechten op al je activiteiten.

Gebruik:
  python scripts/get_refresh_token.py <client_id> <client_secret>

Het script toont een link. Open die, klik op "Autoriseren", en plak daarna de
volledige adresregel van de pagina waar je op uitkomt (die laadt niet, dat is
normaal) terug in de terminal.
"""
import sys
from urllib.parse import parse_qs, urlparse

import requests

if len(sys.argv) != 3:
    sys.exit(__doc__)
cid, secret = sys.argv[1], sys.argv[2]
url = (
    "https://www.strava.com/oauth/authorize"
    f"?client_id={cid}&response_type=code&redirect_uri=http://localhost/exchange_token"
    "&approval_prompt=force&scope=read,activity:read_all,profile:read_all"
)
print("\n1. Open deze link en klik op Autoriseren:\n\n" + url + "\n")
back = input("2. Plak hier de adresregel van de pagina waar je op uitkwam:\n> ").strip()
code = parse_qs(urlparse(back).query).get("code", [back])[0]
r = requests.post(
    "https://www.strava.com/oauth/token",
    data={"client_id": cid, "client_secret": secret, "code": code, "grant_type": "authorization_code"},
    timeout=30,
)
r.raise_for_status()
print("\nGelukt. Zet deze waarde als STRAVA_REFRESH_TOKEN in GitHub:\n")
print(r.json()["refresh_token"])
