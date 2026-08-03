# Changelog

## 1.1.3 - Samlet bekreftelse

- Samler raske `+`- og `−`-trykk på samme vare til én bekreftelse, for eksempel `Melk: fra 1 til 10`.
- Starter en ny bekreftelse først etter noen sekunders pause mellom justeringene.

## 1.1.2 - Kompakt liste

- Gjør listevisningen tydelig mer kompakt enn kortvisningen.
- Fjerner hurtigknapper og åpnehandlinger fra listen, slik at den brukes til rask oversikt.
- Gjør hele vareraden trykkbar og viser navn, plassering, kategori og antall samlet.

## 1.1.1 - Plasseringstagger

- Gjør det mulig å koble NFC-tagger til plasseringer uten å fjerne støtten for produkttagger.
- En plasseringstagg åpner lagerlisten ferdig filtrert til det aktuelle stedet.
- Viser NFC-handlinger direkte under hver plassering på siden **Steder og kategorier**.
- Hurtigknappene `+` og `−` oppdaterer beholdningen i listen uten å åpne varen.
- Antallet pulserer grønt ved økning og rødt ved reduksjon, med en kort bekreftelse på gammelt og nytt antall.

## 1.1.0 - Partier og antall

- La til raske lagerknapper for `+5` og `+10`, samt et eget felt for valgfri positiv eller negativ endring.
- Støtter flere holdbarhetspartier med hvert sitt antall og best før-dato på samme vare.
- Bruker automatisk partiet med tidligst dato når beholdningen reduseres.
- Gjør det mulig å fjerne en holdbarhetsdato uten å fjerne antallet fra lageret.
- Migrerer eksisterende varer med én holdbarhetsdato til ett parti automatisk.

## 1.0.5 - Synlig versjon

- Viser appversjonen som en liten merkelapp ved Hjemmelager-navnet i toppfeltet, også på mobil.

## 1.0.4 - Riktig panelsti

- Bygger NFC-direktelenken fra den faktiske panelstien som er åpen i Home Assistant, i stedet for å anta en Ingress-adresse fra add-on-navnet.
- Viser hvilken Home Assistant-sti som blir skrevet til taggen, slik at feil adresse er synlig før omskriving.

## 1.0.3 - Trygg åpning

- Rettet Ingress-direktelenken som kunne ende i Home Assistants generelle 404-side.
- Direkteåpning bruker nå Home Assistants dokumenterte valg av standardserver og sender tagg-ID-en i navigasjonens fragment.

## 1.0.2 - Tydelig NFC

- Rettet «Test i Home Assistant» slik at den tester appåpningen, ikke iPhone-lenken som kun er ment for NFC-taggen.
- Forklarer tydelig at direkte åpning erstatter Home Assistants vanlige tagg-URL med en ny URL for den aktuelle varen.
- Bruker en URL-parameter for å overføre taggen til Hjemmelager, med støtte for den tidligere lenkeformen videre.

## 1.0.1 - Direkte åpning

- La til en egen NFC-lenke som åpner Home Assistant rett på varen som er koblet til taggen.
- Registrerer skanningen og viser varen uten å endre antallet automatisk.
- La inn veiledet omskriving av eksisterende tagger for Android og iPhone.

## 1.0.0 - Stabil utgave

- Gjorde sletting tryggere: varen, NFC-taggen og historikken kan nå hentes tilbake umiddelbart.
- La inn eksplisitt oppgraderingstest fra tidlig databasestruktur uten tap av eksisterende varer.
- Gjennomførte full regresjon av registrering, bilder, NFC, søk, lager, handleliste, backup, historikk og eksport.
- Ferdigstilte produktnavn, vektorikon, mobilskjermbilde og presentasjon i repoet.
- Samlet installasjon, førstegangsbruk, feilsøking, oppdatering og datatrygghet i dokumentasjonen.
- Stabiliserte 1.0 med 36 automatiske tester.

## 0.9.0 - Profesjonell finish

- Samordnet statusfarger, knapper, overskrifter og kompakte avstander i hele appen.
- La inn synlig lagringsrespons som hindrer doble trykk mens et skjema behandles.
- Forbedret tastatur- og skjermleserstøtte med hopp-lenke, levende status og tydelig fokus.
- Respekterer nå redusert bevegelse fra telefonens eller nettleserens innstillinger.
- Forbedret installasjonsveiledningen og la inn en kort «første fem minutter»-flyt.
- Kontrollert den samlede visningen i mørkt mobilformat på 390 × 844 piksler.
- Utvidet testpakken til 35 tester.

## 0.8.0 - Oversikt og trygghet

