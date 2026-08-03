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

1. I Home Assistant: gå til **Settings → Add-ons → Add-on Store**.
2. Trykk menyen øverst til høyre, velg **Repositories**.
3. Lim inn denne adressen:

   ```text
   https://github.com/Geirgutt/tr-kker
   ```

4. Trykk **Add**, finn **Hjemmelager**, installer og start.
5. Slå på **Start on boot**. **Auto update** er valgfritt; la den være av hvis du vil lese endringsloggen før hver oppdatering.

Home Assistant krever at et add-on-repository har `repository.yaml` i roten, og at hver add-on ligger i sin egen mappe med `config.yaml`.

## De første fem minuttene

1. Åpne **Ny** og skann en strekkode eller skriv inn din første vare.
2. Sett et minimumsantall hvis varen skal dukke opp på handlelisten.
3. Legg eventuelt til bilde, plassering og kategori.
4. Koble en NFC-tag etter lagring hvis du bruker NFC-klistremerker.
5. Åpne **Mer** for hjelp, Home Assistant-varsler, backup eller eksport til CSV.

Lagerforsiden viser antall varer, hva som må kjøpes, varer med nær best før og siste endring. **Historikk** under **Mer** viser hva som har skjedd og åpner varen slik at feil kan rettes.

**Kort** er arbeidsvisningen med hurtigknapper for antall. **Liste** er en tettere oversikt uten `+` og `−`; trykk på en rad for å åpne varen og gjøre endringer.

## Hjelp i appen

Spørsmålstegnet i toppfeltet åpner **Hjelp og veiledning**. Fra skanning, handleliste, registrering, organisering og NFC åpnes det relevante temaet automatisk. Spørsmålstegnet er også tilgjengelig når toppmenyen ellers er skjult på mobil.

Hjelpesiden kan søkes og er delt i sammenleggbare guider. Hver guide inneholder korte trinn og en knapp som åpner funksjonen. Den samme hjelpesiden finnes under **Mer → Hjelp og veiledning**.

## Antall og holdbarhetspartier

På varesiden kan beholdningen endres med **Fjern 1**, **Legg til 1**, **Legg til 5**, **Legg til 10** eller et eget tall. Eget tall kan også være negativt, for eksempel `-3`.

Hurtigknappene `+` og `−` på lagerforsiden endrer antallet uten å åpne varen. På forbruksvarer vises også **Åpne** når det finnes uåpnede pakker, og **Bruk opp** når det finnes en åpnet pakke. Pakkehandlingene er visuelt adskilt fra `+` og `−`. Antallet blir kort grønt ved økning og rødt ved reduksjon. Flere raske trykk på samme vare samles i én melding, for eksempel **Melk: fra 8 til 11**. Etter noen sekunders pause starter neste justering på nytt.

Forbruksvarer kan ha flere holdbarhetspartier. Et parti består av et antall og en best før-dato, for eksempel 6 melk med én dato og 4 med en annen. Ved **Legg til parti** velger du om antallet skal legges til totalbeholdningen, eller om det allerede finnes i totalen og bare skal få en dato. Når beholdningen reduseres, trekkes varer automatisk fra partiet med tidligst dato først.

**Fjern dato** gjør partiet udatert, men beholder antallet i lageret. Eksisterende varer som hadde én holdbarhetsdato før oppgraderingen, blir automatisk gjort om til ett parti med samme antall og dato.

## Data, eksport og trygg sletting

Alle lagerdata og opplastede bilder lagres lokalt i add-onens dataområde.

- **Last ned sikkerhetskopi** lager en komplett JSON-kopi som kan gjenopprettes senere.
- **Eksporter lesbar CSV** lager en regnearkfil for kontroll eller videre bruk.
- Før en sikkerhetskopi gjenopprettes, lagrer Hjemmelager automatisk en kopi av dagens data.
- Etter sletting vises **Angre sletting**. De 20 siste slettede varene beholdes lokalt som sikkerhetsnett til eldre slettinger skyves ut.

Ta gjerne en sikkerhetskopi før større opprydding eller oppdatering.

## NFC-flyt

Home Assistant sin mobilapp kan lese NFC-tags. Hjemmelager kobler seg automatisk til Home Assistant ved oppstart og lytter etter `tag_scanned`. Du trenger derfor ikke konfigurere IP-adresse, REST-kommando eller automasjon selv.

### Koble en NFC-tag til en vare

1. Lag eller åpne en vare i Hjemmelager.
2. Ved opprettelse kan du velge **Koble NFC-tag etter lagring**. På en eksisterende vare trykker du **Koble NFC-tag**.
3. Skann NFC-klistremerket i Home Assistant-appen innen tre minutter.
4. Hjemmelager kobler taggen automatisk til varen.
5. Velg **Gjør taggen klar for direkte åpning** hvis du vil at taggen skal åpne denne varen – ikke bare sende en taggskanning til Home Assistant.

