> Terjemahan turunan — jangan sunting; sumber: 0000-initial-invariants.md (sha256: aa4e32c7a55f)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0000: Invarian Awal — Bentuk Permukaan, Data Bahasa, Seam Netral Embodiment

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** tiga fondasi diputuskan setelah set ADR pertama ditulis: di mana permukaan baru boleh hidup, di mana data bahasa tinggal, dan apakah core boleh mengasumsikan chat. Ketiganya satu paket karena masing-masing mengunci setiap fitur sesudahnya — termasuk robot, harness, dan body editing yang direncanakan pemilik.
- **Supersedes:** —
- **Terkait:** ADR-0001 (memori), ADR-0007 (teks kapabilitas & approval), ADR-0009 (rumah ADR & penjaganya), ADR-0015 (payload), ADR-0016 (pemilik skema), ADR-0018 (kontrak gateway), ADR-0020 (episode), ADR-0021 (keluaran tool), ADR-0022 (`administrator` dan `label`)
- **Order:** foundational — dijalankan sebelum body gateway baru mana pun, sebelum dashboard, dan sebelum pemindahan string/lexicon yang bergantung padanya.
- **Implementasi:** implemented — bukti: §4.2 pack lexicon + pronoun + help hanya katalog, §4.3 `Percept`/`Action` + hint, §4.4 cutover rename, §4.5 `send`/`chat_history` di gateway, body harness, `tests/test_architecture.py`, §7 kotak 8 di `AGENTS.md` (`docs/`, `scripts/`, `tests/`, `panel/`, `apps/` di luar paket)
- **Catatan lokasi:** ini dokumen fondasi lintas-potong, jadi ia duduk di akar rumah ADR (`docs/v0.1.0/adr/`);
  salinan Indonesia di `docs/v0.1.0/adr/id/0001-initial-invariants.md`. ADR engine hidup di
  `docs/v0.1.0/adr/engine/`, ADR web/panel di `docs/v0.1.0/adr/web/`.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| Core berbicara chat | `chat_id` 635 kali di `eidesoul/engine`, `send` 301, `reply` 135, `dm` 125, `ChatMessage` 71, `group` 62 |
| Seam berbentuk chat | `Inbound` membawa `chat_id`, `is_dm`, `is_mention`, `is_reply` (`inbound.py:18-29`) |
| Penyimpanan berbentuk chat | tabel `chats`, `chat_eras`, `chat_people` (`schema.py:69,164,187`) |
| Data bahasa ada di modul | `privacy.py:27`, `context.py:317-320`, `character.py:18`, `guard/patterns.py:16`, `prompts.py:97,239,242,277`; default pronoun `"aku"` di `soul.py:57,221` |
| Satu jalur balasan hardcoded | `commands/konteks.py:21` mengembalikan literal |
| Katalog lengkap | 201 kunci di masing-masing `lang/en.yaml` dan `lang/id.yaml`, paritas 0/0 |
| Spine sudah netral body | memori, affect, guard, lease, worker, dan episode tidak bergantung transport |

Bias chat bukan sekadar soal nama: ia menentukan arti "message", "reply", dan "room" di dalam engine,
sehingga body robot atau editor hanya bisa ditambahkan dengan berpura-pura menjadi chat. Fondasi di
bawah memperbaiki kosakata sebelum lebih banyak fitur dibangun di atasnya.

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Body sederajat | Chat satu body di antara robot, harness, editor; tidak ada yang jadi base class |
| Satu pemilik per konsep | Setiap kosakata punya satu rumah dan satu ADR pemilik |
| Tanpa alias, tanpa shim | Rename mendarat sebagai satu cutover (aturan repo), jadi nama lama tidak menggantung |
| Degradasi jujur | Data bahasa yang hilang melemahkan fitur secara terlihat, bukan diam-diam dalam bahasa yang salah |
| Paket datar | Permukaan baru hidup di luar `eidesoul/`; paket mempertahankan enam subpaketnya |
| Fondasi dulu | Invarian dijalankan sebelum fitur yang jika tidak akan memanggang chat ke dalamnya |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Rename saja** | `chat_id`→`thread_id`, `chats`→`threads`, `is_dm`→hint | Murah, kosakata hilang, tapi seam tetap berbentuk chat |
| **B. Seam Percept/Action** | Engine mengonsumsi `Percept` dan mengeluarkan `Action`; body memasok fakta dan kapabilitas | **Dipilih, dengan A sebagai langkah pertamanya** |
| **C. Tulis ulang engine** | Paket baru, penyimpanan baru | Ditolak: spine sudah benar; biaya jauh di atas manfaat |
| **D. Adapter saja** | Pertahankan tipe chat dan bungkus robot dengan adapter berbentuk chat | Ditolak: setiap body baru mewarisi semantik chat yang tidak dimilikinya |

