> Terjemahan turunan — jangan sunting; sumber: 0033-neutral-core.md (sha256: c1e8a98e5d51)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0033: Core Netral, Tepi Terspesialisasi — Tanpa Budaya, Transport, atau Vendor di Dalam Mekanisme

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0000 (bentuk permukaan dan data bahasa), ADR-0009 (rumah ADR dan gate-nya), ADR-0018 (kontrak gateway), ADR-0027 (clean by default), ADR-0028 (unit plugin), ADR-0031 (tata letak, anggaran pertumbuhan, gate), ADR-0032 (satu pintu)
- **Supersedes:** —
- **Terkait:** ADR-0006 (plafon privasi), ADR-0007 (kelas approval), ADR-0014 (automation), ADR-0019 (`/status`), ADR-0022 (role), ADR-0025 (prefix prompt stabil)
- **Implementasi:** partial — §4.2-§4.4, §4.6, §4.7, §4.9, §4.11-§4.13 sudah mendarat bersama gate-nya (`scripts/neutral.py --check`, suite kontrak prompt, migrasi 0014-0016). Masih terbuka dari §4.5: seam relay/`dm_thread`, protokol `Chat`, digest grup, render kutipan, dan placeholder media di `stores/style.py`.
- **Catatan lokasi:** lintas-potong (mengatur setiap permukaan engine), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0033-neutral-core.md`.

---

## 1. Konteks

Engine sudah punya *bentuk* yang benar untuk perilaku berbasis data: string chat hidup di `eidesoul/lang/<code>.yaml` (dua katalog, 249 key masing-masing), data karakter hidup di `eidesoul/library/`, dan sebuah ekstensi mendeklarasikan dirinya di manifest. Yang **belum** dimiliki adalah aturan yang menjaga mekanisme dan data tetap terpisah. Budaya, geografi, transport, dan vendor ditulis di dalam mekanismenya sendiri, sehingga deployment di luar satu negara atau satu transport harus menyunting kode. Diukur pada 2026-11-04 lewat sapuan penuh atas `eidesoul/engine`, `eidesoul/admin`, `__main__.py`, template yang dikirim, image, dan `scripts/`.

### 1.1 Bahasa adalah default, dua puluh lima kali

- Dua puluh empat signature membawa `language: str = "id"`: sembilan yang pertama dihitung (`character/character.py:53,61,68,116`, `guard/patterns.py:33,45,50`, `jobs/ambient.py:28`, `jobs/cognition.py:373`) plus `turn/prompts.py:121`, `turn/context.py:317`, `turn/salience.py:49,62`, `memory/gate.py:63`, `memory/assess.py:133`, `memory/reflect.py:96,125,267`, `memory/store.py:681,1086`, `stores/affect.py:917`, `stores/history.py:280`.
- Delapan pemanggil jatuh dengan `or "id"`: `agent.py:897`, `access/privacy.py:93`, `access/relay.py:218`, `commands/__init__.py:31`, `jobs/automation.py:1944`, `jobs/cognition.py:387`, `jobs/supervisor.py:270`, `turn/prompts.py:239,247`.
- Default level modul sejalan: `soul.py:60`, `i18n.py:50`, `i18n.ALIASES["auto"]`, `llm.py:51`, dan `soul.py:82,251` yang mengoper `"id"` ke `normalize_language`.
- `soul.py:106-111` tidak memakai katalog sama sekali: `if language == "en": "Reply in English."` dan setiap bahasa lain diberi tahu **"Reply in Indonesian (Bahasa Indonesia)"**.
- Kedua katalog membawa 249 key yang sama, jadi ini kebijakan, bukan batas kemampuan.

### 1.2 Budaya adalah tabel kata, delapan buah

- `guard/patterns.py:15-19` meng-hardcode frasa krisis (`bunuh diri`, `mau mati`, `gantung diri`, `overdosis`, …) dan regex token sensitif yang memuat `kredensial`, padahal docstring modulnya sendiri mengklaim pola itu "language-independent".
- `character/character.py:188-211` — `SEVERITY`, tujuh belas kata Indonesia; `:31` juga menyebut `pantun` sebagai bentuk yang dipertahankan.
- `access/target.py:22-26` — daftar kata kasar kedua, dengan penanda Indonesia di `:21-23,39-40`.
- `access/privacy.py:27,32-46` — `YES` (`ya`, `oke`, `boleh`, `sip`, `gas`, `lanjut`, `silakan`) dan `LEVEL_WORDS`.
- `character/patterns.py:27` — `SERIOUS_REGISTERS = {"serious", "comfort", "soal"}`.
- `stores/style.py` yang terbesar: `:22` `PLACEHOLDERS = ("[Stiker]","[Gambar]","[Dokumen]","[Video]","[Audio]")`, `:27` `LAUGH = (wkwk|awok|hehe|haha)`, `:29-32` `PARTICLES` (dua puluh tiga partikel Indonesia/Jawa), `:33,59` `EMOJIS`, dan `:34-60` `SEED` (median 34, p90 130, partikel, emoji). Itu adalah baseline untuk **setiap** soul: `:343-357` menyemai chat berbahasa Inggris dengan ritme Indonesia.
- `self_knowledge.py:70-77` — `TOPIC_BEHAVIORS` berkunci `ruangan`, `ingatan`, `izin`, `inisiatif`, `gagal`, `batas`; `:313-325` merender `## {key}` sebagai heading prompt dan bercabang pada `"izin"`, `"inisiatif"`, `"ingatan"`; `:462-465` jatuh ke `"id"` untuk nama README.
- `config.py:388` — enam id Indonesia yang sama sebagai default config, sehingga `selfcheck` mencetak `topics=ruangan, ingatan, izin, inisiatif, gagal, batas`.
- `commands/memori.py:58,75,90,126` — DSL verbanya dua bahasa (`{daftar,list}`, `{cari,search}`, `{hapus,forget,lupakan}`, `{tambah,add,simpan}`), dan `lang/en.yaml:121` membocorkan verb Indonesia itu ke baris usage **Inggris**.