Hvis taggen allerede tilhører en annen vare, får du beskjed og ingenting flyttes automatisk. Feltet **Home Assistant Tag-ID** under avanserte varefelter kan fortsatt brukes som manuell reserve.

### Koble en NFC-tag til en plassering

1. Åpne **Mer** og finn plasseringen under **Plasseringer**.
2. Trykk **Koble NFC-tag** ved for eksempel **Kjøleskapsdør**.
3. Skann NFC-klistremerket i Home Assistant-appen innen tre minutter.
4. Velg **Gjør taggen klar for direkte åpning** og skriv direktelenken på samme måte som for en varetagg.

Når plasseringstaggen skannes, åpnes lagerforsiden ferdig filtrert til den plasseringen og viser både forbruksvarer og gjenstander. Produkttagger fortsetter å åpne den enkelte varen. Samme fysiske tagg kan ikke være koblet til både en vare og en plassering.

Dette registrerer scannen direkte i Hjemmelager. Under en aktiv **Koble NFC-tag**-økt kobles neste ukjente tag til den valgte varen. Ellers oppdateres “sist scannet” for kjente tagger.

Home Assistant skriver først en vanlig tagg-URL som bare melder fra om at taggen er skannet. Direkte åpning er en annen URL: den erstatter innholdet på taggen, men beholder koblingen til varen i Hjemmelager. Hjemmelager leser Tag-ID-en fra den nye lenken, registrerer skanningen og viser riktig vare.

På iPhone kan Home Assistant-appen koble taggen, men den kan ikke skrive direkteåpningslenken. Kopier derfor **iPhone-lenken** til en NFC-skriverapp og skriv den som URL på taggen. Når NFC-varselet vises, trykker du **Åpne i Home Assistant**. På Android kan nettleseren skrive taggen direkte når den støtter Web NFC; ellers kopierer du Android-lenken til en NFC-skriverapp. Knappen **Test i Home Assistant** tester selve appåpningen; iPhone-lenken skal bare brukes på NFC-taggen, ikke åpnes i nettleseren.

Direkteåpningssiden leser den faktiske panelstien fra Home Assistant mens Hjemmelager er åpent. Dermed brukes samme sti som allerede virker på den aktuelle installasjonen, også når Home Assistant har gitt Ingress-panelet en annen adresse enn add-on-navnet skulle tilsi.

I add-onens logg skal det stå **Home Assistant NFC-lytter er tilkoblet og klar**. Den tidligere manuelle `rest_command`-automasjonen kan fjernes når denne meldingen vises.

## Strekkode og QR

Hjemmelager har en **Scan**-side som kan bruke mobilkamera til å lese QR-koder og strekkoder. Scanneren bruker ZXing lokalt i nettleseren, samme bibliotekfamilie som Grocy bruker.

Kamera i nettleseren krever normalt HTTPS. Bruk for eksempel Home Assistant Cloud / Nabu Casa, lokal HTTPS eller en reverse proxy med gyldig sertifikat. Hvis kamera ikke er tilgjengelig i nettleseren, kan koden skrives inn manuelt på samme side.

Flyt:

1. Åpne **Scan** fra toppmenyen.
2. Trykk **Skann med kamera**.
3. Scan QR-kode eller strekkode.
4. Hvis koden finnes på en vare, åpnes varen.
5. Hvis koden er ukjent, åpnes ny vare med koden ferdig utfylt.
6. For vanlige produktstrekkoder forsøker Hjemmelager å hente navn, merke, produktbilde og næringsinnhold fra Open Food Facts. Hvis produktet eller nettet ikke er tilgjengelig, fylles varen inn manuelt som før.