## 4. Keputusan

### 4.1 Bentuk permukaan (apa boleh tinggal di mana)

`eidesoul/` hanya memuat spine dan appendage-nya: `engine/`, `gateways/`, `providers/`, `library/`,
`lang/`, `vendor/`, `admin/`, `__main__.py`. Sisanya di akar repo: `docs/`, `scripts/`, `tests/`, dan
`panel/` atau `apps/` di masa depan. Tree baru tidak pernah ditambahkan di dalam `eidesoul/engine/`;
body adalah paket di `eidesoul/gateways/<name>/` (ADR-0018), dan aplikasi yang mengimpor engine
(dashboard, editor UI) adalah proses terpisah di luar paket.

### 4.2 Data bahasa tinggal di permukaan data

Daftar khas bahasa pindah ke `eidesoul/library/lexicon/<lang>.json` dengan skema
`{version, language, negations, stopwords, assistant_tells, injection, prompt_examples}`; engine memuat
pack untuk `soul.language` secara lazy lalu cache.

- Pack yang hilang turun fungsi secara eksplisit: pola guard yang tidak bergantung bahasa tetap jalan,
  digest jatuh ke pemilihan berbasis panjang, contoh prompt dilewati — masing-masing dengan satu warning
  yang terlihat di `/status`. Ia tidak pernah meminjam daftar bahasa lain.
- `soul.pronouns` tetap opsional; saat kosong diselesaikan dari `t("soul.pronoun.default", language)`.
  Tidak ada default kode `"aku"` yang bertahan; katalog tanpa kunci itu gagal saat boot.
- Teks yang dilihat pengguna hanya lewat `t()`; `/help` mewajibkan `command.<name>.description` di
  katalog dan tidak lagi memakai literal handler sebagai cadangan, sedangkan `Handler.description`
  tetap deskripsi tool **untuk model**.
- Percakapan demo CLI pindah ke `tests/fixtures/`.
- Penegakan: `tests/test_language.py` memindai literal string di `eidesoul/engine/**` terhadap daftar
  kata yang diambil **dari** `library/lexicon/id.json`, memeriksa paritas katalog, dan memastikan
  literal default pronoun tidak kembali.

### 4.3 Seam adalah `Percept` dan `Action`

Engine mengonsumsi percept dan mengeluarkan action; tidak ada yang di `engine/` menguji transport.

```
Percept  = source (nama body), thread (id opaque), actor (person_id),
           kind, payload, hints (fakta yang dideklarasikan body), raw (event transport)
Action   = capability (nama tool), target (thread atau alamat), payload,
           approval_class (consent | authorization)
```

- `hints` membawa semantik body (`dm`, `mention`, `reply`, `group`, `presence`) sebagai **fakta dari
  body**. Engine membacanya saat keputusan membutuhkannya (privasi, atribusi, kirim lintas scope); ia
  tidak pernah bercabang pada nama transport dan tidak menuntut hint yang tidak bisa diberikan.
- Body mendeklarasikan kapabilitas dan hook-nya: `GatewayCore` untuk pengiriman, `Rich`/`Directory`
  untuk media dan orang (ADR-0018), plus paket tool khusus body untuk aksi tubuhnya.
- Memori, affect, guard, lease, worker, dan episode tetap seperti sekarang: mereka sudah beroperasi pada
  actor dan unit kerja, bukan pada chat.

### 4.4 Rename mekanis (langkah A dari seam)

Satu cutover, tanpa alias:

| Sekarang | Menjadi |
|---|---|
| `Inbound.chat_id` | `Percept.thread` |
| `ChatMessage` | `Message` (envelope netral transport) |
| `Inbound.is_dm` / `is_mention` / `is_reply` | `Percept.hints["dm" \| "mention" \| "reply"]` |
| tabel `chats`, `chat_eras`, `chat_people` | `threads`, `thread_eras`, `thread_people` |
| `messages.chat_id` | `messages.thread_id` |
| `rooms_of(person_id)` | `threads_of(person_id)` |

DDL dan urutan migrasi milik ADR-0016; namanya milik ADR ini. Aturan repo melarang shim, jadi nama lama
hilang pada perubahan yang sama dan semua pemanggil dimigrasikan.

### 4.5 Kapabilitas khusus chat keluar dari core

`send` (egress chat) dan `chat_history` (menelusuri arsip) pindah ke `eidesoul/gateways/whatsapp/tools`
dan `.../tools` milik body yang memiliki arsip. Engine hanya menyimpan tool netral body:
`read`, `write`, `shell`, `sandbox`, `web_search`, `mcp`, `memory`, `recipe`, `skill`, `settings`.
Body robot mengekspos tool gerak/sensor, body harness mengekspos tool run/inspect, body editor
mengekspos tool edit dokumen — semuanya di paket gateway masing-masing (aturan repo: aksi tubuh tidak
pernah di core).

### 4.6 Urutan kerja

1. Data bahasa: berkas lexicon, default pronoun, `t()` untuk `/konteks`, fixture keluar, `test_language`.
2. Cutover rename (§4.4) bersama migrasi ADR-0016.
3. Seam Percept/Action (§4.3) dan pemindahan `send`/`chat_history` keluar dari core (§4.5).
4. Baru body non-chat pertama (robot, harness, atau editor) — dan baru sesudah itu dashboard.

### 4.7 Penegakan

- `tests/test_language.py` (§4.2).
- `tests/test_architecture.py`: pemindaian AST yang memastikan `eidesoul/engine/**` tidak memuat kosakata
  transport di luar allowlist eksplisit (kunci `hints`, nama netral gateway), dan tidak ada modul di
  `eidesoul/` di luar `gateways/` yang mengimpor `neonize` atau paket gateway (aturan repo dibuat mekanis).
- `scripts/adr.py --check` tetap menjadi gate dokumen ini; kini ia menegakkan layout folder (root,
  `engine/`, `web/`), nomor kronologis yang unik global, dan rujukan yang terselesaikan (ADR-0009 §4.1).

## 5. Konfigurasi

Tidak ada kunci engine baru untuk seam. Data bahasa menurunkan path dari `soul.language`
(`eidesoul/library/lexicon/<code>.json`), dengan override test-only `engine.language.lexicon_dir`.
Body mendeklarasikan nama dan kapabilitasnya di bagian config miliknya, `gateways.<name>`.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Kosakata transport di `eidesoul/engine/**` (`chat_id`, `is_dm`, `ChatMessage`, `chat_eras`) | 0 sesudah rename |
| Literal Indonesia di `eidesoul/engine/**` (di luar lexicon) | 0 (scanner) |
| Paritas katalog `en` ⇄ `id` | 0 kunci hilang per sisi |
| Command tanpa `command.<name>.description` | 0 (boot gagal kalau ada) |
| Body yang berjalan end-to-end (smoke) | ≥ 2 — chat plus satu body non-chat |
| Tool khusus body di dalam `eidesoul/engine/tools` | 0 |
| Tree baru di dalam `eidesoul/engine/` | 0 |

## 7. Acceptance criteria