### 1.3 Geografi dan jam

- `jobs/schedule.py:16` menetapkan `DEFAULT_TZ = "Asia/Jakarta"`, dipakai `zone()` dan `_next_occurrence`, dan `jobs/automation.py:155,1133` menyuntiknya kembali saat memvalidasi manifest dan saat menjadwalkan, sehingga manifest tanpa `tz` diam-diam berjalan di waktu Jakarta.
- `Dockerfile:5` menetapkan `TZ=Asia/Jakarta` untuk seluruh proses.
- `soul.py:30,65,256` men-default `locale: id-ID` tiga kali, `turn/prompts.py:178-191` men-default `research_block(locale="id-ID")` dan memerintahkan "local government, economy, currency, energy, food prices, and public figures" — daftar topik `currents` yang ditulis ulang sebagai prosa, jadi menghapus modulnya tidak menyelesaikan §4.4.
- `access/target.py:126-133` membangun prompt yang menyebut "an Indonesian chat app" ketika locale berawalan `id`.
- `tools/web_search.py:16` menyebut empat mesin pencari di `WEB_BACKENDS` dan `:64` membiarkan mesin ddgs pada default `region="us-en"`, sehingga pencarian diam-diam berorientasi Amerika.
- Pekerjaan kalender membaca jam container: `turn/prompts.py:498`, `jobs/automation.py:1539`, dan `jobs/ambient.py:315` memanggil `date.today()`; `memory/context.py:82`, `memory/ledger.py:25`, dan `stores/affect.py:835` memakai `time.localtime`, sementara `memory/store.py:2216` memakai `gmtime`, jadi dua batas hari hidup bersama.
- `tools/sandbox.py:17` dan `config.py:250-259` meneruskan `TZ` dan `LANG` host ke anak sandbox dan shell.
- `jobs/schedule.py:76-84` meng-hardcode nama hari Inggris berawal Senin dan hanya menurunkan huruf pada cabang list, sehingga `days: "Mon"` ditolak sementara `days: ["mon"]` diterima.
- `jobs/currents.py:18-31` membawa topik satu negara (`pemerintah`, `ekonomi`, `rupiah`, `prabowo`, `sawit`, `surabaya`), sufiks query `" Indonesia {YEAR} terbaru"`, `stores/currents.py` menyimpannya, dan `commands/konteks.py:1,72` mendokumentasikan dirinya sebagai "the Indonesia (+62) context".

### 1.4 Vendor dan plugin

- `admin/templates.py:70` menulis `plugins: {"google": …}` ke setiap config host baru, `:335-345` memberi tool plugin `google` kepada role `member` dan `guest`, dan `:389-397` menulis tiga nama environment Google padahal manifest hanya mendeklarasikan satu (`plugins/google/plugin.yaml`, `secrets: [GOOGLE_SERVICE_ACCOUNT_PATH]`).
- `schema.py:927-946` mendefinisikan `google_identity` dan `google_device` di **DDL core**, dan kode core membacanya: `access/identity.py:22-23,345`, `admin/people.py:14,90`.
- `admin/people.py:57-59` jatuh ke `("whatsapp", value)` saat menjumpai id legacy.
- `memory/episodes.py:149-161` memetakan nama tool ke target type, termasuk tool plugin `google` dan tool gateway `send_to` serta `chat_history`.
- `tools/settings.py:33-37` menutup jenis dokumen ke `("engine","host","gateway","soul","role")`.
- Dependensi spine dinamai di core secara desain (ADR-0001, ADR-0031): `embed.py:15` memaku model MiniLM, `memory/mem0.py:19,186-204,213,223` menjalankan Mem0 lewat provider `openai` dan `huggingface`, dan `llm.py:1,10,177` memakai klien wire OpenAI. Itu adalah pengecualian yang harus dituliskan §4.12, bukan pelanggaran.

### 1.5 Transport

- `turn/prompts.py` memakai kosakata chat pada lima puluh dua baris. Yang menanggung beban: `:309` "You live in chat as {name}", `:319` "real mentions, DMs, and replies to your messages must be answered", `:347-350` "This chat is a direct message (DM)", `:360-372` "one soul across rooms … chat_history reads another archived room … human typing from the same number", `:379-384` semantik deleted / `view-once` / `undecryptable`, `:408-412` kutipan berlingkup grup, `:427-432` stiker animasi dan voice note, `:441-449` `send_to` sebagai satu-satunya jalan ke ruang lain.
- `turn/decision.py` memakai dua puluh baris: `ACTS = {"reply","ignore","react"}` (`:51`), `bubbles` di skema keputusan (`:40-45`), dan blok prompt yang dibangun di atas mention, DM, dan reaksi stiker.
- `stores/style.py:22` merender media sebagai `[Stiker]`, `[Gambar]`, `[Dokumen]`; `turn/context.py:506-513` merender kutipan sebagai `↳ replying to …`; keduanya render body di core.
- Lima yang pertama dihitung tetap: `access/relay.py:1-10` mengikat approval ke "the content owner's DM", `stores/history.py:683` mengekspos `dm_thread` ke `access/privacy.py:202`, `jobs/automation.py:1673`, dan `jobs/cognition.py:1008`; `llm.py:33,648-655` meringkas grup chat; `inbound.py` mendefinisikan protokol `Chat`.

### 1.6 Nama, legacy, dan permukaan ekstensi