Produktoppslaget er gratis og krever ingen konto, men Raspberry Pi-en må kunne kontakte `world.openfoodfacts.org`. Produktdataene kommer fra [Open Food Facts](https://world.openfoodfacts.org/) under Open Database License. Bildet lastes ned én gang, og næringsinnholdet lagres sammen med varen, slik at visningen ikke er avhengig av nettet senere.

Open Food Facts er brukerdrevet. Lenken **Registrer eller rediger hos Open Food Facts** åpner produktsiden for den aktuelle strekkoden, der du kan logge inn og legge til eller korrigere produktdata og egne bilder av emballasjen. Etter en rettelse bruker du **Hent på nytt** i Hjemmelager. Knappen omgår det vanlige 24-timers mellomlageret og henter ferske data direkte. Verdiene vises i formularen før lagring, slik at lokale rettelser ikke overskrives uten at du kontrollerer og lagrer varen.

QR-koder som inneholder en Hjemmelager-vare-URL, for eksempel `/item/12`, åpner varen direkte.

## Legg til vare eller gjenstand

Trykk **Ny** og velg den korteste veien:

1. **Skann en vare** henter navn, bilde, kategori og enhet fra strekkoden når produktet finnes.
2. **Skriv inn en vare** krever bare navn og antall.
3. **Legg inn en gjenstand** brukes for verktøy, utstyr og andre ting.

Bilde, lagergrenser, plassering, kategori og tekniske koder ligger i valgfrie seksjoner. Etter lagring kan du koble NFC-tag, legge til flere detaljer eller registrere noe nytt.

Lagreknappen vises også rett under de viktigste feltene, slik at en rask endring kan lagres uten å bla til bunnen. Hvis du har endret formularen og trykker på en meny, en annen intern lenke eller nettleserens tilbakeknapp, spør Hjemmelager om du vil **Lagre**, **Forkaste** eller **Bli her**. Ved lagring fortsetter appen til siden du valgte etter at varen er lagret. Nettleseren kan vise sin egen standardadvarsel hvis fanen lukkes eller siden lastes på nytt.

### Feilsøking for strekkode-scanning

Scan-siden viser diagnostikk for sikker tilkobling, kameratilgang, ZXing-biblioteket, antall kameraenheter og valgt kamera. Bruk disse punktene hvis kameraet ikke starter:

1. **Home Assistant Ingress:** Åpne add-onen via **Open Web UI** først. Ingress kan fungere fint for vanlig web-UI, men nettleseren kan fortsatt nekte kamera hvis siden ikke regnes som sikker.
2. **Nabu Casa / HTTPS:** Mobilkamera i nettleser krever normalt sikker kontekst. Home Assistant Cloud / Nabu Casa, lokal HTTPS eller en reverse proxy med gyldig sertifikat er anbefalt.
3. **Direkte port 8099:** Hvis Ingress gir problemer, aktiver port `8099` i add-onens **Network**-innstillinger og test `https://homeassistant.local:8099` hvis du har HTTPS foran Home Assistant. Ren `http://homeassistant.local:8099` kan brukes til web-UI, men kamera blir ofte blokkert av mobilnettleseren.
4. **Nettlesertillatelser:** Slett eller endre kameratillatelsen for Home Assistant-siden i nettleseren, last siden på nytt og trykk **Start kamera** igjen.
5. **Android/iPhone:** Sjekk at både nettleseren/Home Assistant-appen og selve nettstedet har kameratillatelse. På iPhone må kamera ofte tillates både for Safari/Home Assistant-appen og for den konkrete siden.

Hvis kamera fortsatt ikke starter, bruk feltet **Manuell kode** på samme side. Backend-oppslag og flyten videre er den samme som ved vellykket scanning.

## Pris, holdbarhet og åpne pakker

Varer kan ha pris og holdbarhetsdato. Utløpte varer og varer med best før-dato innen 14 dager får et tydelig merke. Når slike varer finnes, vises en kompakt rad på lageroversikten som åpner en ferdig filtrert liste med nærmeste dato først. Det samme valget finnes under filterikonet.

Ved opprettelse og redigering kan du velge et bilde fra telefonens bildebibliotek eller åpne kameraet. Bildet vises før lagring, og store mobilbilder skaleres og komprimeres automatisk for å unngå at Home Assistant-visningen stopper under opplasting.

For forbruksvarer kan du skille mellom uåpnede varer på lager og åpne pakker:

1. **Antall** er uåpnet lager, for eksempel `2 nye`.
2. **Åpne pakker** er pakker som er åpnet og normalt ikke skal regnes som lager, for eksempel `1 åpen`.
3. **Åpne pakke** flytter én fra uåpnet lager til åpne pakker.
4. **Bruk opp** reduserer antall åpne pakker.

## Næringsinnhold

Forbruksvarer har en sammenleggbar seksjon for næringsinnhold. Den inneholder energi per 100 g/ml og per porsjon, porsjonsstørrelse og enhet, fett, mettet fett, karbohydrater, sukkerarter, protein, fiber og salt. Feltene fylles automatisk når Open Food Facts har data, men kan alltid åpnes og redigeres manuelt.

Næringsverdiene lagres lokalt når varen lagres og følger med i Hjemmelagers JSON-sikkerhetskopi. Senere endringer hos Open Food Facts endrer ikke varen automatisk. Bruk **Hent på nytt**, kontroller verdiene i formularen og lagre for å ta inn en oppdatering.

## API

Alle endepunkter bruker JSON.

```text
GET  /api/version
GET  /api/items
GET  /api/low-stock
GET  /api/alerts
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

`GET /api/alerts` samler lav beholdning og best før i ett svar som er laget for Home Assistant. `summary.total` er antall unike varer som trenger oppmerksomhet, mens `message` er ferdig tekst til et varsel. Bruk for eksempel `?days=30` for å se 30 dager frem; verdien begrenses til 1–90 dager.

## Home Assistant-varsler

Hjemmelager oppretter og oppdaterer `sensor.hjemmelager_varsler` automatisk gjennom Home Assistant. Sensoren inneholder antall varer som trenger oppmerksomhet og en ferdig varseltekst. Port `8099`, endringer i `configuration.yaml` og omstart av Home Assistant er ikke nødvendig.

1. Oppdater og start Hjemmelager-add-onen.
2. Åpne **Mer → Home Assistant-varsler** i Hjemmelager og kontroller at sensoren er klar.
3. Trykk **Importer varseloppsett** og bekreft importen i Home Assistant.
4. Velg `sensor.hjemmelager_varsler`, telefonen som skal varsles og ønsket tidspunkt.
5. Lagre automasjonen.

Etter lagring finnes oppsettet under **Innstillinger → Automatiseringer og scener → Automatiseringer**, også når siden åpnes i Companion-appen. Automatiseringen sender bare varsel når sensoren er større enn null. Telefonen må være registrert i Home Assistant via Companion-appen for å kunne velges.

Sensoren oppdateres etter relevante lagerendringer og kontrolleres dessuten automatisk mens add-onen kjører. De gamle YAML-eksemplene i `hjemmelager/examples` er kun reserveoppsett for eldre installasjoner.

## Oppdatering

Add-onen skal ikke oppdatere sin egen container innenfra. Home Assistant Supervisor eier installasjon og oppdatering av add-ons.

Etter at repo-listen oppdateres kan Home Assistant i noen minutter vise samme installerte og nyeste versjon selv om et varsel allerede er på vei. Vent til oppdateringen vises under **Notifications**, eller oppdater add-on-butikken på nytt.

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

### Last ned egen sikkerhetskopi

Åpne **Mer**, finn **Data og sikkerhetskopi**, og trykk **Last ned sikkerhetskopi**. Filen inneholder varer, bilder, steder, kategorier og historikk i et lesbart JSON-format. Nedlastingen endrer ingenting i Hjemmelager.

Oppbevar filen på en annen enhet enn Raspberry Pi-en. Home Assistant sine vanlige sikkerhetskopier bør fortsatt brukes i tillegg.

Fra samme panel kan du åpne **Gjenopprett fra fil** og velge en tidligere sikkerhetskopi. Hjemmelager kontrollerer filen før noe endres, ber om en tydelig bekreftelse og lager automatisk en ekstra kopi av dagens data før gjenopprettingen starter. En gjenoppretting erstatter dagens varer, steder, kategorier og historikk med innholdet i sikkerhetskopien.

## Versjon og kodenavn

Hver godkjente versjon skal ha både versjonsnummer og kodenavn.

Gjeldende versjon er:

```text
1.4.0 - Ryddig vareflyt
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

Et tomt lager viser to enkle veier videre: skann en matvare med strekkode, eller legg til en gjenstand som verktøy og utstyr. Tomme søk og filtre viser en egen **Vis hele lageret**-knapp, slik at brukeren ikke blir stående fast.

1. Opprett lokasjoner som tekst, for eksempel `Bod > Hylle 2 > Boks A`.
2. Legg NFC-tag på boksen, skuffen eller varen.
3. Opprett varen i Hjemmelager og bruk **Koble NFC-tag**, eller skann produktets strekkode.
4. Bruk **Fjern 1**, **Legg til 1**, **Åpne 1 pakke** og **Bruk 1 åpen** fra varesiden.
5. Bruk **Handleliste** som sjekkliste i butikken.

For forbruksvarer kan **Varsle ved antall** brukes som grensen for når varen dukker opp på handlelisten. Verdien `0` betyr at varen legges til når ingen uåpnede pakker er igjen, for eksempel idet siste pakke åpnes. **Varsle og legg på handlelisten når beholdningen blir lav** slår denne funksjonen helt av eller på for varen.

**Fyll opp til** bestemmer hvor mange handlelisten foreslår at du kjøper. Hvis feltet ikke er satt, brukes varslingsgrensen; Hjemmelager foreslår alltid minst én enhet.

Hvis en tom vare skal beholdes, men ikke kjøpes igjen, trykker du **Ikke på handleliste** på varesiden. Hvis varen ikke skal beholdes i Hjemmelager i det hele tatt, åpner du **Flere valg** og velger **Slett vare**. Sletting krever bekreftelse og fjerner også NFC-koblingen og varehistorikken.