- [ ] `library/lexicon/{id,en}.json` ada, lolos check, dan dibaca engine; grep tidak menemukan literal kata Indonesia di `eidesoul/engine/**`.
- [ ] Soul dengan `language: en` dan `pronouns` kosong menghasilkan pronoun English; katalog tanpa `soul.pronoun.default` gagal saat boot.
- [ ] `/konteks` dan handler lain menjawab hanya lewat `t()`; `/help` menolak boot saat kunci deskripsi command hilang; `__main__.py` tidak menyimpan percakapan demo.
- [ ] `Percept`/`Action` menggantikan `Inbound`/`ChatMessage` di engine; `hints` membawa `dm`/`mention`/`reply`; tidak ada cabang engine pada nama transport (test AST).
- [ ] Rename mendarat satu cutover: tidak ada `chat_id`/`is_dm`/`ChatMessage`/`chat_eras` tersisa di engine, dan migrasi DDL ada di urutan ADR-0016.
- [ ] `send` dan `chat_history` hidup di paket gateway; `eidesoul/engine/tools` tidak memuat tool khusus body.
- [ ] Satu body non-chat menyelesaikan smoke run (percept masuk, action keluar) tanpa kasus khusus di engine.
- [ ] `docs/`, `scripts/`, `tests/`, `panel/`, `apps/` tetap di luar `eidesoul/`, dan `AGENTS.md` menyatakannya.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Rename 635 titik menyembunyikan perubahan perilaku | Cutover mekanis (search, cek AST), test mengunci perilaku per jalur, dan penjaga AST gagal kalau nama lama kembali |
| Rename penyimpanan butuh migrasi | ADR-0016 memegang urutannya; tanpa dual-write, tanpa view kompatibilitas |
| Prompt regresi saat framing chat jadi fakta body | Test payload memastikan blok fakta permukaan; body yang tidak memasoknya turun ke kalimat netral |
| Body baru memancing kasus khusus di engine | Allowlist AST §4.7; aksi body hanya di paket gateway |
| Pack bahasa melenceng dari katalog | Satu scanner membaca keduanya dan membandingkan paritas |
| Fondasi tertunda dan fitur memanggang chat lagi | Baris `Order:` (§4.6) plus daftar acceptance ADR ini; dashboard eksplisit paling akhir |

## 9. Interaksi dengan ADR lain

- **ADR-0007**: `rooms_of`/`cross_*` menjadi fakta keanggotaan dari body; kelas approval tidak berubah.
- **ADR-0009**: rumah ADR mendapat kategori (root untuk lintas-potong, `engine/`, `web/`) dan aturan
  bahwa nomor unik di seluruh rumah ADR serta tidak pernah dipakai ulang (`0000` untuk dokumen fondasi
  ini); ADR ini duduk di root karena mengikat semua permukaan.
- **ADR-0016**: pemilik DDL `threads`/`thread_people`/`thread_eras` dan urutan migrasinya.
- **ADR-0018**: `GatewayCore`/`Rich`/`Directory` tetap; body menambah paket tool dan hints sendiri.
- **ADR-0020**: episode dan skill sudah netral body; episode robot memakai sinyal penutup yang sama.
- **ADR-0021**: cap keluaran tool tetap per tool; tool body mewarisi kontrak yang sama.
- **ADR-0022**: `administrator` dan `label` adalah data orang/role, bukan kosakata body.

## 10. Referensi kode

- `eidesoul/engine/inbound.py:18-29` — `Inbound` dan `Gateway` (seam yang diganti).
- `eidesoul/engine/schema.py:44-67,69,164,187` — `messages`, `chats`, `chat_eras`, `chat_people`.
- `eidesoul/engine/agent.py` — pemanggilan kirim dan penanganan turn (rename + seam).
- `eidesoul/engine/turn/prompts.py:141-147` — render role; fakta body masuk payload di sini.
- `eidesoul/gateways/whatsapp/tools/send.py` dan `eidesoul/gateways/whatsapp/tools/chat_history.py` — dua tool khusus chat, keduanya sudah keluar dari core.
- `eidesoul/engine/soul.py:57,221` — default `pronouns` (akan dihapus).
- `eidesoul/engine/access/privacy.py:27`, `context.py:317-320`, `character.py:18`, `guard/patterns.py:16`, `prompts.py:97,239,242,277` — data bahasa (akan dipindah).
- `eidesoul/engine/commands/konteks.py:21` — balasan hardcoded (akan lewat `t()`).
- `eidesoul/__main__.py:429,456` — fixture Indonesia (akan pindah ke `tests/fixtures/`).
- `eidesoul/lang/en.yaml`, `eidesoul/lang/id.yaml` — 201 kunci masing-masing, paritas 0/0.
- `eidesoul/library/patterns|facts|affect/**` — preseden permukaan data yang diperiksa.

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0001-initial-invariants.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/0000-initial-invariants.md`.*
