# Changelog

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