- Empat command bernama Indonesia (`commands/cari.py`, `kirim.py`, `konteks.py`, `memori.py`), enam alias command dan dua alias tool melakukan hal yang sama (`tenang`, `bantuan`, `bahasa`, `tautkan`, `ingat`, `lepas`; `tools/web_search.py:107`, `tools/memory.py:201`), 76 key katalog dinamai menurutnya, dan `admin/templates.py:354-356,380` memberikan nama itu ke role.
- `access/roles.py:20` menyimpan `LEGACY = "owner"` dengan jalur migrasi di `admin/roles.py`, `admin/people.py:22` menyimpan `LEGACY_GATEWAY = "legacy"`, dan `memory/ledger.py:104` serta `stores/history.py:746` menyimpan "shutdown compatibility hooks".
- Ekstensibilitas ditutup oleh tabel nama, bukan metadata: `guard/policy.py:18` menentukan kelas approval dari `AUTHORIZATION_TOOLS = {"shell","sandbox","settings","send","send_to"}`, `:108-111` dari `DESTRUCTIVE_ACTIONS`, dan `:289-356` dari cabang per-nama; `tools/__init__.py:23` menutup `FAMILIES` ke enam nama dan `plugins.py:508-511` memvalidasi tool plugin terhadap himpunan itu, sehingga tool pihak ketiga tidak bisa ada sama sekali; `tools/__init__.py:349-367` membaca `args["path"]` dan mengkhususkan `sandbox` berdasarkan nama.

Dua konsekuensi mengikuti. Pertama, **keselamatan tidak portabel**: jaring krisis mencakup satu rumpun bahasa, gate register bersandar pada gender yang ditebak, dan kelas approval tidak dapat mendeskripsikan tool yang belum pernah didengarnya. Kedua, **core tidak dapat diperluas**: setiap negara, transport, plugin, atau tool baru menyunting mekanisme, yang justru merupakan churn yang hendak dicegah oleh anggaran pertumbuhan ADR-0031.

## 2. Decision drivers

1. **Netralitas harus dapat dieksekusi, bukan diingat.** Checklist review sudah gagal: setiap item di atas bertahan melewati beberapa putaran review karena "berjalan".
2. **Keselamatan tidak boleh bergantung pada satu negara.** Deteksi krisis adalah perilaku spine dan harus mencakup setiap bahasa yang diklaim engine.
3. **Keselamatan tidak boleh bergantung pada tabel nama.** Kelas approval yang diputuskan oleh nama tool berarti tool plugin entah tak bisa dikategorikan atau salah kelas tanpa suara.
4. **Instalasi adalah kesan pertama.** `eidesoul init` harus menghasilkan host yang tidak menyebut vendor, negara, transport, maupun bahasa apa pun.
5. **Ekstensi tidak boleh menyunting core.** Plugin, gateway, tool, atau bahasa baru menambah data dan manifest; ia tidak pernah menyentuh `eidesoul/engine/**` atau `eidesoul/admin/templates.py`.
6. **Penolakan, bukan fallback diam.** Pack, timezone, locale, atau plugin yang hilang adalah error eksplisit yang menyebut apa yang hilang — tidak pernah berupa default Indonesia, Inggris, atau Jakarta yang dipilih atas nama operator.
7. **Satu gate, mekanis.** Aturannya hidup di skrip yang dijalankan suite, dengan inventaris yang terdaftar, bukan di prosa.

## 3. Opsi yang dipertimbangkan

**A. Status quo plus disiplin review.** Ditolak: §1 adalah bukti bahwa review saja tidak menjaga garis.

**B. Override konfigurasi.** Tambahkan setelan `soul.language` dan `engine.timezone` lalu dokumentasikan. Ditolak sebagai tidak memadai: override menyembunyikan default alih-alih menghapusnya, dan tidak melakukan apa pun untuk tabel kata, nama vendor, tabel nama, atau jaring krisis.

**C. Pack, manifest, metadata tool, dan satu gate mekanis (dipilih).** Setiap fakta kultural, geografis, transport, dan vendor menjadi data yang punya pemilik (`lang/*.yaml`, `library/<kind>/<lang>.json`, `plugin.yaml`, `config.yaml`), setiap handler mendeklarasikan metadatanya, dan sebuah gate membuktikannya.

**D. Pindahkan i18n dan keselamatan ke plugin.** Ditolak: `i18n.py` adalah satu dari lima belas modul level atas engine (ADR-0031), dan soul engine harus bisa berbicara serta mendeteksi krisis sebelum plugin apa pun terpasang. Keselamatan adalah perilaku spine (ADR-0000, ADR-0007).

## 4. Keputusan

### 4.1 Kontrak core netral

Engine harus boot, lulus suite-nya, dan melayani `/status` dalam konfigurasi ini: `plugins: {}`, tanpa gateway **opsional** terdaftar, tanpa pack library terpasang, `language: en`, dan database kosong. Body yang dikirim engine untuk konsol operatornya sendiri (`eidesoul/gateways/cli`) bukan gateway opsional; ia adalah permukaan bawaan dan dihitung sebagai bagian dari neutral boot. Data apa pun yang dibutuhkannya dan tidak ditemukan adalah penolakan eksplisit yang menyebut hal yang hilang. Inilah definisi operasional "netral"; yang lebih lemah hanyalah slogan.

### 4.2 Budaya adalah data

Pola krisis dan sensitif, severity kata kasar, kata afirmasi, level word, tawa, partikel, dan seed style hidup di pack per bahasa di samping pack yang sudah ada:

- `eidesoul/library/safety/<lang>.json` — daftar frasa `crisis`, `sensitive`.
- `eidesoul/library/severity/<lang>.json` — kata → `{kind, level}`.
- `eidesoul/library/lexicon/<lang>.json` — memperoleh `affirmations`, `level_words`, `laughter`, `particles`.
- `eidesoul/library/style/<lang>.json` — baseline style (`seed_median`, `seed_p90`, `seed_lower`, `seed_emoji`, `particles`, `emojis`).

