# Configurare Xcode IPA Installer

[English](SETUP.md) · [README italiano](../README.it.md)

## 1. Accedere all’account dentro Xcode

Installa Xcode completo, avvialo almeno una volta e completa le operazioni iniziali, inclusi gli strumenti iOS. I soli Command Line Tools non bastano. Usa una versione completa di Xcode compatibile con macOS e con iOS dell’iPhone. L’app supporta macOS 12+: su Xcode precedente alla 15 usa xcdevice per trovare i dispositivi e ios-deploy, da installare separatamente, per installare via USB su iOS 16 o precedenti. Per iOS 17+ servono Xcode 15+ e macOS compatibile.

1. Con Xcode aperto, premi **⌘ ,** oppure scegli **Xcode → Settings…** dalla barra dei menu.
2. Nella barra laterale scegli **Apple Accounts**. Nelle versioni precedenti può chiamarsi **Accounts** e il menu **Preferences…**.
3. Premi **+** oppure **Add Apple Account…**, scegli Apple Account/Apple ID se richiesto e completa l’accesso.
4. Inserisci la 2FA **nella finestra di Xcode**. Verifica poi che il tuo account e il relativo team compaiano nell’elenco.

![Dove aggiungere un account in Xcode](images/xcode-apple-accounts.it.svg)

*Schema illustrativo: i nomi dei controlli possono cambiare tra versioni. Non mostra account reali.*

