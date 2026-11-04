> Terjemahan turunan — jangan sunting; sumber: engine/0041-voice-layer-off-by-default.md (sha256: 30fea6ffee51)

# ADR-0041: Konfigurasi Voice Layer — Satu Section, Off secara Default, dan Config yang Berkomentar Penuh

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** mengikuti ADR-0027 (clean by default), ADR-0031 (struktur engine), ADR-0033 (core netral), ADR-0039 (setup terminal), dan ADR-0040 (penamaan); menggantikan empat section voice top-level dengan satu section `engine.voice` dan membuat `config.yaml` yang dihasilkan mendokumentasikan dirinya.
- **Supersedes:** —
- **Terkait:** ADR-0001 (memory source of truth), ADR-0027 (distribusi), ADR-0031 (struktur modul), ADR-0033 (core netral), ADR-0039 (setup terminal), ADR-0040 (penamaan)
- **Implementasi:** draft — keputusan dicatat; schema, loader, dan template berkomentar belum diimplementasikan di ADR ini.
- **Catatan lokasi:** voice layer mengikat runtime engine, jadi ADR ini duduk di `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0041-voice-layer-off-by-default.md`.
- **Pembaruan lokasi:** ADR-0045 memindahkan section dari `engine.voice` ke `soul.voice`, menghapus blok pack `soul.style` lama, dan menghapus pemilihan pack dari bagian karakter. Baca setiap jalur `engine.voice` di bawah sebagai `soul.voice`; semantik master/off-by-default tetap berlaku. Opsi E tidak lagi ditolak: tuning voice adalah data per soul.

---

## 1. Konteks

ADR-0027 menetapkan aturan clean by default: gateway, tool, dan plugin mati sampai operator mengaktifkannya; hanya spine yang selalu on. Voice layer bukan spine: emotion, humanize, character, dan rhythm adalah fitur presentasi, bukan memory store, guard, atau lease.

Permukaan yang dikirim bertentangan dengan aturan itu dan memecah satu konsep menjadi empat sakelar top-level:

| Permukaan | Perilaku saat ini | Bukti |
| --- | --- | --- |
| Template `init` | menulis `affect.enabled: true`, `affect.vibe.enabled: true`, `humanize.enabled: true`, `style.enabled: true`, `character.enabled: false` | `admin/templates.py` |
| Default loader | `affect`, `affect.vibe`, dan `style` default `True`; `humanize` dan `character` default `False` | `engine/config.py` |
| Nilai `init` | `build(voice="on")`, jadi nilai true dari template bertahan | `admin/init.py` |
| Wizard | bertanya *"keep emotion, humanize, and character on?"* dengan default `on` | `admin/wizard.py` |
| CLI | `--voice-layer` mendokumentasikan *on (default)* | `__main__.py` |
| README | *"The voice layer — emotion, humanize, character — is on by default"* | `README.md` |

Dua defect mengikuti. Pertama, operator melihat empat flag `enabled` untuk satu layer, plus daftar ketiga di `admin/init.py` (`VOICE_PARTS`), dan kata `style` berarti dua hal berbeda: `engine.style` adalah store ritme statistik, sedangkan `soul.style` adalah pemilihan pack karakter. Kedua, `config.yaml` yang dihasilkan adalah `yaml.safe_dump` polos dari sebuah dictionary (`templates.dump`): tanpa komentar, tanpa penjelasan, dan tanpa cara bagi operator baru untuk mengetahui apa yang dilakukan sebuah key atau default apa yang dibawanya.

Arahan owner eksplisit: satu section, satu sakelar master, `enabled` per sub-section sebagai satu-satunya selector, tanpa daftar parts terpisah, setiap section voice off secara default, dan `config.yaml` yang dihasilkan menjelaskan dirinya sendiri.

## 2. Decision drivers