`eidesoul/engine/**` tidak memuat literal non-Inggris dan tidak memuat daftar kata khusus bahasa. Satu-satunya pengecualian adalah jaring keselamatan Inggris, yang dicatat eksplisit oleh gate. Bahasa tanpa style pack tidak mendapat baseline style, bukan baseline Indonesia.

### 4.3 Bahasa adalah nilai wajib

Tidak ada default `language: str = "id"`, tidak ada fallback `or "id"`, dan tidak ada argumen `normalize_language(..., "id")` yang tersisa di `eidesoul/engine/**`; `soul.language` ditulis oleh `eidesoul init`, setiap pemanggil meneruskannya eksplisit, dan lookup katalog hanya jatuh ke `en`. `"auto"` diselesaikan ke locale operator, tidak pernah ke negara tetap. `soul.language_line` berhenti bercabang di kode: instruksinya adalah key katalog, sehingga soul Prancis diberi tahu untuk membalas dalam bahasa Prancis atau ditolak, tidak pernah dalam bahasa Indonesia.

### 4.4 Geografi adalah nilai wajib

Manifest automation wajib menyatakan `event.tz`; `tz` yang hilang adalah `ManifestError` saat validasi, dan penjadwal membaca nilai tersimpan alih-alih men-default ulang. Image tidak membawa `TZ`. `currents` dihapus sepenuhnya — `jobs/currents.py`, `stores/currents.py`, `/konteks` beserta deskripsi benderanya, blok prompt-nya, state key-nya — **dan** daftar topiknya harus keluar dari `turn/prompts.py:178-191`, yang sekarang mengulangnya sebagai prosa. Fakta segar datang dari tool `web_search`; pengetahuan tahan lama datang dari facts pack yang ditumbuhkan `scripts/grow.py`; kapabilitas berita langsung adalah plugin. Pencarian diminta dengan region eksplisit atau tanpa region; default vendor seperti `us-en` bukan default.

### 4.5 Transport hidup di seam

`dm_thread`, protokol `Chat`, `digest`, ringkasan grup, semantik stiker dan reaksi, render kutipan, placeholder media, id pesan, status pengiriman, dan "model hidup di chat" keluar dari kosakata engine sendiri. Ketika engine membutuhkan "thread privat dengan orang ini", body menjawabnya lewat seam inbound (ADR-0018, ADR-0032).

Fakta transport mencapai prompt dari body, tidak pernah dari cabang core. `CoreDefaults` memperoleh `prompt_section() -> str`, yang mengembalikan teks milik body itu sendiri: bagaimana ia menyapa orang, apa arti mention di sini, kutipan, penanda view-once atau undecryptable, stiker, dan target mana yang bisa dijangkau tool-nya. Engine menyusun `neutral base + body section`, sehingga CLI dan WhatsApp mendeskripsikan dirinya sendiri, dan body tanpa ruang — robot dengan satu thread per sensor, body email — mengembalikan kosong dan mendapat prompt netral. Section itu statis per boot, berbahasa Inggris, dibatasi 1200 karakter, tidak boleh menginterpolasi data pengguna, secret, atau nilai runtime, dan ikut masuk system prompt, sehingga `prompt_prefix_hash` (ADR-0025) mencakupnya dan cache prefix tetap benar.

### 4.6 Nama vendor absen dari core, kecuali spine

Identitas plugin, gateway, atau vendor hanya boleh muncul di dalam pohonnya sendiri dan manifest-nya, kecuali spine yang disebut §4.12. Khususnya:

- `admin/templates.py` menulis `plugins: {}` dan memberi role template hanya tool core.
- `.env` dihasilkan dari nama core, key provider yang dipilih, dan gabungan `secrets` yang dideklarasikan manifest plugin yang benar-benar terpasang.
- Tabel plugin hidup bersama plugin; `schema.py` tidak mendefinisikan `google_identity` atau `google_device`, dan tidak ada modul core yang membacanya.
- `memory/episodes.py` menurunkan target dari deklarasi tool itu sendiri, bukan dari tabel nama.

### 4.7 Discovery, bukan enumerasi

Metadata hidup bersama hal yang dideskripsikannya: tool mendeklarasikan `target` (`file`, `room`, `document`, `process`, atau `""`) dan mempertahankan `family`; plugin mendeklarasikan `provides` dan `secrets`; pack mendeklarasikan `language`; manifest mendeklarasikan `tz`; handler mendeklarasikan kelas approval-nya (§4.11); body mendeklarasikan `prompt_section()`-nya (§4.5), kosong secara default. Tidak ada tabel nama → jenis terpusat di core.

### 4.8 Hapus spesialisasi; jangan deprecate

Karena belum ada rilis, spesialisasi dihapus alih-alih di-shim: `currents` (pasangan modul, command, prosa prompt, state key), `LEGACY`/`LEGACY_GATEWAY`, shutdown compatibility hook, dan DSL verba `memori`.

Gender terbagi tiga. `gender` milik soul sendiri (`soul.py:62`, dibaca prompt keputusan) tetap: itu data identitas yang ditulis `soul.yaml` dan `eidesoul init`. Semua yang menebak atau menggerbangi gender **seseorang** pergi: `access/target.py` sepenuhnya, `identity.display_target` dan pemanggilnya, command `/gender` beserta key katalognya, key `gender_gate`/`gender_confidence`/`gender_avatar`, kolom `people.gender*` beserta `affect.gender`/`set_gender`, dan sinyal `avatar` gateway — `gateways/whatsapp/profile.py` ada hanya untuk tebakan itu.