- La inn en kompakt status på lagerforsiden med totalt antall, handlebehov, best før og siste endring.
- Samlet forståelig status for NFC, produktoppslag og backup under Mer.
- La inn en egen historikkside med tidspunkt, forklarende hendelser og lenke tilbake til varen.
- La inn en lesbar CSV-eksport som åpnes riktig i vanlige regnearkprogrammer og beholder norske tegn.
- Gjorde backup, eksport og gjenoppretting lettere å finne og skille fra hverandre.
- Utvidet testpakken til 34 tester, inkludert statusoversikt, historikk og eksport.

## 0.7.0 - Raskere hverdag

- Gjorde søket tolerant for små skrivefeil, ulike skrivemåter og norske tegn.
- Grupperte handlelisten etter kategori og beholdt en kompakt sjekkliste for bruk i butikk.
- La inn en tidsbegrenset angreknapp etter endring av lagerantall.
- Beholdt raske pluss-, minus- og åpne-handlinger direkte i både kort- og listevisning.
- Gjorde tomme søkeresultater konkrete, med snarveier tilbake til lageret eller ny registrering.
- Utvidet testpakken til 31 tester, inkludert søk, handlelistegruppering og angrefunksjon.

## 0.6.0 - Trygg oversikt

- Gjorde **Ny** til en kort veiviser med egne valg for strekkode, manuell vare og gjenstand.
- Reduserte førsteregistreringen til navn og antall, med bilde og øvrige felt som valgfrie seksjoner.
- Gjorde strekkode til den naturlige starten og forklarer hvilke produktdata som fylles inn automatisk.
- La inn tydelig bekreftelse etter lagring med neste steg for NFC, detaljer eller en ny registrering.
- Skjulte Tag-ID og irrelevante matvarefelt fra den normale gjenstands- og NFC-flyten.
- Utvidet testpakken til 28 tester for registrering, forslag, NFC, bilder, backup og daglig bruk.

## 0.5.2 - Trygg oversikt

- Viser nå tydelig om NFC-forbindelsen til Home Assistant er klar, kobler til eller prøver igjen.
- Gjør det enklere å skille en manglende NFC-skanning fra et tilkoblingsproblem.
- La inn en levende roadmap for det videre arbeidet frem mot 1.0.

## 0.5.1 - Trygg oversikt

- Koblede Hjemmelager direkte til Home Assistants NFC-hendelser, uten manuell IP-adresse, REST-kommando eller automasjon.
- Gjorde det mulig å gå rett til NFC-kobling etter at en ny vare er lagret.
- Forbedret bildevalg på mobil med bildebibliotek, kamera, forhåndsvisning og automatisk komprimering av store bilder.
- Erstattet svarte feilsider ved bildeproblemer med en forståelig melding og trygg vei tilbake.
- Utvidet testpakken til å dekke automatisk NFC-kobling, bildevalg og bildeopplasting.

## 0.5.0 - Trygg oversikt

- La inn komplett sikkerhetskopi og kontrollert gjenoppretting med automatisk kopi av dagens data før noe erstattes.
- La inn kompakt best før-varsel og filtrert oversikt, sortert med nærmeste dato først.
- Forbedret tomt lager og tomme søkeresultater med tydelige veier til scanning, ny forbruksvare og ny gjenstand.
- Gjorde språket i ny-gjenstand-flyten enklere og skjulte matvarefunksjoner når de ikke er relevante.
- La inn samlet varsel-API for lav beholdning og best før, med ferdig Home Assistant-sensor og daglig mobilautomatisering.
- Utvidet testpakken til å dekke backup, gjenoppretting, varsler, holdbarhetsfilter og førstegangsbruk.

## 0.4.0 - Første hylle

- Delte lageret i **Forbruk**, **Ting** og **Alle**, og gjorde kort, liste, filtre og mobilnavigasjon mer kompakte.
- Forenklet **Ny vare** med et kort grunnskjema og valgfrie seksjoner for lager, plassering, koder og notater.
- La inn automatisk produktoppslag fra Open Food Facts etter strekkodeskann, med navn, merke, lokalt lagret bilde og manuell reserve.
- La inn enkel **Koble NFC-tag**-flyt med ventemodus, automatisk kobling og trygg konflikthåndtering.
- Gjorde handlelisten til en varig sjekkliste med avhuking, deling og eget mål for hvor mye som skal kjøpes.
- La inn tydelig valg for å slå av handlelisten på en vare og sikker, bekreftet sletting av varer.
- Forbedret varesiden med tydeligere lagerhandlinger, kompakt bilde og merking av varer som er utløpt eller snart utløper.
- Skjulte teknisk kameradiagnostikk under **Feilsøking** og forbedret tekstene på Scan-siden.
- La inn automatiske tester for NFC, produktoppslag, handleliste, holdbarhet og sletting.

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