| Driver | Konsekuensi |
| --- | --- |
| Satu section | `engine.voice` memiliki setiap knob voice; empat section top-level lama hilang |
| Satu master | `engine.voice.enabled` adalah satu-satunya sakelar yang ditanyakan `init`; default `false` |
| Satu selector per bagian | Setiap sub-section mempertahankan `enabled`-nya; tidak ada daftar `parts` yang menduplikasinya |
| Clean by default | Default efektif adalah off; default sub-section inert sampai master on |
| Config yang mendokumentasikan diri | `config.yaml` yang dihasilkan membawa komentar untuk setiap section dan setiap key `enabled` |
| Mekanisme tetap terpisah | Empat implementasi mempertahankan modul dan test-nya; hanya namespace yang disatukan |
| Cutover bersih | Key top-level lama dihapus; tanpa alias, tanpa shim, tanpa jalur baca ganda |
| Spine tidak berubah | Memory, guard, lease, worker, dan kontrak gateway mempertahankan default |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkasan | Nilai |
| --- | --- | --- |
| **A. Pertahankan empat section top-level** | `affect`, `humanize`, `character`, `style` masing-masing dengan `enabled` | Ditolak: empat sakelar untuk satu layer, dan `style` bertabrakan dengan `soul.style` |
| **B. `engine.voice` dengan daftar `parts`** | satu master plus daftar bagian aktif | Ditolak: `parts` menduplikasi `enabled` per sub-section; dua selector dapat melenceng |
| **C. `engine.voice` dengan master dan `enabled` per sub-section** | satu section, dua level | **Dipilih** |
| **D. Satu bag `engine.voice` datar** | setiap key di satu level | Ditolak: sekitar lima belas key dengan bentuk berbeda; validasi, test, dan docs kehilangan batas |
| **E. Taruh knob di `soul.yaml`** | perlakukan tuning voice sebagai data persona | Ditolak: soul adalah data, engine adalah mekanisme; `soul.style` tetap pemilihan pack |
| **F. Biarkan config yang dihasilkan tanpa komentar** | pertahankan `yaml.safe_dump` | Ditolak: default tidak transparan; operator tidak tahu apa yang dilakukan sebuah key |
| **G. `config.yaml` berkomentar** | komentar dihasilkan dari satu sumber | **Dipilih** |

## 4. Keputusan

### 4.1 Satu section, dua level

```text
engine:
  voice:
    enabled: false
    emotion:
      enabled: true
    rhythm:
      enabled: true
    character:
      enabled: true
    humanize:
      enabled: true
```

Master adalah `engine.voice.enabled`, default `false`. Setiap sub-section mempertahankan `enabled`-nya, default `true`, tetapi nilainya inert selama master `false`. Section top-level lama (`engine.affect`, `engine.humanize`, `engine.character`, `engine.style`) dihapus.

`engine.style` di-rename menjadi `engine.voice.rhythm` agar kata `style` tidak lagi menamai dua hal berbeda. `soul.style` tidak berubah: ia tetap pemilihan pack (`patterns`, `facts`, `registers`, `max_patterns`, `max_facts`) yang diturunkan `eidesoul library resolve` dari teks soul.

### 4.2 State efektif

| `engine.voice.enabled` | `enabled` sub-section | Efektif |
| --- | --- | --- |
| `false` | apa pun | off; nilai sub-section diabaikan |
| `true` | `true` | on |
| `true` | `false` | off |

Nilai eksplisit di `config.yaml` yang ada tidak pernah ditimpa `init`. Master dan sub-section adalah satu-satunya selector; tidak ada key `parts`.

### 4.3 Apa yang digerbang setiap bagian

| Bagian | Menggerbang | Saat off |
| --- | --- | --- |
| `emotion` | `AffectStore` (PAD, drives, relasi, `vibe`) dan blok prompt emosi | store tidak dibangun; `/mood` dan `/calmdown` menjawab unavailable; memori baru tidak membawa metadata valence |
| `rhythm` | `StyleStore` (ritme chat statistik) dan blok prompt style/person-style | store tidak dibangun; prompt tidak menerima blok ritme |
| `character` | pass `polish`/`regulate` **dan** pemilihan `select_patterns`/`select_facts` dari `soul.style` | draft tidak ditulis ulang; tidak ada pack pattern atau fact yang dipilih |
| `humanize` | perencanaan bubble dan pacing delivery (`max_bubbles`, `cpms`, delay, sticker) | satu pesan, tanpa typing atau delay antar-bubble; `max_bubbles` diabaikan |

### 4.4 `init`, wizard, dan flag

`init` bertanya satu hal: nyalakan voice layer atau tidak. Default `off`. `--voice-layer` menerima `off` (default) dan `on`; ia hanya menyentuh master. Wizard tidak bertanya daftar bagian; satu bagian dimatikan di `config.yaml` dengan menyetel `enabled`-nya `false`. Daftar `VOICE_PARTS`/`voice_off()` di `admin/init.py` dihapus.