`gender_gate` adalah pembuka, bukan jaring: `effective_limits` memberi target yang diyakininya male `toxicity`/`adult_memes` sesuai konfigurasi sementara yang lain dikunci ke `(1, 0)`, dan `character/patterns.py:28,167-171` menggerbangi pemilihan pola pada sumbu `TARGETS` yang sama. Karena itu penghapusannya mengirim penggantinya di commit yang sama, tidak pernah sesudahnya: register datang dari konfigurasi room, prasyarat "male target" pada level `adult_memes` tertinggi dihapus, `TARGETS`/`_eligible` kehilangan sumbunya, dan aturan prompt menjadi target-independent ("no sexual or degrading material about a real person; an unclear target or room stays gentle"). Hard floor di `character.py` (minor, doxxing, ancaman kredibel, kebencian) tidak berubah dan tetap menjadi garis keselamatan yang sebenarnya.

### 4.9 Nama command berbahasa Inggris, tanpa alias lokal

`cari`, `kirim`, dan `memori` menjadi `search`, `send`, dan `memory`; `konteks` hilang bersama `currents`; enam alias command Indonesia, dua alias tool, dan delapan cabang verba lokal di `commands/memori.py` dihapus, dan key katalog ikut diganti namanya, termasuk verba yang bocor di `lang/en.yaml:121`. Gateway atau plugin boleh mengekspos command ber-namespace miliknya sendiri; core tidak menyimpan lapisan lokalisasi untuk permukaannya sendiri.

### 4.10 Gate bersifat mekanis

Bagian 6 mendaftar pemeriksaannya. Semuanya berjalan di suite, bukan di review.

### 4.11 Handler mendeklarasikan metadatanya sendiri

Registry membawa fakta, bukan nama:

```text
TOOL = Handler(
    name="read",
    family="read",          # dideklarasikan, divalidasi bentuknya, tidak pernah dicocokkan ke himpunan tertutup
    target="file",          # file | room | document | process | ""
    approval_class="none",  # none | consent | authorization
    destructive=(),         # aksi yang merusak state, mis. ("forget",)
    path_arg="path",        # argumen yang menamai target filesystem
    scan=(),                # argumen yang harus dibaca pemindai kredensial dan injeksi
)
```

`FAMILIES`, `AUTHORIZATION_TOOLS`, `DESTRUCTIVE_ACTIONS`, dan cabang per-nama di `guard/policy.py` serta `tools/__init__.py` dihapus. Tool milik plugin lolos validasi dengan mendeklarasikan field yang sama; core tidak pernah mengetahui namanya.

### 4.12 Spine adalah satu-satunya allowlist vendor

Spine yang disebut ADR-0001 dan ADR-0031 bersifat tetap dan boleh dinamai di core: PostgreSQL dengan pgvector, Mem0, embedder MiniLM bersama, dan klien wire yang kompatibel OpenAI. Selain itu opsional dan keluar dari `eidesoul/engine/**`: mesin pencari beserta region-nya, vendor model bahasa, API cloud, dan tabel plugin. Allowlist adalah konstanta dengan satu entri per komponen spine dan komentar yang menyebut ADR-nya, sehingga menambahkannya adalah perubahan yang ditinjau, bukan import tak sengaja.

### 4.13 Satu jam, satu locale

`locale` dan `timezone` adalah nilai soul yang wajib dan ditulis `eidesoul init`, bukan default kode. Core membaca jam lewat satu helper, `now(tz)`, dan menyimpan bucket hari pada zona itu; `date.today()`, `time.localtime()`, dan pasangan `gmtime`/`localtime` yang bercampur dilarang di `eidesoul/engine/**`. `TZ` dan `LANG` host tidak diteruskan ke anak sandbox atau shell. Parsing hari kerja menerima angka ISO atau nama tanpa peka huruf, dan awal minggu dinyatakan, bukan diasumsikan.

## 5. Konfigurasi

```yaml
soul:
  language: en            # ditulis oleh `eidesoul init`; wajib, tanpa default
  locale: en-US           # wajib; menggerakkan format dan region pencarian
  timezone: Europe/Berlin # wajib; zona rumah host untuk prompt dan bucket hari
engine:
  safety:
    allow_degraded: false # true menjalankan jaring Inggris saat pack bahasa tidak ada
  web_search:
    region: ""            # kosong berarti tanpa bias region; tidak pernah default vendor
automation:
  event:
    tz: Europe/Berlin     # wajib per manifest; tanpa default engine
plugins: {}               # tanpa key plugin di template yang dikirim
```

Tata letak pack:

```text
eidesoul/library/safety/<lang>.json     {"version":1,"language":"en","crisis":[…],"sensitive":[…]}
eidesoul/library/severity/<lang>.json   {"version":1,"language":"en","words":{"word":["kind",2]}}
eidesoul/library/style/<lang>.json      {"version":1,"language":"en","seed_median":34,"particles":[…],"emojis":[…]}
eidesoul/library/lexicon/<lang>.json    memperoleh "affirmations", "level_words", "laughter", "particles"
```

## 6. Metrik dan gate

Hitungan yang harus menjadi nol, diukur 2026-11-04:

| Spesialisasi | Jumlah | Lokasi |
| --- | --- | --- |
| default bahasa | 25 + 8 fallback | 24 signature, default modul `soul`/`i18n`/`llm`, 8 situs `or "id"` |
| tabel kata hardcoded | 8 | `privacy` ×2, `character.SEVERITY`, `target`, `style` ×4, `patterns.SERIOUS_REGISTERS`, topik `self_knowledge` |
| default locale dan negara | 9 | `TZ` di `Dockerfile`, `locale` ×3, `research_block`, region ddgs, `Asia/Jakarta` ×3 |
| referensi vendor | 13 + 2 tabel | template ×3, episodes, `schema.google_*`, `people.whatsapp`, `WEB_BACKENDS`, ddgs |
| referensi transport | 72 baris | 52 di `turn/prompts.py`, 20 di `turn/decision.py`, plus `relay`, `dm_thread` ×3, `Chat`, `digest`, placeholder, render kutipan |
| tabel nama yang memblokir ekstensi | 6 | `FAMILIES`, `AUTHORIZATION_TOOLS`, `DESTRUCTIVE_ACTIONS`, cabang policy, `PROVIDES_KEYS`, jenis dokumen `settings` |
| situs jam | 6 | `date.today()` ×3, `localtime` ×3 melawan satu `gmtime` |
| nama permukaan non-Inggris | 20 | 4 command, 6 alias command, 2 alias tool, 8 cabang verba |
| penanda legacy | 6 | `LEGACY`, `LEGACY_GATEWAY`, `people.whatsapp`, compatibility hook ×2 |

Gate-nya, `scripts/neutral.py --check` (modul baru), gagal ketika:

1. `eidesoul/engine/**`, `eidesoul/admin/**`, atau template yang dikirim memuat **huruf non-ASCII** (kategori Unicode `L*`) di luar allowlist jaring keselamatan Inggris yang eksplisit. Tanda baca tipografis dan panah (`§ — … → • ≥ ↳`) bersifat struktural, bukan budaya; emoji dan simbol negara atau negara bagian ditolak di string yang terlihat pengguna.
2. Daftar kosakata terlarang (kata fungsi Indonesia plus kata yang dipindah ke pack) muncul di lingkup yang sama.
3. Identitas vendor, gateway, atau plugin muncul di sana di luar allowlist spine (§4.12), `eidesoul/providers/`, `eidesoul/gateways/`, dan `plugins/`.
4. Default atau fallback `language`/`locale`/`tz` muncul di sana: tidak ada `language="id"`, `default="id"`, `"id-ID"`, `"Asia/`, atau `TZ=` di `Dockerfile`.
5. Sebuah bahasa katalog tidak punya safety pack maupun catatan penolakan.
6. Sebuah `Handler` tidak punya `target` atau `approval_class`, atau tabel nama tertutup (`FAMILIES`, `AUTHORIZATION_TOOLS`, `DESTRUCTIVE_ACTIONS`, `PROVIDES_KEYS`) ditemukan, atau cabang nama menentukan kelas approval.
7. Template yang dikirim punya peta `plugins:` tidak kosong, menyebut tool plugin di seed role, atau menulis kredensial plugin ke `.env`.
8. `date.today()`, `time.localtime()`, atau `time.gmtime()` muncul di `eidesoul/engine/**` alih-alih helper `now(tz)`; atau `TZ`/`LANG` host diteruskan ke proses anak.
9. Smoke netral gagal: `pytest -q` dan `eidesoul selftest` berjalan dengan `plugins: {}`, `language: en`, tanpa gateway opsional, dan database kosong.
10. `prompt_section()` sebuah body lebih dari 1200 karakter, berubah antara dua pemanggilan dalam satu boot, atau body yang mengembalikan section kosong masih menghasilkan kosakata transport di prompt yang tersusun.
11. Suite kontrak prompt gagal: `tests/data/prompt_cases.yaml` menyatakan, per kasus, fragmen yang wajib ada, token yang dilarang, dan `prompt_prefix_hash` yang diharapkan, dan `pytest -q` menjalankannya terhadap builder prompt yang asli. Ia mengunci properti, bukan kata-kata, sehingga kalimat yang diubah tidak memaksa suntingan fixture tetapi token transport, budaya, atau bahasa yang bocor akan gagal.

Gate itu berjalan di samping gate yang sudah ada: `pytest -q`, `ruff check eidesoul scripts tests plugins migrations`, `python -P scripts/types.py --check`, `python scripts/selfcheck.py --check`, `python scripts/adr.py --check`.

## 7. Acceptance criteria

- Gate di §6 lulus tanpa satu pun temuan, dalam lingkup yang disebutnya (`engine`, `admin`, `__main__.py`, template, `Dockerfile`).
- `language`, `locale`, dan `timezone` tidak punya default di mana pun di `eidesoul/engine/**`; `eidesoul init` menulis ketiganya, dan `soul.yaml` seed tidak membawa satu pun sebelum itu.
- Soul Prancis diberi tahu membalas dalam bahasa Prancis lewat katalog, atau ditolak; `language_line` tidak membawa cabang bahasa di kode.
- Manifest tanpa `tz` gagal dengan `ManifestError`; tidak ada literal `Asia/` dan tidak ada `TZ=` di luar manifest deployment.
- `currents` hilang: tidak ada modul, command, state key, maupun daftar topik prosa di `turn/prompts.py`.
- Body dengan `prompt_section()` kosong (robot dispatch-only) menghasilkan prompt tanpa kosakata transport; section CLI dan WhatsApp adalah teks milik masing-masing, di bawah 1200 karakter, identik pada dua pemanggilan dalam satu boot.
- Hash prefix dicatat per body, sehingga perubahan pada section sebuah body adalah diff yang disengaja, bukan kejutan cache.
- Suite kontrak prompt membawa satu kasus per body (`cli`, thread grup, robot dispatch-only) dan per perilaku (krisis, kelas approval, register, penolakan bahasa) dan berjalan di dalam `pytest -q`.
- `stores/style.py` tidak membawa konstanta partikel, tawa, emoji, atau seed; bahasa tanpa style pack tidak mendapat blok style.
- Satu batas hari: helper `now(tz)` yang sama memberi makan prompt, anggaran automation, cap ambient, dan bucket memori.
- `days: "Mon"` dan `days: ["mon"]` sama-sama diterima; awal minggu didokumentasikan.
- Plugin uji yang mendeklarasikan family dan `target` baru berhasil boot dan tool-nya terklasifikasi tanpa menyunting core; tidak ada himpunan `FAMILIES` atau `AUTHORIZATION_TOOLS`.
- `schema.py` tidak mendefinisikan tabel plugin; plugin membuatnya sendiri.
- `plugins: {}` dan `.env` tanpa secret plugin; memasang plugin menambahkan `secrets` yang dideklarasikannya tanpa menyunting `templates.py`.
- Menghapus semua plugin dan gateway opsional tetap membuat engine boot; `eidesoul selftest` melaporkan `safety: ok` untuk bahasa yang punya pack dan `safety: degraded` hanya ketika `allow_degraded: true`.
- Setiap nama command, tool, dan verba berbahasa Inggris; tidak ada alias atau cabang verba lokal yang diselesaikan lewat core.

