# Fietsdashboard

Persoonlijk dashboard met wegfietsen, gravel en Zwift uit Strava, op **https://verploegen.eu**.
GitHub haalt elke 3 uur nieuwe ritten op, rekent alles door en zet de site opnieuw online.

## Hoe het werkt

| Onderdeel | Wat het doet |
|---|---|
| `scripts/fetch_strava.py` | Haalt ritten en vermogensdata op uit de Strava API. Bewaart geen kaarten of locaties. |
| `scripts/build_data.py` | Rekent fitheid, volume, vermogenscurve, races en klassiekers uit naar `site/data.json`. |
| `site/index.html` | Het dashboard zelf. |
| `config.json` | Jouw instellingen: FTP, gewicht en de handmatige ZwiftPower-cijfers. |
| `.github/workflows/update.yml` | Draait het geheel elke 3 uur en publiceert via GitHub Pages. |

## Eenmalige installatie

### 1. Je bestaande Strava API-app hergebruiken (2 min)

Strava geeft per account één API-app. Die gebruik je al voor Home Assistant (ha_strava),
en het dashboard deelt hem.

1. Ga naar https://www.strava.com/settings/api
2. Noteer de **Client ID** en **Client Secret**.
3. **Verander niets** aan de instellingen, vooral niet aan het *Authorization Callback Domain*:
   Home Assistant heeft dat nodig. De stappen hieronder gebruiken `localhost`, en dat staat
   Strava altijd toe, ongeacht wat daar is ingevuld.

### 2. Refresh token ophalen (5 min)

Open deze link in je browser, met jouw Client ID op de plek van `CLIENT_ID`:

```
https://www.strava.com/oauth/authorize?client_id=CLIENT_ID&response_type=code&redirect_uri=http://localhost/exchange_token&approval_prompt=force&scope=read,activity:read_all,profile:read_all
```

Klik op **Autoriseren**. Je komt uit op een pagina die niet laadt; dat hoort zo. Kopieer uit de adresbalk de waarde achter `code=` (tot aan `&`).

Wissel die code in via Terminal (Mac) of PowerShell (Windows):

```
curl -X POST https://www.strava.com/oauth/token -d client_id=CLIENT_ID -d client_secret=CLIENT_SECRET -d code=CODE -d grant_type=authorization_code
```

In het antwoord staat `"refresh_token":"..."`. Die waarde heb je nodig in stap 3.
De code is maar een paar minuten geldig, dus doe dit direct na het autoriseren.
Met Python kan het ook: `python scripts/get_refresh_token.py CLIENT_ID CLIENT_SECRET`.

### 3. GitHub instellen

1. Maak een nieuwe, **openbare** repository `fietsdashboard` aan op https://github.com/new
   (GitHub Pages is gratis voor openbare repositories).
2. Zet de code erin.
3. **Settings → Secrets and variables → Actions → New repository secret**, drie keer:
   - `STRAVA_CLIENT_ID`
   - `STRAVA_CLIENT_SECRET`
   - `STRAVA_REFRESH_TOKEN`
4. **Settings → Pages → Build and deployment → Source:** kies **GitHub Actions**.
5. **Actions → Dashboard bijwerken → Run workflow** om de eerste run te starten.

De eerste run haalt al je ritten op plus de vermogensdata van de nieuwste 40. De rest komt er
in de runs daarna bij; na ongeveer twee dagen staat je hele vermogensarchief erin.
Start de eerste run op een moment dat je geen rit uploadt, dan zit je Home Assistant niet in de weg.

### 4. Domein koppelen bij Active24

Log in bij Active24 en open het DNS-beheer van **verploegen.eu**. Laat je bestaande
**MX-records** (voor je e-mail) staan en voeg toe:

| Type | Naam | Waarde |
|---|---|---|
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |
| CNAME | `www` | `badiajunkie.github.io.` |

Staat er al een A-record voor `@` (bijvoorbeeld een parkeerpagina van Active24), verwijder die dan.

Daarna in GitHub: **Settings → Pages → Custom domain:** `verploegen.eu` → Save.
Zodra de DNS-check groen is (soms een paar uur), vink **Enforce HTTPS** aan.

Tip: bevestig het domein ook onder je GitHub-profiel (**Settings → Pages → Add a domain**).
Dan kan niemand anders verploegen.eu aan een eigen GitHub-site koppelen.

## Samen met Home Assistant

- Het dashboard en ha_strava delen de limiet van de app (100 verzoeken per kwartier, 1000 per dag).
  ha_strava haalt alleen iets op als je een rit uploadt; het dashboard draait elke 3 uur op :17
  en gebruikt na de inhaalperiode maar een paar verzoeken per run.
- Strava staat per app maar **één webhook** toe, en die is van Home Assistant. Het dashboard
  gebruikt geen webhook; voeg er ook geen toe.
- Een Strava refresh token werkt niet meer zodra er een nieuwe is uitgegeven. Moet het
  dashboard of Home Assistant daardoor opnieuw gekoppeld worden: zie *Problemen* hieronder.

## Instellingen aanpassen

Pas `config.json` aan en commit. De site wordt binnen een paar minuten opnieuw gebouwd.

- `ftp` en `weight` bepalen de belasting en alle W/kg-waarden.
- `manual` bevat cijfers die Strava niet kent: raceklasse, aantal races en racing score.
  Laat een veld weg om het te verbergen.

## Privacy

De repository en de site zijn openbaar. Er worden alleen afstanden, tijden, hoogtemeters,
hartslag en vermogens bewaard, geen kaarten, routes of start- en eindpunten.
Wil je later een login, dan kan de site naar Cloudflare Pages met Cloudflare Access.

## Problemen

- **Run faalt bij "Strava ophalen" met 400 of 401:** het refresh token klopt niet meer. Herhaal stap 2
  en werk het secret `STRAVA_REFRESH_TOKEN` bij.
- **Home Assistant geeft daarna een Strava-fout:** koppel de integratie opnieuw via
  Instellingen → Apparaten en diensten → Strava → Opnieuw configureren.
- **Run meldt "Strava-limiet bereikt":** geen probleem, de volgende run gaat verder waar deze stopte.
- **Site toont oude cijfers:** kijk onder **Actions** of de laatste run gelukt is.