## 5. Config yang dihasilkan

`config.yaml` yang dihasilkan mendokumentasikan dirinya di setiap kedalaman. File itu berisi blok host (`database`, `workspace`, `roles_file`, `soul_file`, `sync_documents`, `sandbox`, `web_search`, `mcp`, `plugin`, `plugins`), blok `engine`, dan blok `gateways`; ketiganya tercakup.

- Setiap section top-level punya komentar yang menyatakan tujuannya.
- Setiap key di setiap kedalaman punya komentar: tujuannya, default-nya, dan efek `true` serta `false` bila nilainya sakelar.
- Tidak ada key yang dibiarkan telanjang; tidak ada section yang dirender sebagai mapping kosong tanpa komentar yang menjelaskan bentuknya.
- Komentar berbahasa Inggris, seperti permukaan operator lainnya, dan dihasilkan dari satu sumber (`admin/templates.py`), sehingga default tidak dapat melenceng antara komentar dan pohon dataclass.
- Komentar diabaikan loader dan `eidesoul config import`; file tetap YAML valid.

Peta komentar adalah data yang di-key oleh dotted path (`database.pool_size`, `engine.llm.retry.base_seconds`, `sandbox.image`, `gateways.cli.enabled`). Sebuah test menelusuri YAML yang dihasilkan dan memastikan setiap leaf key punya komentar.

Contoh bentuk komentar:

| Baris komentar | Arti |
| --- | --- |
| `# database.pool_size — Postgres connection pool size. Default: 5.` | key host dengan default-nya |
| `# sandbox.image — container image for the sandbox. Default: ...` | key host dengan default-nya |
| `# engine.llm.timeout_seconds — per-call model timeout. Default: 120.` | key engine dengan default-nya |
| `# voice.enabled — master switch for the voice layer. Default: false.` | sakelar dan default-nya |
| `#   false: emotion, rhythm, character, and humanize are all off.` | efek off |
| `#   true:  each sub-section decides; a sub-section defaults to true.` | efek on |

## 6. Konfigurasi

| Key | Default | Arti |
| --- | --- | --- |
| `engine.voice.enabled` | `false` | sakelar master; satu-satunya nilai yang ditulis `init` |
| `engine.voice.emotion.enabled` | `true` | PAD, drives, relasi, vibe; inert selama master off |
| `engine.voice.rhythm.enabled` | `true` | ritme chat statistik; inert selama master off |
| `engine.voice.character.enabled` | `true` | polish/regulate dan pemilihan pack; inert selama master off |
| `engine.voice.humanize.enabled` | `true` | perencanaan bubble dan pacing; inert selama master off |

Semantik flag dan wizard:

| Input | Hasil |
| --- | --- |
| `--voice-layer off` atau tidak ada | `engine.voice.enabled: false` |
| `--voice-layer on` | `engine.voice.enabled: true`; sub-section mempertahankan nilainya |
| wizard kosong | default `off` |
| wizard `on` | master `true` |

Tidak ada key `parts` yang diperkenalkan. Key lama `engine.affect`, `engine.humanize`, `engine.character`, dan `engine.style` tidak dibaca lagi. `soul.style` tidak berubah.

## 7. Metrik dan gate

| Metrik | Target |
| --- | ---: |
| Bagian voice yang efektif on secara default | 0 |
| Section voice top-level | 1 (`engine.voice`); empat lama: 0 |
| Key `parts` | 0 |
| Key di setiap kedalaman di `config.yaml` yang dihasilkan tanpa komentar | 0 |
| Key telanjang di `config.yaml` yang dihasilkan | 0 |
| Section yang dirender sebagai mapping kosong tanpa komentar penjelas | 0 |
| `config import` dan loader mem-parse file berkomentar | lolos |
| `pytest -q` | lolos |
| `ruff check` | lolos |
| `python -P scripts/types.py --check` | lolos |
| `python -P scripts/adr.py --check` | lolos |
| `python -P scripts/adr.py --translate-check` | lolos |
| `python scripts/selfcheck.py --check` | lolos |

## 8. Kriteria penerimaan