## 8. Risiko dan mitigasi

- **Regresi keselamatan saat jaring krisis pindah ke pack.** Mitigasi: jaring Inggris tetap dikompilasi sebagai garis terakhir, bahasa tanpa pack ditolak kecuali `allow_degraded: true`, dan `selftest` menguji satu frasa krisis per pack yang dikirim.
- **Perutean approval rusak saat tabel nama pergi.** Mitigasi: `approval_class` menjadi field yang dideklarasikan dengan default `authorization` untuk handler tak dikenal, sehingga deklarasi yang terlupa gagal tertutup, bukan terbuka; suite memastikan setiap handler yang dikirim mendeklarasikannya.
- **Biaya penulisan pack.** Mitigasi: `scripts/grow.py` sudah menumbuhkan pack dari korpus, `--check` memvalidasi bentuk pack, dan rilis pertama hanya mengirim `en` serta `id`.
- **Churn penggantian nama.** Mitigasi: satu commit per fase; test, katalog, role template, dan docstring berpindah di commit yang sama; belum ada rilis, jadi tidak ada shim.
- **Gate menjadi musuh prosa.** Mitigasi: gate memeriksa **huruf** non-ASCII, bukan tanda baca, sehingga `§`, `—`, `→`, dan `•` di docstring tetap sah sementara emoji dan bendera di string yang terlihat pengguna tidak.
- **Drift allowlist spine.** Mitigasi: allowlist adalah satu konstanta dengan kutipan ADR per entri; gate menyebut berkas dan baris penambahannya.
- **Migrasi jam.** Mitigasi: satu helper, satu test yang menetapkan `TZ` host ke dua zona berbeda dan mengharapkan bucket hari yang identik.
- **Section prompt sebuah body menjadi permukaan injeksi.** Mitigasi: itu kode yang dikirim operator pada tingkat kepercayaan plugin, statis per boot, dibatasi 1200 karakter, dan dilarang menginterpolasi data pengguna, secret, atau nilai runtime; suite memastikan batas dan sifat statisnya, dan section itu tidak pernah membawa isi pesan.
- **Section dinamis merusak cache prompt.** Mitigasi: `prompt_section()` tidak menerima argumen, test memanggilnya dua kali dalam satu boot dan mengharapkan teks identik, dan fakta per-turn tetap di bagian volatil prompt.
- **Kehilangan konteks langsung bersama `currents`.** Mitigasi: `web_search` menutup pencarian ad hoc, facts pack menutup pengetahuan tahan lama, dan plugin dapat mengembalikan feed berita tanpa perubahan core.

## 9. Interaksi dengan ADR lain

- **ADR-0000** sudah menyatakan data bahasa sebagai katalog dan seam sebagai embodiment-neutral; ADR ini membuat kedua klaim itu dapat dieksekusi.
- **ADR-0001** memaku spine yang disebut §4.12, termasuk Mem0 dan embedder bersama.
- **ADR-0006** mempertahankan plafon privasi; hanya kosakata afirmatif yang pindah ke pack.
- **ADR-0007** memiliki kelas approval; §4.11 mengubah cara kelas ditemukan, bukan arti kelasnya.
- **ADR-0009** memiliki register dan gate ADR; gate baru ini mengikuti polanya.
- **ADR-0014** memperoleh `event.tz` wajib; state machine automation tidak berubah.
- **ADR-0018** mempertahankan kontrak, sementara `Chat`, `dm_thread`, dan kosakata chat di prompt pindah keluar dari core ke body.
- **ADR-0019** melaporkan mode keselamatan di `/status` dan di `selftest`.
- **ADR-0022** mempertahankan role yang dilindungi; hanya jalur nama legacy yang pergi.
- **ADR-0025** tidak terpengaruh: bahasa yang wajib sudah menjadi bagian dari prefix stabil.
- **ADR-0027** ditegakkan di sini: clean by default berarti template tanpa blok vendor.
- **ADR-0028** memperoleh aturan bahwa `.env` template diturunkan dari `secrets` sebuah manifest, dan bahwa plugin memiliki skemanya sendiri.
- **ADR-0031** menyediakan gate yang diperluas ADR ini dan anggaran modul yang harus dipatuhinya.
- **ADR-0032** menyediakan pintu; ADR ini menghapus kosakata transport yang menyusup di sekitarnya.

## 10. Referensi kode

