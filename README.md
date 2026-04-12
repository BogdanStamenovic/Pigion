# Pigeon

Pigeon je lokalni autonomni agent framework zamišljen kao dedicated execution wrapper za mašinu koja je namenjena isključivo njemu. Trenutni fokus projekta je Watchdog, primarni agent koji planira, izvršava, evaluira i oporavlja se od grešaka unutar striktno ograničenog kontrolnog loop-a. Ostatak sistema je zamišljen hijerarhijski i odvojeno: Bossman, Cogmet, Cleaner i Mutormentor.

## Trenutno stanje projekta

Trenutno je implementiran Watchdog, odnosno glavni agent loop. Njegova svrha je da uzme jedan goal, razbije ga na ograničen broj koraka, izvršava samo trenutni korak, koristi alate preko tekstualnih akcija i po potrebi pokušava recovery. Pored toga postoji experience store koji čuva prethodne failure obrasce i njihove uspešne alternative kako bi recovery imao dodatni kontekst. Ceo flow je bounded i ne može da ostane zaglavljen beskonačno, jer svaki korak i svaki nivo retry-a imaju hardkodovane limite. fileciteturn5file0L13-L24 fileciteturn5file0L314-L351 fileciteturn5file0L773-L941

## Arhitektura

Trenutna i planirana arhitektura je hijerarhijska i modularna:

- Bossman: spoljašnji failsafe i sistemski rollback sloj za katastrofalne greške izazvane agentom.
- Cogmet: meta layer za health i evaluaciju na nivou završenih taskova.
- Cleaner: održavanje i uklanjanje redundansi iz experience baze.
- Mutormentor: eksperimentalni sloj za mutacije promptova i njihovo testiranje.
- Watchdog: glavni execution agent.

Watchdog je jedini deo koji je trenutno direktno implementiran u dostavljenom kodu. Ostali slojevi su planirani, ali još nisu integrisani u runtime prikazan ovde. fileciteturn5file0L773-L941

## Kako Watchdog radi trenutno

### 1. Konfiguracija i budžeti

Watchdog koristi niz runtime parametara kroz environment promenljive. Najbitnije su:

- model za LLM pozive
- temperatura
- maksimalan broj izlaznih tokena po pozivu
- maksimalan broj akcija po koraku
- maksimalan broj LLM retry pokušaja
- maksimalan broj recovery pokušaja po koraku
- ukupan token budget po goal-u
- putanja do experience baze
- broj sličnih failure primera koji se vraćaju recovery fazi

Token accounting je trenutno aproksimacija zasnovana na dužini prompta, a ne stvarni usage iz API odgovora. Experience baza se čuva kao JSONL fajl. fileciteturn5file0L13-L24 fileciteturn5file0L44-L71 fileciteturn5file0L275-L313

### 2. Učitavanje okruženja i dokumentacije alata

Na startu se pokušava učitavanje dva tekstualna fajla:

- `td.txt` za opis dostupnih alata
- `enving.txt` za opis okruženja

Ti tekstovi se kasnije direktno ubacuju u system prompt, tako da Watchdog u svakom LLM pozivu dobija isto objašnjenje okruženja i tool surface-a. Ako fajlovi ne postoje, koriste se fallback poruke. fileciteturn5file0L28-L41

### 3. JSON disciplina i ekstrakcija izlaza

Watchdog očekuje da model uvek vraća validan JSON objekat. Zbog toga:

- gradi prompt koji više puta insistira na čistom JSON izlazu
- pokušava prvo direktan `json.loads`
- ako to ne uspe, pokušava da izdvoji prvi validan JSON objekat iz sirovog teksta

Ovo je osnovni mehanizam koji drži agent determinističnijim i kompatibilnim sa ostatkom kontrolnog loop-a. fileciteturn5file0L49-L71

### 4. Experience store

Experience store služi za čuvanje prethodnih failure događaja i uspešnih alternativa. Svaki entry sadrži:

- `name`: generički tip greške
- `reason`: razlog ili opis greške
- `alternative`: akciju koja je kasnije pomogla
- `step`: korak u kome se greška desila
- `failed_action`: akciju koja je failovala
- `successful_action`: akciju koja je kasnije bila uspešna
- `created_at`: timestamp

Prilikom učitavanja baze svi entry-ji se tokenizuju i pretvaraju u sparse vektore. Sličnost se računa cosine similarity pristupom nad kombinacijom `name`, `reason`, `step` i `failed_action`. Rezultat recovery fazi vraća top K najsličnijih prethodnih failova. fileciteturn5file0L74-L182

### 5. Klasifikacija failure događaja

Kada dođe do faila, Watchdog pokušava da ga svede na generičko ime greške pomoću determinističke funkcije `infer_failure_name`. Trenutno podržani tipovi uključuju:

- `permission_denied`
- `repository_not_found`
- `module_not_found`
- `path_not_found`
- `tool_timeout`
- `rate_limited`
- `json_parse_failed`
- `dependency_install_failed`
- `shell_command_failed`
- `search_failed`
- `memory_write_failed`
- `generic_step_failure`

Ovaj layer je važan jer experience retrieval ne radi samo po slobodnom tekstu, nego i po stabilnom failure identitetu. fileciteturn5file0L185-L212

### 6. Konstrukcija system prompt-a

Glavna funkcija za prompt building ubacuje u system prompt sledeće:

- environment opis
- tool docs
- global goal
- plan framework
- indeks trenutnog koraka
- tekst trenutnog koraka
- listu završenih koraka
- internu memory vrednost
- runtime state
- skraćenu istoriju poslednjih akcija
- informaciju o procenjenoj token potrošnji
- niz striktnih execution pravila

Najvažnija pravila su:

- radi samo na trenutnom koraku
- nema subplanova
- nema preskakanja unapred
- jedna akcija po odluci
- status može biti `ongoing`, `done` ili `fail`
- izlaz mora biti validan JSON

To znači da je Watchdog trenutno vrlo prompt-driven i da dosta discipline dobija iz velikog system prompt-a, a ne iz mnogo spoljne logike. fileciteturn5file0L231-L273

### 7. LLM pozivi

`call_llm` je jedino mesto koje direktno zove model. Tok rada je sledeći:

1. spaja system prompt i task-specific prompt
2. dodaje dodatni disclaimer koji ponavlja da izlaz mora biti JSON
3. procenjuje dodatnu token potrošnju
4. pokušava poziv do `MAX_LLM_RETRIES` puta
5. loguje raw model output
6. parsira rezultat u JSON

Ako svi pokušaji propadnu, baca runtime grešku. fileciteturn5file0L275-L313

### 8. Planiranje

Na početku svakog goal-a Watchdog prvo traži od modela plan. Plan mora da bude:

- između 3 i 7 koraka
- high-level
- bez tool call-ova
- bez subplanova

Ako plan nije validna lista, izvršavanje se prekida. Plan je samo okvir; kasnije se i dalje radi strogo korak po korak. fileciteturn5file0L314-L351

### 9. Biranje sledeće akcije

Za svaki trenutni korak Watchdog traži od modela tačno jednu sledeću izvršivu akciju. Model vraća:

- `status`
- `reason`
- `next_action`

Akcija je tekstualna komanda tipa:

- `shell:...`
- `search:...`
- `memadd:...`
- `return:...`

Postoji i mehanizam `force_next_action` koji recovery faza može da upiše u state. Kada je on postavljen, sledeća akcija se ne bira preko modela već se direktno izvršava. fileciteturn5file0L354-L408

### 10. Evaluacija poslednje akcije

Posle svake izvršene akcije, Watchdog ponovo zove model da proceni samo poslednju akciju u kontekstu trenutnog koraka. Evaluacija vraća:

- `ongoing`
- `done`
- `fail`

Ovo je druga grana LLM logike pored biranja sledeće akcije. Trenutno postoji odvojena evaluaciona faza, pa Watchdog radi odluku, zatim tool execution, zatim evaluaciju tog output-a. fileciteturn5file0L411-L456

### 11. Recovery faza

Kada odluka ili evaluacija označe fail, aktivira se recovery. Recovery prompt dobija:

- trenutni korak
- opis greške
- listu sličnih prethodnih failure primera iz experience store-a

Model tada bira jedan od recovery modova:

- `retry`
- `replace_step`
- `skip_step`
- `abort_goal`

To recovery logici daje mogućnost da:

- proba novi konkretan sledeći potez
- promeni formulaciju koraka
- preskoči korak ako nije bitan ili je praktično završen
- potpuno prekine goal ako dalji rad nema smisla ili nije bezbedan

Broj recovery pokušaja je takođe bounded. fileciteturn5file0L459-L499 fileciteturn5file0L629-L771

### 12. Izvršavanje alata

Watchdog trenutno podržava četiri tipa akcija:

#### `return:`
Dodaje tekst u završni `returned_output` i ažurira state. fileciteturn5file0L506-L518

#### `search:`
Poziva `search(query)` i rezultat čuva kao `last_tool_output`. fileciteturn5file0L520-L529

#### `shell:`
Poziva `shell(command)` i rezultat čuva kao `last_tool_output`. Ovo je najmoćniji alat, jer agentu daje direktan shell surface. fileciteturn5file0L531-L540

#### `memadd:`
Dodaje vrednost u internu memory ako se ista vrednost već ne nalazi na kraju memory stringa. Održava i `memory_items` listu u state-u. fileciteturn5file0L542-L566

Ako alat nije prepoznat, funkcija vraća `UNKNOWN_TOOL`. fileciteturn5file0L568-L575

### 13. Pending failure i upis iskustva

Kada se fail detektuje, Watchdog formira `pending_failure` objekat koji sadrži:

- failure name
- failure reason
- step
- failed action
- timestamp

Kasnije, kada neka naredna akcija uspešno zatvori korak, `finalize_experience_if_needed` upisuje novi entry u experience store. Na taj način sistem pokušava da pamti koje alternative su bile korisne nakon određenog failure obrasca. fileciteturn5file0L578-L627

### 14. Glavni execution loop

Glavni loop radi ovako:

1. resetuje runtime state i token brojač
2. učitava experience store
3. generiše plan
4. ulazi u petlju po koracima
5. za svaki korak pokreće akcione runde
6. bira akciju
7. izvršava alat
8. evaluira ishod
9. po potrebi radi recovery
10. prelazi na sledeći korak kada je trenutni završen

Ograničenja su hardkodovana:

- 3 do 7 plan koraka
- do 12 akcija po koraku
- do 6 LLM retry pokušaja po pozivu
- do 6 recovery pokušaja po koraku
- token budget po goal-u

Ako korak ne može da se završi u zadatim granicama, goal se prekida greškom. To znači da je izvršavanje bounded i ne postoji beskonačni loop unutar jednog goal-a. fileciteturn5file0L773-L941

## Trenutna svojstva Watchdog-a

Trenutni Watchdog ima sledeće osobine:

- planira pre izvršavanja
- izvršava samo jedan korak u datom trenutku
- bira jednu akciju po iteraciji
- koristi poseban evaluacioni prolaz nakon svake akcije
- ima recovery mehanizam koji koristi prethodna iskustva
- ima tvrde limite na korake, akcije, retry i budžet
- može da izvršava shell komande
- čuva iskustvo u lokalnom JSONL store-u
- koristi veliki system prompt kao glavni izvor discipline

## TODO

### Watchdog

- optimizovati token usage
- smanjiti veličinu system prompt-a bez gubitka discipline
- podeliti promptove po fazama umesto jednog univerzalnog system prompt-a
- izbaciti nepotrebna grananja u execution flow-u
- ukloniti ili spojiti dupliranu evaluacionu logiku gde je moguće
- unaprediti klasifikaciju failure događaja i preciznost experience retrieval-a
- dodati bolji mehanizam za fallback na externals kada task nije prirodno rešiv postojećim alatima
- preći sa aproksimacije tokena na stvarni usage iz model response-a
- dodatno smanjiti količinu state-a i history-ja koji se šalje modelu

### Bossman

- implementirati odvojeni failsafe sloj izvan Watchdog-a
- uvesti snapshot i rollback mehanizam za katastrofalne sistemske greške izazvane agentom
- napraviti verzionisanje kritičnih fajlova i konfiguracije
- uvesti restore logiku za slučajeve kada agent ošteti sopstveni runtime ili core fajlove
- definisati minimalan i robustan signal kojim Cogmet ili drugi sloj mogu da traže intervenciju Bossman-a

### Cogmet

- implementirati meta layer koji se poziva po završetku taska
- računati health i kvalitet izvršavanja na nivou taska
- pratiti metrike kao što su broj koraka, broj akcija, broj recovery pokušaja, status završetka i efikasnost
- računati dugoročne proseke performansi za poređenje verzija promptova ili ponašanja sistema
- odlučivati kada sistem pokazuje degradaciju koja zahteva signal prema Bossman-u ili Mutormentor-u

### Cleaner

- uklanjati redundanse u experience bazi
- spajati ili deduplikovati semantički iste entry-je
- održavati experience store malim i korisnim
- uklanjati loše ili zastarele pattern-e koji više ne donose vrednost recovery fazi
- pripremati experience bazu za efikasniji retrieval

### Mutormentor

- implementirati odvojeni mutation engine za promptove i eventualno konfiguracione parametre
- generisati male, kontrolisane mutacije umesto potpunog rewrite-a promptova
- testirati staru i novu verziju na kontrolisanom test setu
- puštati novu verziju samo ako je bolja na testu
- pratiti prosečne metrike nove verzije kroz duži period i na osnovu toga potvrđivati ili odbacivati mutaciju
- revertovati promene ako nova verzija dugoročno pogorša statistike
- nakon više uzastopnih reverta tretirati trenutnu verziju kao stabilnu osnovu
- omogućiti ručni override od strane korisnika

## Napomena o hijerarhiji

Pigeon nije zamišljen kao jedan proces sa više internih submodula koji svi rade istovremeno. Ideja je hijerarhijska i odvojena:

- Watchdog izvršava taskove
- Cogmet evaluira health i rezultate
- Bossman reaguje samo kod katastrofalnih sistemskih posledica
- Cleaner radi kada su odgovarajući execution slojevi ugašeni
- Mutormentor radi kada ostali delovi sistema nisu aktivni

Time se zadržava jasan separation of concerns i izbegava se da recovery, maintenance i evolucija sistema budu pomešani sa samim izvršavanjem zadataka.
