> Terjemahan turunan — jangan sunting; sumber: 0033-neutral-core.md (sha256: 992134fda9fe)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0033: Core Netral, Tepi Terspesialisasi — Tanpa Budaya, Transport, atau Vendor di Dalam Mekanisme

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0000 (bentuk permukaan dan data bahasa), ADR-0009 (rumah ADR dan gate-nya), ADR-0018 (kontrak gateway), ADR-0027 (clean by default), ADR-0028 (unit plugin), ADR-0031 (tata letak, anggaran pertumbuhan, gate), ADR-0032 (satu pintu)
- **Supersedes:** —
- **Terkait:** ADR-0006 (plafon privasi), ADR-0007 (kelas approval), ADR-0014 (automation), ADR-0019 (`/status`), ADR-0022 (role), ADR-0025 (prefix prompt stabil)
- **Implementasi:** draft — kontrak dan gate-nya diputuskan di sini; setiap pelanggaran di §1 masih ada di pohon.
- **Catatan lokasi:** lintas-potong (mengatur setiap permukaan engine), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0033-neutral-core.md`.

---

## 1. Konteks

Engine sudah punya *bentuk* yang benar untuk perilaku berbasis data: string chat hidup di `eidesoul/lang/<code>.yaml` (dua katalog, 249 key masing-masing), data karakter hidup di `eidesoul/library/`, dan sebuah ekstensi mendeklarasikan dirinya di manifest. Yang **belum** dimiliki adalah aturan yang menjaga mekanisme dan data tetap terpisah. Budaya, geografi, transport, dan vendor ditulis di dalam mekanismenya sendiri, sehingga deployment di luar satu negara atau satu transport harus menyunting kode. Diukur pada 2026-11-04:

**Bahasa dan budaya terpatri di mekanisme**

- Tiga belas default memaksa Bahasa Indonesia: `language: str = "id"` pada sembilan signature engine (`character/character.py:53,61,68,116`, `guard/patterns.py:33,45,50`, `jobs/ambient.py:28`, `jobs/cognition.py:373`), plus `soul.py:60`, `i18n.py:50`, `i18n.ALIASES["auto"]`, dan `llm.py:51`. Kedua katalog membawa 249 key yang sama, jadi ini kebijakan, bukan batas kemampuan.
- Empat tabel kata ada di kode: `access/privacy.py:27` (`YES`), `access/privacy.py:32-46` (`LEVEL_WORDS`), `character/character.py:188-211` (`SEVERITY`, tujuh belas kata Indonesia), dan `access/target.py:22-26` (daftar kata kasar kedua).
- Jaring keselamatan bersifat kultural: `guard/patterns.py:15-19` meng-hardcode frasa krisis (`bunuh diri`, `mau mati`, `gantung diri`, `overdosis`, …) dan regex token sensitif memuat `kredensial`, padahal docstring modulnya sendiri mengklaim pola itu "language-independent". Deployment Prancis atau Jepang tidak punya deteksi krisis sama sekali.
- Library karakter yang dikirim hanya satu negara: `eidesoul/library/patterns/jawatimur.json` (72 pola) dan `eidesoul/library/facts/indonesia.json` (306 fakta) adalah satu-satunya pack, keduanya `language: id`, jadi `packs_for("en")` kosong dan soul non-Indonesia berakhir tanpa pola dan tanpa fakta. `lexicon/`, `affect/`, dan `intent/` sudah dua bahasa.

**Geografi di mekanisme**

- `jobs/schedule.py:16` menetapkan `DEFAULT_TZ = "Asia/Jakarta"`; `zone()` dan `_next_occurrence` memakainya setiap kali manifest tidak menulis `tz`, sehingga setiap jadwal tanpa zona eksplisit berjalan di waktu Jakarta.
- `jobs/currents.py:18-31` membawa topik berita satu negara (`pemerintah`, `ekonomi`, `rupiah`, `prabowo`, `sawit`, `surabaya`, `jawa timur`) dan sufiks query `" Indonesia {YEAR} terbaru"`; `stores/currents.py` menyimpannya di state key `currents`, dan `commands/konteks.py:1` mendokumentasikan dirinya sebagai "the Indonesia (+62) context". `currents` juga menduplikasi apa yang sudah disediakan tool `web_search` dan facts pack, dan mengikat prompt ke siklus berita satu negara.

**Vendor dan plugin di mekanisme**

- `admin/templates.py:70` menulis `plugins: {"google": …}` ke setiap config host baru, `admin/templates.py:335-345` memberi tool plugin `google` kepada role `member` dan `guest`, dan `admin/templates.py:389-397` menulis tiga nama environment Google ke `.env` padahal manifest plugin hanya mendeklarasikan satu (`plugins/google/plugin.yaml`, `secrets: [GOOGLE_SERVICE_ACCOUNT_PATH]`). Template itu salinan tangan yang basi dari sebuah manifest, dan seratus plugin berarti seratus suntingan di sana.
- `memory/episodes.py:149-161` memetakan nama tool ke target type dalam sebuah konstanta, dan dua nama di antaranya milik plugin (`google`) serta gateway (`send_to`, `chat_history`).

**Transport di mekanisme**

- `access/relay.py` mengikat approval ke "the content owner's DM"; `stores/history.py:683` mengekspos `dm_thread`, dipanggil dari `access/privacy.py:202`, `jobs/automation.py:1673`, dan `jobs/cognition.py:1008`.
- `llm.py:33` membawa `DIGEST_PROMPT` ("You summarize a chat group") dan `llm.py:648` sebuah metode `digest(chat_name, …)`; `inbound.py` mendefinisikan protokol `Chat`. Robot embodiment harus berpura-pura menjadi chat agar bisa memakai engine.

**Nama dan legacy**

- Empat command bernama Indonesia (`commands/cari.py`, `kirim.py`, `konteks.py`, `memori.py`), enam alias command dan dua alias tool melakukan hal yang sama (`tenang`, `bantuan`, `bahasa`, `tautkan`, `ingat`, `lepas`; `tools/web_search.py:107`, `tools/memory.py:201`), dan 76 key katalog dinamai menurutnya.
- `access/roles.py:20` menyimpan `LEGACY = "owner"` dengan jalur migrasi di `admin/roles.py`, `admin/people.py:22` menyimpan `LEGACY_GATEWAY = "legacy"`, dan `memory/ledger.py:104` serta `stores/history.py:746` menyimpan "shutdown compatibility hooks".

Dua konsekuensi mengikuti. Pertama, **keselamatan tidak portabel**: jaring krisis hanya mencakup satu rumpun bahasa. Kedua, **core tidak dapat diperluas**: setiap negara, transport, atau plugin baru menyunting mekanisme, yang justru merupakan churn yang hendak dicegah oleh anggaran pertumbuhan ADR-0031.

## 2. Decision drivers

1. **Netralitas harus dapat dieksekusi, bukan diingat.** Checklist review sudah gagal: setiap item di atas bertahan melewati beberapa putaran review karena "berjalan".
2. **Keselamatan tidak boleh bergantung pada satu negara.** Deteksi krisis adalah perilaku spine dan harus mencakup setiap bahasa yang diklaim engine.
3. **Instalasi adalah kesan pertama.** `eidesoul init` harus menghasilkan host yang tidak menyebut vendor, negara, maupun transport apa pun.
4. **Ekstensi tidak boleh menyunting core.** Plugin, gateway, atau bahasa baru menambah data dan manifest; ia tidak pernah menyentuh `eidesoul/engine/**` atau `eidesoul/admin/templates.py`.
5. **Penolakan, bukan fallback diam.** Pack, timezone, atau plugin yang hilang adalah error eksplisit yang menyebut apa yang hilang — tidak pernah berupa default Indonesia, Inggris, atau Jakarta yang dipilih atas nama operator.
6. **Satu gate, mekanis.** Aturannya hidup di skrip yang dijalankan suite, dengan inventaris yang terdaftar, bukan di prosa.

## 3. Opsi yang dipertimbangkan

**A. Status quo plus disiplin review.** Ditolak: §1 adalah bukti bahwa review saja tidak menjaga garis.

**B. Override konfigurasi.** Tambahkan setelan `soul.language` dan `engine.timezone` lalu dokumentasikan. Ditolak sebagai tidak memadai: override menyembunyikan default alih-alih menghapusnya, dan tidak melakukan apa pun untuk tabel kata, nama vendor, atau jaring krisis.

**C. Pack, manifest, metadata tool, dan satu gate mekanis (dipilih).** Setiap fakta kultural, geografis, transport, dan vendor menjadi data yang punya pemilik (`lang/*.yaml`, `library/<kind>/<lang>.json`, `plugin.yaml`, `config.yaml`), core tidak menyebut apa pun dengan nama, dan sebuah gate membuktikannya.

**D. Pindahkan i18n dan keselamatan ke plugin.** Ditolak: `i18n.py` adalah satu dari lima belas modul level atas engine (ADR-0031), dan soul engine harus bisa berbicara serta mendeteksi krisis sebelum plugin apa pun terpasang. Keselamatan adalah perilaku spine (ADR-0000, ADR-0007).

## 4. Keputusan

### 4.1 Kontrak core netral

Engine harus boot, lulus suite-nya, dan melayani `/status` dalam konfigurasi ini: `plugins: {}`, tanpa gateway terdaftar, tanpa pack library terpasang, `language: en`, dan database kosong. Data apa pun yang dibutuhkannya dan tidak ditemukan adalah penolakan eksplisit yang menyebut hal yang hilang. Inilah definisi operasional "netral"; yang lebih lemah hanyalah slogan.

### 4.2 Budaya adalah data

Pola krisis dan sensitif, severity kata kasar, kata afirmasi, dan level word hidup di pack per bahasa di samping pack yang sudah ada:

- `eidesoul/library/safety/<lang>.json` — daftar frasa `crisis`, `sensitive`.
- `eidesoul/library/severity/<lang>.json` — kata → `{kind, level}`.
- `eidesoul/library/lexicon/<lang>.json` — memperoleh `affirmations` dan `level_words`, sehingga sisi negatif dan positif menjadi simetris.

`eidesoul/engine/**` tidak memuat literal non-Inggris dan tidak memuat daftar kata khusus bahasa. Satu-satunya pengecualian adalah jaring keselamatan Inggris, yang dicatat eksplisit oleh gate.

### 4.3 Bahasa adalah nilai wajib

Tidak ada default `language: str = "id"` yang tersisa di `eidesoul/engine/**`. `soul.language` ditulis oleh `eidesoul init`, setiap pemanggil meneruskannya eksplisit, dan lookup katalog hanya jatuh ke `en`. `"auto"` diselesaikan ke locale operator, tidak pernah ke negara tetap.

### 4.4 Geografi adalah nilai wajib

Manifest automation wajib menyatakan `event.tz`; `tz` yang hilang adalah `ManifestError`. Tidak ada zona satu negara sebagai default, dan tidak ada modul yang menyebut topik berita satu negara. `currents` dihapus sepenuhnya — `jobs/currents.py`, `stores/currents.py`, command `/konteks`, blok prompt-nya, dan state key-nya. Fakta segar datang dari tool `web_search`; pengetahuan tahan lama datang dari facts pack yang ditumbuhkan `scripts/grow.py`; kapabilitas berita langsung adalah plugin.

### 4.5 Transport hidup di seam

`dm_thread`, protokol `Chat`, `digest`, dan ringkasan grup keluar dari kosakata engine sendiri: body menyelesaikan thread-nya sendiri dan meringkas ruangnya sendiri. Engine berbicara tentang orang, thread, dan aksi. Ketika engine membutuhkan "thread privat dengan orang ini", body menjawabnya lewat seam inbound (ADR-0018, ADR-0032).

### 4.6 Nama vendor absen dari core

Identitas plugin, gateway, atau vendor hanya boleh muncul di dalam pohonnya sendiri dan manifest-nya. Khususnya:

- `admin/templates.py` menulis `plugins: {}` dan memberi role template hanya tool core.
- `.env` dihasilkan dari nama core, key provider yang dipilih, dan gabungan `secrets` yang dideklarasikan manifest plugin yang benar-benar terpasang.
- `memory/episodes.py` menurunkan target dari deklarasi tool itu sendiri, bukan dari tabel nama.

### 4.7 Discovery, bukan enumerasi

Metadata hidup bersama hal yang dideskripsikannya: tool mendeklarasikan `target` (`file`, `room`, `document`, `process`, atau `""`) dan mempertahankan `family`; plugin mendeklarasikan `provides` dan `secrets`; pack mendeklarasikan `language`; manifest mendeklarasikan `tz`. Tidak ada tabel nama → jenis terpusat di core.

### 4.8 Hapus spesialisasi; jangan deprecate

Karena belum ada rilis, spesialisasi dihapus alih-alih di-shim: `currents` (pasangan modul, command, blok prompt, state key), `LEGACY`/`LEGACY_GATEWAY`, dan shutdown compatibility hook.

Gender terbagi tiga. `gender` milik soul sendiri (`soul.py:62`, dibaca prompt keputusan) tetap: itu data identitas yang ditulis `soul.yaml` dan `eidesoul init`. Semua yang menebak atau menggerbangi gender **seseorang** pergi: `access/target.py` sepenuhnya, `identity.display_target` dan pemanggilnya, command `/gender` beserta key katalognya, key `gender_gate`/`gender_confidence`/`gender_avatar`, kolom `people.gender*` beserta `affect.gender`/`set_gender`, dan sinyal `avatar` gateway — `gateways/whatsapp/profile.py` ada hanya untuk tebakan itu.

`gender_gate` adalah pembuka, bukan jaring: `effective_limits` memberi target yang diyakininya male `toxicity`/`adult_memes` sesuai konfigurasi sementara yang lain dikunci ke `(1, 0)`. Karena itu penghapusannya mengirim penggantinya di commit yang sama, tidak pernah sesudahnya: register datang dari konfigurasi room, prasyarat "male target" pada level `adult_memes` tertinggi dihapus, dan aturan prompt menjadi target-independent ("no sexual or degrading material about a real person; an unclear target or room stays gentle"). Hard floor di `character.py` (minor, doxxing, ancaman kredibel, kebencian) tidak berubah dan tetap menjadi garis keselamatan yang sebenarnya.

### 4.9 Nama command berbahasa Inggris, tanpa alias lokal

`cari`, `kirim`, dan `memori` menjadi `search`, `send`, dan `memory`; `konteks` hilang bersama `currents`; enam alias command Indonesia dan dua alias tool dihapus, dan key katalog ikut diganti namanya. Gateway atau plugin boleh mengekspos command ber-namespace miliknya sendiri; core tidak menyimpan lapisan lokalisasi untuk permukaannya sendiri.

### 4.10 Gate bersifat mekanis

Bagian 6 mendaftar pemeriksaannya. Semuanya berjalan di suite, bukan di review.

## 5. Konfigurasi

```yaml
soul:
  language: en            # ditulis oleh `eidesoul init`; wajib, tanpa default
engine:
  safety:
    allow_degraded: false # true menjalankan jaring Inggris saat pack bahasa tidak ada
automation:
  event:
    tz: Europe/Berlin     # wajib per manifest; tanpa default engine
plugins: {}               # tanpa key plugin di template yang dikirim
```

Tata letak pack:

```text
eidesoul/library/safety/<lang>.json     {"version":1,"language":"en","crisis":[…],"sensitive":[…]}
eidesoul/library/severity/<lang>.json   {"version":1,"language":"en","words":{"word":["kind",2]}}
eidesoul/library/lexicon/<lang>.json    memperoleh "affirmations" dan "level_words"
```

Metadata Handler (usulan):

```text
TOOL = Handler(name="read", family="read", target="file", …)
```

## 6. Metrik dan gate

Hitungan yang harus menjadi nol, diukur 2026-11-04:

| Spesialisasi | Jumlah | Lokasi |
| --- | --- | --- |
| default bahasa | 13 | 9 signature + 4 default modul/config |
| tabel kata hardcoded | 4 | `privacy` ×2, `character`, `target` |
| default negara | 2 | `Asia/Jakarta`, daftar topik `currents` |
| referensi vendor | 4 | `plugins` template, grant role, `ENV_TAIL`, `TARGET_TYPES` |
| referensi transport | 5 | `relay`, `dm_thread` ×3, `Chat`, `digest` |
| nama permukaan non-Inggris | 12 | 4 command, 6 alias command, 2 alias tool |
| penanda legacy | 4 | `LEGACY`, `LEGACY_GATEWAY`, compatibility hook ×2 |

Gate-nya, `scripts/neutral.py --check` (modul baru), gagal ketika:

1. `eidesoul/engine/**` memuat karakter non-ASCII di luar allowlist jaring keselamatan Inggris yang eksplisit.
2. `eidesoul/engine/**` memuat token dari daftar kosakata terlarang (kata fungsi Indonesia plus kata yang dipindah ke pack).
3. Identitas vendor, gateway, atau plugin muncul di bawah `eidesoul/engine/**` (hanya boleh di `eidesoul/providers/`, `eidesoul/gateways/`, `plugins/`).
4. `eidesoul/engine/**` memuat default `language="id"`, `default="id"`, atau literal `"Asia/`.
5. Sebuah bahasa katalog tidak punya safety pack maupun catatan penolakan.
6. Sebuah `Handler` tidak punya `target`, atau template yang dikirim menyebut plugin.
7. Smoke netral gagal: `pytest -q` dan `eidesoul selftest` berjalan dengan `plugins: {}`, `language: en`, dan tanpa gateway.

Gate itu berjalan di samping gate yang sudah ada: `pytest -q`, `ruff check eidesoul scripts tests plugins migrations`, `python -P scripts/types.py --check`, `python scripts/selfcheck.py --check`, `python scripts/adr.py --check`.

## 7. Acceptance criteria

- `grep -Pn '[^\x00-\x7F]' eidesoul/engine` kosong kecuali jaring keselamatan Inggris yang ada di allowlist.
- Pemindaian kosakata terlarang dan vendor di bawah `eidesoul/engine/**` melaporkan nol.
- `language` tidak punya default di mana pun di `eidesoul/engine/**`; setiap pemanggil meneruskannya.
- Manifest tanpa `tz` gagal dengan `ManifestError`; tidak ada literal `Asia/` di engine.
- `currents` hilang: tidak ada `jobs/currents.py`, tidak ada `stores/currents.py`, tidak ada `/konteks`, tidak ada pembaca state key `currents`, tidak ada blok prompt.
- `packs_for(lang)` tidak kosong untuk setiap bahasa yang mengirim katalog, atau soul ditolak dengan pesan yang menyebut celahnya.
- `eidesoul init` pada host bersih menulis `plugins: {}` dan `.env` tanpa secret plugin; memasang sebuah plugin menambahkan `secrets` yang dideklarasikannya ke `.env` yang dihasilkan tanpa menyunting `templates.py`.
- Menghapus semua plugin dan gateway tetap membuat engine boot dan `eidesoul selftest` melaporkan `safety: ok` untuk bahasa yang punya pack serta `safety: degraded` hanya ketika `allow_degraded: true`.
- Setiap nama command dan tool berbahasa Inggris; tidak ada alias lokal yang diselesaikan lewat core.

## 8. Risiko dan mitigasi

- **Regresi keselamatan saat jaring krisis pindah ke pack.** Mitigasi: jaring Inggris tetap dikompilasi sebagai garis terakhir, bahasa tanpa pack ditolak kecuali `allow_degraded: true`, dan `selftest` menguji satu frasa krisis per pack yang dikirim.
- **Biaya penulisan pack.** Mitigasi: `scripts/grow.py` sudah menumbuhkan pack dari korpus, `--check` memvalidasi bentuk pack, dan rilis pertama hanya mengirim `en` serta `id`.
- **Churn penggantian nama.** Mitigasi: satu commit per fase; test, katalog, role template, dan docstring berpindah di commit yang sama; belum ada rilis, jadi tidak ada shim.
- **Positif palsu di gate kata terlarang.** Mitigasi: gate mencetak token dan berkas yang cocok; pemindaian non-ASCII adalah yang utama, daftar kata dikurasi dan ditinjau per fase.
- **`Handler.target` menyentuh setiap tool.** Mitigasi: field-nya default `""`, yang berarti "tanpa target", sehingga tool yang tidak mendeklarasikannya berperilaku seperti sekarang dan hanya kehilangan pencocokan episode.
- **Register melonggar saat `gender_gate` pergi.** Mitigasi: konfigurasi room menggantikan pembukaan per-target di commit yang sama, aturan prompt khusus gender ditulis ulang target-independent, dan test memastikan room yang tidak jelas tetap menghasilkan limit lembut `(min(toxicity, 1), 0)`.
- **Kehilangan konteks langsung bersama `currents`.** Mitigasi: `web_search` menutup pencarian ad hoc, facts pack menutup pengetahuan tahan lama, dan plugin dapat mengembalikan feed berita tanpa perubahan core.

## 9. Interaksi dengan ADR lain

- **ADR-0000** sudah menyatakan data bahasa sebagai katalog dan seam sebagai embodiment-neutral; ADR ini membuat kedua klaim itu dapat dieksekusi.
- **ADR-0006** mempertahankan plafon privasi; hanya kosakata afirmatif yang pindah ke pack.
- **ADR-0007** tidak berubah: menghapus gender gate membuang sebuah heuristik, bukan kelas approval.
- **ADR-0009** memiliki register dan gate ADR; gate baru ini mengikuti polanya.
- **ADR-0014** memperoleh `event.tz` wajib; state machine automation tidak berubah.
- **ADR-0018** mempertahankan kontrak, sementara `Chat` dan `dm_thread` pindah keluar dari core ke body.
- **ADR-0019** melaporkan mode keselamatan di `/status` dan di `selftest`.
- **ADR-0022** mempertahankan role yang dilindungi; hanya jalur nama legacy yang pergi.
- **ADR-0025** tidak terpengaruh: bahasa yang wajib sudah menjadi bagian dari prefix stabil.
- **ADR-0027** ditegakkan di sini: clean by default berarti template tanpa blok vendor.
- **ADR-0028** memperoleh aturan bahwa `.env` template diturunkan dari `secrets` sebuah manifest.
- **ADR-0031** menyediakan gate yang diperluas ADR ini dan anggaran modul yang harus dipatuhinya.
- **ADR-0032** menyediakan pintu; ADR ini menghapus kosakata transport yang menyusup di sekitarnya.

## 10. Referensi kode

- `eidesoul/engine/guard/patterns.py:15-19` — pola krisis dan sensitif yang di-hardcode.
- `eidesoul/engine/character/character.py:53,61,68,116` — default bahasa yang menaungi kosakata terpatri itu.
- `eidesoul/engine/character/character.py:188-211` — `SEVERITY`, tujuh belas kata Indonesia.
- `eidesoul/engine/access/target.py:22-26,111-155` — daftar kata kasar kedua dan inferensi gender, keduanya dihapus.
- `eidesoul/engine/access/identity.py:461-500` — `display_target`, perakit sinyal yang memberi makan gate.
- `eidesoul/engine/commands/gender.py:1-31` — command `/gender` yang menulis label secara manual.
- `eidesoul/engine/stores/affect.py:933-975` — `gender`/`set_gender`, penyimpanan yang diisi rantai itu.
- `eidesoul/gateways/whatsapp/profile.py:1-3` — pengklasifikasi avatar yang ada hanya sebagai fallback gender.
- `eidesoul/engine/character/character.py:227-235` — `effective_limits`, pembukaan yang harus diganti penghapusannya, bukan dibuang.
- `eidesoul/engine/turn/prompts.py:128-135` — aturan register khusus gender yang ditulis ulang target-independent.
- `eidesoul/engine/access/privacy.py:27,32-46` — `YES` dan `LEVEL_WORDS`.
- `eidesoul/engine/soul.py:60` — `language: str = "id"`.
- `eidesoul/engine/i18n.py:50,59` — default `normalize_language` dan alias `auto`.
- `eidesoul/engine/jobs/schedule.py:16,30-36,136` — `DEFAULT_TZ` dan dua pemakaiannya.
- `eidesoul/engine/jobs/currents.py:1,18-31` — topik dan sufiks query negara.
- `eidesoul/engine/stores/currents.py:1` — persistensi state `currents`.
- `eidesoul/engine/commands/konteks.py:1,11,71-75` — command konteks negara dan refresh word-nya.
- `eidesoul/engine/commands/cari.py:48`, `eidesoul/engine/commands/kirim.py:30`, `eidesoul/engine/commands/memori.py:161` — nama dan alias Indonesia.
- `eidesoul/engine/tools/web_search.py:107`, `eidesoul/engine/tools/memory.py:201` — alias tool Indonesia.
- `eidesoul/admin/templates.py:70,335-345,389-397` — default plugin, grant role, dan ekor `.env`.
- `plugins/google/plugin.yaml` — manifest yang `secrets`-nya sudah dibantah template.
- `eidesoul/engine/memory/episodes.py:149-161` — `TARGET_TYPES` yang menyebut plugin dan tool gateway.
- `eidesoul/engine/access/relay.py:1-10` — approval relay yang terikat DM.
- `eidesoul/engine/stores/history.py:683` — `dm_thread`, dipanggil `access/privacy.py:202`, `jobs/automation.py:1673`, `jobs/cognition.py:1008`.
- `eidesoul/engine/llm.py:33,648-655` — prompt dan metode digest grup.
- `eidesoul/engine/access/roles.py:20` — `LEGACY = "owner"`; `eidesoul/admin/people.py:22` — `LEGACY_GATEWAY`.
- `eidesoul/engine/memory/ledger.py:104`, `eidesoul/engine/stores/history.py:746` — compatibility hook.
- `eidesoul/library/patterns/jawatimur.json`, `eidesoul/library/facts/indonesia.json` — satu-satunya pack yang dikirim, keduanya `language: id`.
- Usulan, belum ada kode: `eidesoul/library/safety/<lang>.json`, `eidesoul/library/severity/<lang>.json`, key lexicon `affirmations`/`level_words`, `Handler.target`, dan `scripts/neutral.py`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0033-neutral-core.md`.*