- `eidesoul/engine/guard/patterns.py:15-19` — pola krisis dan sensitif yang di-hardcode.
- `eidesoul/engine/character/character.py:31,188-211,227-235` — `pantun`, `SEVERITY`, dan `effective_limits`.
- `eidesoul/engine/access/target.py:21-26,39-40,50,111-155` — daftar kata kasar kedua, penandanya, sumbu `TARGETS`, dan inferensinya.
- `eidesoul/engine/access/privacy.py:27,32-46,93` — `YES`, `LEVEL_WORDS`, dan fallback `or "id"`.
- `eidesoul/engine/character/patterns.py:27,28,167-171` — `SERIOUS_REGISTERS`, `TARGETS`, `_eligible`.
- `eidesoul/engine/stores/style.py:22,27,29-33,34-60,343-357` — placeholder, tawa, partikel, emoji, seed, dan baseline prompt.
- `eidesoul/engine/config.py:215-218,250-259,388` — konfigurasi pencarian, `TZ`/`LANG` yang diteruskan, id topik Indonesia.
- `eidesoul/engine/self_knowledge.py:70-77,313-325,462-465` — key topik, heading prompt, fallback README.
- `eidesoul/engine/commands/memori.py:58,75,90,126` — DSL verba lokal; `eidesoul/lang/en.yaml:121` — verba yang sama di katalog Inggris.
- `eidesoul/engine/soul.py:22-44,60,65,82,106-111,251,256` — dokumen seed, `language`, `locale`, `language_line`, dan kedua fallback.
- `eidesoul/engine/i18n.py:50,59` — default `normalize_language` dan alias `auto`.
- `eidesoul/engine/jobs/schedule.py:16,29-36,76-84,136` — `DEFAULT_TZ`, `zone`, parsing hari kerja.
- `eidesoul/engine/jobs/automation.py:155,1133,1539` — dua default ulang `tz` dan key hari.
- `eidesoul/engine/jobs/currents.py:1,18-31`, `eidesoul/engine/stores/currents.py:1`, `eidesoul/engine/commands/konteks.py:1,11,72` — konteks negara dan benderanya.
- `eidesoul/engine/commands/cari.py:46-48`, `kirim.py:28-30`, `memori.py:159-161` — nama, alias, dan deskripsi emoji Indonesia.
- `eidesoul/engine/tools/web_search.py:16,64,107` — `WEB_BACKENDS`, default region ddgs, alias tool.
- `eidesoul/engine/tools/sandbox.py:17,69-71` — `TZ`/`LANG` yang diteruskan; `eidesoul/engine/tools/settings.py:33-37` — jenis dokumen tertutup.
- `eidesoul/engine/guard/policy.py:18,108-111,289-356` — tabel nama approval dan cabangnya.
- `eidesoul/engine/tools/__init__.py:23,349-367` — `FAMILIES` dan logika pintu per-nama.
- `eidesoul/engine/plugins.py:105-110,508-511` — `PROVIDES_KEYS` dan tool plugin yang divalidasi terhadap family core.
- `eidesoul/engine/schema.py:927-946`, `eidesoul/engine/access/identity.py:22-23,345`, `eidesoul/admin/people.py:14,22,57-59,90` — tabel plugin, pembaca core-nya, dan fallback WhatsApp/legacy.
- `eidesoul/admin/templates.py:70,335-345,354-356,380,389-397` — default plugin, grant role, nama command Indonesia, ekor `.env`.
- `eidesoul/engine/memory/episodes.py:149-161` — `TARGET_TYPES` yang menyebut plugin dan tool gateway.
- `eidesoul/engine/turn/prompts.py:61-84,121,138-159,178-191,239,247,309,319,329-332,347-350,360-372,379-384,408-412,427-432,441-449,498` — kosakata chat dan daftar topik prosa.
- `eidesoul/engine/turn/decision.py:13-51,343-345` — mention, DM, `ACTS`, `bubbles`, label prompt.
- `eidesoul/engine/inbound.py:168-176` — `CoreDefaults`, tempat `prompt_section()` mendarat dengan default kosong.
- `eidesoul/engine/llm.py:138-145` — `prompt_prefix_hash`, yang akan mencakup section body secara otomatis.
- `eidesoul/gateways/cli/gateway.py:91-106`, `eidesoul/gateways/whatsapp/gateway.py` — body yang akan membawa section-nya sendiri alih-alih mengajari core kosakatanya.
- `eidesoul/engine/turn/context.py:317,506-513` — default bahasa dan render kutipan.
- `eidesoul/engine/access/relay.py:1-10,218`, `eidesoul/engine/stores/history.py:683,746` — relay terikat DM, `dm_thread`, compatibility hook.
- `eidesoul/engine/llm.py:1,10,33,51,177,648-655` — klien wire, prompt dan metode digest, default bahasa.
- `eidesoul/engine/memory/mem0.py:19,186-204,213,223`, `eidesoul/engine/embed.py:15` — komponen spine yang di-allowlist §4.12.
- `eidesoul/engine/memory/context.py:82`, `eidesoul/engine/memory/ledger.py:25,104`, `eidesoul/engine/stores/affect.py:835,917`, `eidesoul/engine/memory/store.py:2216` — batas hari yang bercampur.
- `eidesoul/engine/access/roles.py:20`, `eidesoul/engine/memory/ledger.py:104` — penanda legacy.
- `Dockerfile:5` — `TZ=Asia/Jakarta`.
- `eidesoul/library/patterns/jawatimur.json`, `eidesoul/library/facts/indonesia.json` — satu-satunya pack yang dikirim, keduanya `language: id`.
- `tests/support.py:66-120` — `default_soul`, `role`, dan `build_config`, helper yang dipakai ulang suite kontrak prompt alih-alih membangun dunia sendiri.
- Usulan, belum ada kode: `eidesoul/library/{safety,severity,style}/<lang>.json`, key lexicon `affirmations`/`level_words`/`laughter`/`particles`, `Handler.target`/`approval_class`/`destructive`/`path_arg`/`scan`, `now(tz)`, `soul.timezone`, `scripts/neutral.py`, dan `tests/data/prompt_cases.yaml`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0033-neutral-core.md`.*
