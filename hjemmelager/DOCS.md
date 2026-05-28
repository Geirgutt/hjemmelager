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

### Enkel bruk uten automasjon

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

## API

Alle endepunkter bruker JSON.

```text
GET  /api/version
GET  /api/items
GET  /api/low-stock
POST /api/items
POST /api/items/{id}/adjust
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
0.1.2 - Første hylle
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
3. Opprett varen i Hjemmelager og fyll inn samme tag-id.
4. Bruk `+1`, `-1` og `Sett antall` fra mobilvisningen.
5. Bruk **Lav beholdning** som enkel handleliste-kilde.
