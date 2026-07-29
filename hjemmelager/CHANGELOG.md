# Changelog

## 0.3.0 - Første hylle

- Gjorde mobilgrensesnittet enklere med fast bunnmeny, tydeligere varekort, søk og sammenleggbare filtre.
- Forbedret bilder, lagertekster og handlinger for forbruksvarer og gjenstander.
- Fikset relative lenker slik at redigering ikke havner i en ugyldig navigasjonsløkke.

## 0.2.1 - Første hylle

- La inn diagnostikk på Scan-siden for HTTPS, nettleser-API-er, ZXing og valgte kameraenheter.
- Gjorde kameraoppstart mer robust i Home Assistant ved å be om kameratilgang før enhetsvalg når nettleseren skjuler kameranavn.
- Foretrekker bak-/miljøkamera på mobil og stopper scanneren straks en kode er lest.

## 0.2.0 - Første hylle

- La inn pris og holdbarhetsdato på varer.
- La inn egen telling for åpne pakker ved siden av uåpnet lager.
- La inn handlingene **Åpne pakke** og **Bruk åpen**.

## 0.1.9 - Første hylle

- Byttet kamera-scanneren fra nettleserens `BarcodeDetector` til lokalt vendoret ZXing, samme bibliotekfamilie som Grocy bruker.
- Scanner-siden fungerer nå i flere nettlesere og støtter både strekkoder og QR-koder uten CDN-avhengighet.

## 0.1.8 - Første hylle

- Fjernet **Koble NFC-tag**-knappen og automatisk ventemodus for NFC-scans.
- NFC kan fortsatt brukes ved å lime inn `tag_id` manuelt på varen og sette opp Home Assistant-automasjon selv.

## 0.1.7 - Første hylle

- La inn strekkode-/QR-felt på varer.
- La inn **Scan**-side som kan lese QR-koder og strekkoder via kamera når nettleseren støtter det.
- Scannede koder åpner kjent vare, eller starter ny vare med koden ferdig utfylt.

## 0.1.6 - Første hylle

- La inn **Koble NFC-tag** på varesiden.
- Neste Home Assistant `tag_scanned` kan nå automatisk kobles til valgt vare via eksisterende `/api/tag/{tag_id}/touch`-flyt.

## 0.1.5 - Første hylle

- La inn egne registre for plasseringer og kategorier.
- Endret vareskjemaet til valglister med mulighet for å legge til nye steder og kategorier.
- La inn egen side for å administrere steder og kategorier.

## 0.1.4 - Første hylle

- La inn kompakt listevisning for store varelister.
- La inn filter for plassering, kategori og lav beholdning på varelisten.

## 0.1.3 - Første hylle

- Viser små varebilder i varelisten når varen har bilde.
- La inn støtte for å laste opp bildefil direkte hvis man ikke har bilde-URL.

## 0.1.2 - Første hylle

- Fikset Docker-build for nyere Home Assistant Supervisor-versjoner ved å bruke eksplisitt base image.
- Fjernet utdaterte arkitekturverdier fra add-on-konfigurasjonen.

## 0.1.1 - Første hylle

- La inn eksempler for daglig oppdateringssjekk via Home Assistant sin update-entity.
- La inn varseloppsett for nye add-on-versjoner uten automatisk installasjon.
- Beholder kodenavnet `Første hylle` for denne første patch-releasen.

## 0.1.0 - Første hylle

- Første versjon av Hjemmelager.
- Mobilvennlig web-UI via Home Assistant Ingress.
- SQLite-lagring i add-onens `/data`.
- Varer/gjenstander med antall, minimum, plassering, kategori og NFC tag-id.
- Quick actions for tag-scanning og API-endepunkter for automasjoner.
