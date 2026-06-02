# Hjemmelager

Hjemmelager er en liten “Grocy-light” for Home Assistant. Den er laget for rask bruk i hverdagen: scan en NFC-tag, trykk `+1`, `-1`, `Sett antall` eller finn ut hvor noe ligger.

## Installasjon lokalt

1. Installer Home Assistant add-onen **Samba share** eller **Terminal & SSH**.
2. Kopier hele mappen `hjemmelager` til Home Assistant sin lokale `/addons`-mappe.
3. Gå til **Settings → Add-ons → Add-on Store**.
4. Trykk menyen øverst til høyre og velg **Check for updates**.
5. Åpne **Local add-ons → Hjemmelager**.
6. Trykk **Install** og deretter **Start**.
7. Åpne add-onen via **Open Web UI**.

## Installasjon fra GitHub

1. Legg repoet på GitHub.
2. Sørg for at `repository.yaml` og `hjemmelager/config.yaml` peker til dette GitHub-repoet.
3. I Home Assistant: gå til **Settings → Add-ons → Add-on Store**.
4. Trykk menyen øverst til høyre, velg **Repositories**.
5. Lim inn GitHub-URL-en til repoet, for eksempel:

   ```text
   https://github.com/Geirgutt/tr-kker
   ```

6. Trykk **Add**, finn **Hjemmelager**, installer og start.

Home Assistant krever at et add-on-repository har `repository.yaml` i roten, og at hver add-on ligger i sin egen mappe med `config.yaml`.

## NFC-flyt

Home Assistant sin mobilapp kan lese NFC-tags. Når en tag scannes, fyrer Home Assistant eventen `tag_scanned` med en `tag_id`.

### Manuell bruk

1. Lag eller åpne en vare i Hjemmelager.
2. Skriv inn NFC-tag-id i feltet **NFC tag-id**.
3. Når taggen scannes i Home Assistant, kan du bruke automasjonen under for å sende scannen til Hjemmelager.

### REST-kommando i Home Assistant

Legg dette i `configuration.yaml`:

```yaml
rest_command:
  hjemmelager_tag_touch:
    url: "http://local-hjemmelager:8099/api/tag/{{ tag_id }}/touch"
    method: POST
    content_type: "application/json"
    payload: "{}"
```

Hvis add-onen er installert fra GitHub, kan hostname være et generert repo-navn i stedet for `local-hjemmelager`. Se add-onens logg eller Supervisor-info hvis DNS-navnet ikke svarer. Som fallback kan du aktivere port `8099` i add-onens **Network**-innstillinger og bruke `http://homeassistant.local:8099`.

### Automasjon for alle tagger

```yaml
alias: Hjemmelager NFC
mode: queued
trigger:
  - platform: event
    event_type: tag_scanned
action:
  - service: rest_command.hjemmelager_tag_touch
    data:
      tag_id: "{{ trigger.event.data.tag_id }}"
```

Dette registrerer scannen i Hjemmelager. Hvis taggen finnes, oppdateres “sist scannet”. Hvis den ikke finnes, returnerer API-et `404`, og du kan opprette en vare med tag-id-en i web-UI.

## Strekkode og QR

Hjemmelager har en **Scan**-side som kan bruke mobilkamera til å lese QR-koder og strekkoder. Scanneren bruker ZXing lokalt i nettleseren, samme bibliotekfamilie som Grocy bruker.

Kamera i nettleseren krever normalt HTTPS. Bruk for eksempel Home Assistant Cloud / Nabu Casa, lokal HTTPS eller en reverse proxy med gyldig sertifikat. Hvis kamera ikke er tilgjengelig i nettleseren, kan koden skrives inn manuelt på samme side.

Flyt:

1. Åpne **Scan** fra toppmenyen.
2. Trykk **Start kamera**.
3. Scan QR-kode eller strekkode.
4. Hvis koden finnes på en vare, åpnes varen.
5. Hvis koden er ukjent, åpnes ny vare med koden ferdig utfylt.

QR-koder som inneholder en Hjemmelager-vare-URL, for eksempel `/item/12`, åpner varen direkte.

### Feilsøking for strekkode-scanning

Scan-siden viser diagnostikk for sikker tilkobling, kameratilgang, ZXing-biblioteket, antall kameraenheter og valgt kamera. Bruk disse punktene hvis kameraet ikke starter:

1. **Home Assistant Ingress:** Åpne add-onen via **Open Web UI** først. Ingress kan fungere fint for vanlig web-UI, men nettleseren kan fortsatt nekte kamera hvis siden ikke regnes som sikker.
2. **Nabu Casa / HTTPS:** Mobilkamera i nettleser krever normalt sikker kontekst. Home Assistant Cloud / Nabu Casa, lokal HTTPS eller en reverse proxy med gyldig sertifikat er anbefalt.
3. **Direkte port 8099:** Hvis Ingress gir problemer, aktiver port `8099` i add-onens **Network**-innstillinger og test `https://homeassistant.local:8099` hvis du har HTTPS foran Home Assistant. Ren `http://homeassistant.local:8099` kan brukes til web-UI, men kamera blir ofte blokkert av mobilnettleseren.
4. **Nettlesertillatelser:** Slett eller endre kameratillatelsen for Home Assistant-siden i nettleseren, last siden på nytt og trykk **Start kamera** igjen.
5. **Android/iPhone:** Sjekk at både nettleseren/Home Assistant-appen og selve nettstedet har kameratillatelse. På iPhone må kamera ofte tillates både for Safari/Home Assistant-appen og for den konkrete siden.