- [ ] `engine.voice` adalah satu-satunya section voice; empat section top-level lama hilang.
- [ ] `engine.voice.enabled` default `false`; sub-section inert selama master off.
- [ ] Tidak ada key `parts` di config, wizard, atau loader.
- [ ] `engine.style` di-rename `engine.voice.rhythm`; `soul.style` tidak berubah.
- [ ] `--voice-layer` hanya menerima `off` dan `on`; wizard hanya bertanya master.
- [ ] `voice.character.enabled: false` mematikan pass polish dan pemilihan pattern/fact.
- [ ] `voice.humanize.enabled: false` menghasilkan satu pesan tanpa typing atau delay antar-bubble.
- [ ] `config.yaml` yang dihasilkan punya komentar di setiap key di setiap kedalaman — host, engine, dan gateways.
- [ ] Tidak ada key telanjang; tidak ada section yang dihasilkan sebagai mapping kosong tanpa komentar penjelas.
- [ ] `eidesoul config import` dan loader mem-parse file berkomentar.

## 9. Risiko dan mitigasi

| Risiko | Mitigasi |
| --- | --- |
| Komentar melenceng dari default | Komentar adalah data; test cakupan memastikan keberadaannya, dan nilai tetap diturunkan dari dataclass |
| File `config.yaml` lama berhenti bekerja | Pra-rilis; `config import` memetakan key lama sekali; tanpa fallback runtime |
| Master dan sub-section membingungkan operator | Tabel state efektif adalah kontraknya; test mencakup master-off, master-on, dan sub-off |
| Rename `style` ke `rhythm` meninggalkan referensi basi | Cutover bersih; gate `grep` dan checklist rename mencakup pohon |
| `character` off tetap memilih pack | `select_patterns`/`select_facts` digerbang oleh `voice.character.enabled`; test memastikan tidak ada pack saat off |
| `humanize` off tetap memecah bubble | Jalur off menyetel satu pesan dan mengabaikan `max_bubbles`; test memastikan satu kiriman |
| Affect off kehilangan metadata valence di memori baru | Diterima; jalur memori memperlakukan valence sebagai absen. Keputusan terpisah diperlukan untuk mempertahankannya |
| Peta komentar tumbuh besar | Satu peta di-key oleh dotted path; test cakupan gagal pada key telanjang |

## 10. Interaksi dengan ADR lain

- **ADR-0027**: ADR ini menerapkan aturan clean by default pada voice layer; pengecualian spine tidak berubah.
- **ADR-0001**: memory, Mem0, dan action ledger tidak terpengaruh; hanya default store presentasi yang berubah.
- **ADR-0031**: batas modul dan cap 800 baris tidak berpindah; peta komentar tetap di `admin/templates.py`.
- **ADR-0033**: tidak ada literal bahasa, budaya, atau vendor yang masuk ke engine; komentar adalah permukaan operator, bukan teks chat.
- **ADR-0039**: langkah voice mempertahankan posisinya di rail; default dan kata-katanya berubah, sedangkan perilaku location, credential, dan review tidak.
- **ADR-0040**: nama perintah `init` mengikuti cutover penamaan; ADR ini memakai `init` generik.

## 11. Referensi kode

- `eidesoul/admin/templates.py` — template `config.yaml` berkomentar dan peta komentar.
- `eidesoul/engine/config.py` — `VoiceConfig`, empat sub-config, dan fallback loader.
- `eidesoul/engine/turn/loop.py` — call site emotion, rhythm, character, dan humanize.
- `eidesoul/engine/character/character.py` — gate `polish`/`regulate` dan pemilihan pattern/fact.
- `eidesoul/engine/stores/affect.py` — affect store, dibangun hanya saat `voice.emotion` on.
- `eidesoul/engine/stores/style.py` — rhythm store, dibangun hanya saat `voice.rhythm` on.
- `eidesoul/engine/turn/reply.py` — `plan_chunks` dan jalur humanize-off.
- `eidesoul/engine/turn/delivery.py` — `typing_delay` dan delay antar-bubble.
- `eidesoul/admin/init.py` — sakelar master, penghapusan `VOICE_PARTS`/`voice_off()`, dan `build()`.
- `eidesoul/admin/wizard.py` — prompt voice master-only dan default-nya.
- `eidesoul/__main__.py` — argumen `--voice-layer` dan teks help-nya.
- `eidesoul/engine/agent.py` — laporan konfigurasi `/status` untuk bagian voice.
- `README.md`, `README.id.md` — paragraf voice layer.
- `tests/` — cakupan default-off, master-on, sub-off, template berkomentar, dan satu pesan.

---

*ADR ini tinggal di `docs/v0.1.0/adr/engine/0041-voice-layer-off-by-default.md`.*
