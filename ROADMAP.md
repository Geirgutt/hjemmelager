# Roadmap for Hjemmelager

Dette er den levende planen for prosjektet. Den oppdateres når vi starter, fullfører
eller omprioriterer arbeid.

## Mål

Hjemmelager skal være en enkel og mobilvennlig Home Assistant-app som:

- er rask å forstå uten forkunnskaper
- gjør registrering av varer og gjenstander lett
- bruker strekkode og NFC uten teknisk oppsett
- gir en nyttig lageroversikt og handleliste
- føles gjennomført, trygg og kommersiell
- fungerer lokalt uten abonnement eller skytjeneste

## Statusforklaring

- ✅ Ferdig og publisert
- 🟡 Under arbeid lokalt
- ⬜ Planlagt
- 💡 Senere idé

## Nå

### 1.3 – Varsler i hus

- ✅ Publiser varselsensor automatisk til Home Assistant uten direkte port eller manuell REST-konfigurasjon.
- ✅ Lever et importerbart varseloppsett som blir en vanlig Home Assistant-automasjon.
- ✅ Erstatt den generelle systemstatusen med kompakt og faktisk status for varselsensoren.
- ⬜ Bekreft mobilvarsel og blueprint-import på en fysisk Home Assistant-installasjon.

### 0.5.2 – Tydelig NFC

- ✅ Vis om forbindelsen til Home Assistant er klar mens appen venter på NFC-tag.
- ⬜ Test automatisk NFC-kobling på en fysisk Home Assistant-installasjon.

### 0.6 – Raskere registrering

- ✅ Lag et enda enklere førstebilde for **Ny vare** og **Ny gjenstand**.
- ✅ La strekkodeskanning være den naturlige starten for forbruksvarer.
- ✅ La NFC kobles på som et enkelt neste steg, uten synlig Tag-ID.
- ✅ Foreslå navn, bilde, kategori, enhet og standardverdier automatisk.
- ✅ Gjør avanserte felt tilgjengelige ved behov, men skjult som standard.
- ✅ Gi en tydelig bekreftelse og naturlig neste handling etter lagring.

## Neste

### Bedre organisering

- ⬜ Gjør steder og kategorier enklere å opprette, endre og rydde.
- ⬜ Gjør det tydelig hvordan varer flyttes mellom steder.
- ⬜ Kontroller hele flyten på mobil før publisering.

### 0.7 – Bedre daglig bruk

- ✅ Gjør lagerjustering enda raskere med kompakte hurtigvalg.
- ✅ Forbedre søk med toleranse for små skrivefeil og relevante forslag.
- ✅ Gjør handlelisten rask å bruke i butikk, også med mange varer.
- ✅ Gruppér handlelisten på en nyttig måte, for eksempel etter kategori.
- ✅ Legg inn enkel angre-funksjon etter lagerendring.
- ✅ Gjør tomme tilstander og feilmeldinger konkrete og hjelpsomme.

### 0.8 – Oversikt og trygghet

- ✅ Lag en enkel startside med det viktigste: lav beholdning, utløpsdatoer og nylige handlinger.
- ✅ Gjør NFC-status synlig der NFC kobles, og vis faktisk varselsensorstatus der varsler settes opp.
- ✅ Gjør backup og gjenoppretting enklere å forstå og kontrollere.
- ✅ Legg inn import og eksport i et lesbart format.
- ✅ Forbedre historikken slik at man ser hva som ble endret og kan rette feil.

### 0.9 – Profesjonell finish

- ✅ Samordne typografi, ikoner, farger, avstander og knapper i hele appen.
- ✅ Kontrollere lys og mørk visning.
- ✅ Legge inn gode lastetilstander og små bekreftelser uten unødvendige popup-vinduer.
- 🟡 Gjennomføre full fysisk kameratest på iPhone og Android.
- ✅ Kontrollere tilgjengelighet: kontrast, trykkflater, tastatur og skjermleser.
- ✅ Forbedre installasjon, dokumentasjon og førstegangsopplevelse.

### 1.0 – Stabil utgave

- ✅ Lukke kjente feil og gjennomføre regresjonstest av alle hovedflyter.
- ✅ Teste oppgradering uten tap av eksisterende lagerdata.
- ✅ Ferdigstille navn, ikon, skjermbilder og enkel presentasjon.
- ✅ Publisere en ryddig 1.0-versjon med komplett endringslogg.

## Ferdig

### 0.5.1 – Bilder og automatisk NFC

- ✅ Valg fra bildebibliotek eller kamera med forhåndsvisning og komprimering.
- ✅ Forståelige bildefeil i stedet for svart side.
- ✅ Valg om å koble NFC rett etter opprettelse.
- ✅ Direkte lytting på Home Assistants NFC-hendelser uten IP eller manuell automasjon.

### 0.5.0 – Trygg oversikt

- ✅ Backup og kontrollert gjenoppretting.
- ✅ Varsler for lav beholdning og best før.
- ✅ Bedre tomme tilstander og førstegangsbruk.
- ✅ Enklere språk og flyt for gjenstander.

### 0.4.0 – Første komplette arbeidsflyt

- ✅ Skille mellom forbruksvarer og gjenstander.
- ✅ Forenklet registreringsskjema.
- ✅ Produktoppslag og produktbilder fra Open Food Facts.
- ✅ NFC-kobling, handleliste, sletting og kompakt mobilvisning.

### 0.3.0 og tidligere – Grunnlaget

- ✅ Mobilnavigasjon, lagerkort, søk og filtre.
- ✅ Strekkode- og QR-skanning.
- ✅ Lagerjustering, plassering, kategorier og Tag-ID.
- ✅ Home Assistant add-on med GitHub-oppdateringer.

## Senere ideer

- 💡 Flere brukere eller husholdninger.
- 💡 Utlån av verktøy og gjenstander.
- 💡 Flere bilder per gjenstand.
- 💡 Etiketter eller QR-koder som kan skrives ut.
- 💡 Statistikk over forbruk og foreslått innkjøpsmengde.
- 💡 Valgfri deling av handleliste utenfor Home Assistant.

## Arbeidsregel

Vi gjør normalt ett punkt ferdig om gangen:

1. Implementer lokalt.
2. Kontroller i mobilforhåndsvisningen.
3. Kjør automatiske tester.
4. Test på ekte Home Assistant når funksjonen krever det.
5. Oppdater denne roadmapen og changelog.
6. Øk versjonsnummer, commit og push når pakken er klar.