Puoi aggiungere più account. Non serve accedere all’iCloud principale del Mac con ciascuno di essi. Xcode IPA Installer riutilizza la sessione gestita da Xcode; non richiede di copiarne i token. Se Xcode segnala sessione scaduta o accordi da accettare, completa la procedura lì e premi **Aggiorna** nell’installer. [Guida Apple agli account Xcode](https://help.apple.com/xcode/mac/current/en.lproj/devaf282080a.html)

## 2. Preparare l’iPhone

Collega l’iPhone al Mac via USB, sbloccalo e conferma **Autorizza questo computer** se richiesto. In Xcode puoi controllare l’associazione da **Window → Devices and Simulators**.

Sull’iPhone apri **Impostazioni → Privacy e sicurezza → Modalità sviluppatore**, attivala e completa riavvio e conferma. Se la voce non compare, completa prima l’associazione con Xcode. Premi poi **Aggiorna** nell’installer. La prima configurazione via USB è preferibile; la connessione via rete richiede che Xcode abbia già associato e renda raggiungibile il dispositivo. [Modalità sviluppatore: guida Apple](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)

Anche **Solo firma** richiede un iPhone selezionato: il provisioning di sviluppo viene verificato per quel dispositivo.

## 3. Configurare il team

Apri l’app scaricata dalla [pagina Releases](https://github.com/dre4moff/Xcode-IPA-Installer/releases/latest). Seleziona l’account Xcode e il suo team. Quando Xcode non espone il team nella cache locale, usa **Configura team…**, disponibile anche in **Opzioni**.

Inserisci un nome riconoscibile e il **Team ID di 10 lettere/numeri**. Se è il team gratuito personale, lascia attiva la voce **Personal Team (account gratuito)**. Questa configurazione associa solo metadati locali; non effettua un nuovo login e non crea un team.

Per recuperare il Team ID:

- Per un abbonamento Apple Developer Program, consulta i dettagli dell’iscrizione del team su [Apple Developer Account](https://developer.apple.com/account).
- Per un Personal Team, in Xcode crea un piccolo progetto **iOS → App**, apri il target → **Signing & Capabilities**, abilita **Automatically manage signing** e scegli il tuo Personal Team. In **Build Settings** cerca **Development Team** / `DEVELOPMENT_TEAM`; se vedi solo il nome, apri il file `project.pbxproj` del progetto con un editor e cerca la voce `DEVELOPMENT_TEAM = ...;`. Non occorre compilare o installare questo progetto.

In alternativa, per un progetto già configurato:

```sh
xcrun xcodebuild -project "/percorso/Prova.xcodeproj" \
  -scheme "Prova" -showBuildSettings |
  awk '/ DEVELOPMENT_TEAM = / { print $3; exit }'
```

Sostituisci percorso e schema. Non usare come Team ID il codice tra parentesi nel nome del certificato: può essere un identificativo diverso.

Se lo stesso team è associato a più account autenticati in Xcode, l’installer segnala l’ambiguità prima di firmare. Scegli un Personal Team distinto o risolvi l’associazione in Xcode; la selezione tramite Team ID non identifica un singolo membro di un team condiviso.

## 4. Firmare un’IPA

1. Premi **Scegli IPA…** o trascina il file nella finestra.
2. Controlla **account, team e iPhone**.
3. Lascia **Increased Memory Limit** attivo se l’app lo richiede. Si applica solo al principale; tutti gli altri componenti vengono comunque firmati.
4. Usa **Solo firma** per esportare oppure **Firma e installa** per esportare e installare sull’iPhone selezionato.

L’originale non viene modificato. In Download trovi l’IPA firmata e il rapporto `.verification.json`. Dal rapporto puoi controllare team, scadenza, componenti firmati e stato della capability memoria. Se l’installazione fallisce dopo l’esportazione, questi file possono essere già disponibili.

Xcode può richiedere l’accesso alla chiave di firma nel Portachiavi. Gestisci personalmente eventuali richieste di macOS. Se iOS richiede di autorizzare lo sviluppatore, controlla **Impostazioni → Generali → VPN e gestione dispositivo** sull’iPhone.

Il principale usa **l’ID originale più il Team ID dell’account selezionato**, per esempio `com.example.App.ABCDEFGHIJ`. Se il suffisso corretto è già presente, non viene duplicato. In **Opzioni** puoi leggere l’identificativo finale; cambiando team cambia anche il suffisso. Non vengono aggiunti prefissi o identificativi del Mac.

Gli identificativi delle estensioni e delle app incorporate vengono aggiornati sotto quello del principale, conservando le relazioni: ad esempio `com.example.App.ABCDEFGHIJ.Widget`. Il backend verifica che ogni profilo e firma appartengano al team selezionato e non accetta un ID finale diverso da quello previsto. Il rapporto registra gli ID originali e quelli di firma.

Per aggiornare un’app esistente servono lo stesso Bundle ID completo e una firma compatibile. Se la copia sull’iPhone usa già `<ID originale>.<Team ID>`, lo schema della versione 1.0.5 coincide. Un ID diverso, inclusi i prefissi delle vecchie versioni dell’installer, resta un’altra app: non vengono migrati i dati tra identificativi diversi.

## 5. Increased Memory Limit ed EVA

Le due capability sono indipendenti:

| Opzione | Comportamento |
| --- | --- |
| Increased Memory Limit attivo | Richiede `com.apple.developer.kernel.increased-memory-limit` nel profilo e nella firma del principale. Se manca, interrompe la procedura. |
| Increased Memory Limit disattivo | Esclude quell’entitlement anche se presente nell’IPA originale. |
| EVA presente nell’IPA, team compatibile | Conserva `com.apple.developer.kernel.extended-virtual-addressing` sul principale. |
| EVA non disponibile per il team | La esclude automaticamente. I Personal Team vengono riconosciuti prima del provisioning; un rifiuto esplicito di Xcode su un altro team causa un unico nuovo tentativo senza EVA. |
| Processi ausiliari | App incorporate, estensioni e JIT helper vengono firmati senza capability memoria. Framework e librerie vengono firmati come componenti del processo che li carica. |

La gestione è generale per tutte le IPA. EVA non viene aggiunta a un’app che non la richiede. L’esclusione di EVA non disattiva Increased Memory Limit. Le altre capability originali restano richieste, salvo che tu disattivi esplicitamente **Mantieni le capability originali**. Nessuna di queste opzioni aggiunge RAM fisica.

## Importare un certificato invece di usare Xcode

Scegli **Firma → Certificato importato → Importa certificato…**. Seleziona P12/PFX e uno o più mobileprovision, oppure la cartella che li contiene. Inserisci la password nel campo protetto, lasciandola vuota se assente; i file di testo vengono ignorati. Dopo l’importazione scegli il profilo principale e un iPhone incluso nel profilo, poi Solo firma o Firma e installa. Questa modalità non richiede l’accesso a un account Apple.

La chiave privata viene importata nel Portachiavi predefinito; la password non viene salvata. Gestisci personalmente eventuali richieste di macOS. Aggiungi i profili delle estensioni dal menu di gestione. Il profilo esatto del principale non copre estensioni arbitrarie: servono profili compatibili o una wildcard appropriata. I permessi mancanti causano un errore, senza saltare componenti. [Dettagli su profili e JIT](../README.it.md#certificati-e-profili-importati).

## Problemi comuni

| Messaggio o sintomo | Cosa controllare |
| --- | --- |
| Nessun account / sessione scaduta | Completa l’accesso in Xcode → Settings → Apple Accounts e aggiorna l’elenco. |
| Team assente | Associa il Team ID corretto da **Configura team…**; controlla se è un Personal Team. |
| iPhone assente o non raggiungibile | Cavo, autorizzazione, sblocco, supporto iOS di Xcode e Window → Devices and Simulators. |
| Bundle Identifier non disponibile | Apple non autorizza l’ID con il suffisso del team. L’installer interrompe la procedura senza generare identificativi alternativi. |
| Limite di app con profili gratuiti raggiunto | Il messaggio mostra gli identificativi che occupano gli slot. Un aggiornamento deve avere lo stesso ID della copia esistente e una firma compatibile; un ID diverso occupa un nuovo slot. |
| Extended Virtual Addressing non supportata | La versione 1.0.3 la esclude automaticamente per il team che non la autorizza, preservando la richiesta Increased Memory Limit. |
| Increased Memory Limit non autorizzato | La procedura si ferma. Verifica team e profilo: l’app non dichiara riuscita la firma senza la capability richiesta. |
| App Groups, iCloud o push non autorizzati | Il nuovo team deve possedere le autorizzazioni. Disattivare le capability originali può compromettere le funzioni che le usano. |
| IPA cifrata / componenti Watch | Questi pacchetti non sono supportati. |
| App installata ma funzione assente | La firma non garantisce codice, backend remoto, Live Activities, Dynamic Island o servizi del team originale. Controlla il rapporto e i log. |

I limiti, le scadenze e le capability disponibili dipendono dal provisioning Apple e dal tipo di team. L’installer non li aggira. Conserva l’IPA sorgente e i dati locali dell’installer; prima di pubblicare log o rapporti rimuovi i tuoi identificativi personali.

## Lingua e ridimensionamento

La lingua segue le preferenze di macOS, con inglese e italiano e fallback inglese. Non è presente un selettore nell’app. Scorri il contenuto per raggiungere tutte le opzioni: i comandi di firma restano in basso. Liquid Glass da macOS 26, materiali nativi sulle versioni precedenti.