Hvis kamera fortsatt ikke starter, bruk feltet **Manuell kode** på samme side. Backend-oppslag og flyten videre er den samme som ved vellykket scanning.

## Pris, holdbarhet og åpne pakker

Varer kan ha pris og holdbarhetsdato. Datoen er et enkelt datofelt og brukes foreløpig som informasjon på varen.

For forbruksvarer kan du skille mellom uåpnede varer på lager og åpne pakker:

1. **Antall** er uåpnet lager, for eksempel `2 nye`.
2. **Åpne pakker** er pakker som er åpnet og normalt ikke skal regnes som lager, for eksempel `1 åpen`.
3. **Åpne pakke** flytter én fra uåpnet lager til åpne pakker.
4. **Bruk åpen** reduserer antall åpne pakker.

## API

Alle endepunkter bruker JSON.

```text
GET  /api/version
GET  /api/items
GET  /api/low-stock
POST /api/items
POST /api/items/{id}/adjust
POST /api/items/{id}/open
POST /api/items/{id}/adjust-opened
POST /api/tag/{tag_id}/touch
POST /api/tag/{tag_id}/adjust
```

Eksempel:

```bash
curl -X POST http://homeassistant.local:8099/api/tag/04-AB-CD/adjust \
  -H "Content-Type: application/json" \
  -d '{"delta": -1}'
```

## Oppdatering

Add-onen skal ikke oppdatere sin egen container innenfra. Home Assistant Supervisor eier installasjon og oppdatering av add-ons.

Når Hjemmelager er installert fra GitHub-repoet, vil Home Assistant normalt lage en `update`-entity for add-onen. Finn riktig entity-id i **Settings → Devices & services → Entities** ved å søke etter `Hjemmelager`.

I eksemplene under brukes:

```text
update.hjemmelager_update
```

Bytt den ut hvis Home Assistant har gitt entity-en et annet navn.

### Daglig sjekk med varsel

Bruk dette hvis du vil ha kontroll før du installerer:

```yaml
alias: Hjemmelager - daglig oppdateringssjekk
description: Sjekker Hjemmelager sin update-entity hver morgen og varsler hvis ny versjon finnes.
mode: single
trigger:
  - platform: time
    at: "07:30:00"
variables:
  hjemmelager_update_entity: update.hjemmelager_update
action:
  - action: homeassistant.update_entity
    target:
      entity_id: "{{ hjemmelager_update_entity }}"
  - delay: "00:00:10"
  - condition: template
    value_template: "{{ is_state(hjemmelager_update_entity, 'on') }}"
  - action: persistent_notification.create
    data:
      title: Hjemmelager-oppdatering tilgjengelig
      message: >
        Ny Hjemmelager-versjon er tilgjengelig.
        Installert: {{ state_attr(hjemmelager_update_entity, 'installed_version') }}
        Ny: {{ state_attr(hjemmelager_update_entity, 'latest_version') }}
      notification_id: hjemmelager_update_available
```

Samme eksempel ligger i:

```text
hjemmelager/examples/daily_update_check.yaml
```

### Lokal `/addons`-installasjon

1. Kopier inn nye filer i `/addons/hjemmelager`.
2. Øk `version` i `hjemmelager/config.yaml`.
3. Gå til **Add-on Store → Check for updates**.
4. Åpne add-onen og trykk **Rebuild** eller **Update**.

### GitHub-installasjon

1. Commit og push endringer til GitHub.
2. Øk `version` i `hjemmelager/config.yaml`.
3. I Home Assistant: **Add-on Store → Check for updates**.
4. Trykk **Update** på Hjemmelager.

Data lagres i add-onens `/data/hjemmelager.db`, så databasen overlever restart og oppdatering av add-onen.

## Versjon og kodenavn

Hver godkjente versjon skal ha både versjonsnummer og kodenavn.

Gjeldende versjon er:

```text
0.2.1 - Første hylle
```

Kontroller installert versjon på én av disse måtene:

1. Åpne Hjemmelager web-UI og se nederst på siden.
2. Kall API-et:

   ```text
   GET /api/version
   ```

Ved nye endringer bør disse fire stedene oppdateres sammen:

```text
hjemmelager/config.yaml      version
hjemmelager/app/server.py    APP_VERSION og APP_CODENAME
hjemmelager/CHANGELOG.md     release-notat
hjemmelager/DOCS.md          denne kontrollseksjonen
```

## Første anbefalte arbeidsflyt

1. Opprett lokasjoner som tekst, for eksempel `Bod > Hylle 2 > Boks A`.
2. Legg NFC-tag på boksen, skuffen eller varen.
3. Opprett varen i Hjemmelager og fyll inn NFC tag-id manuelt, eller legg inn strekkode/QR-kode.
4. Bruk `+1`, `-1` og `Sett antall` fra mobilvisningen.
5. Bruk **Lav beholdning** som enkel handleliste-kilde.
