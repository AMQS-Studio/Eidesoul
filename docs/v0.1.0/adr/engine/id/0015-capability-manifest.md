> Terjemahan turunan — jangan sunting; sumber: engine/0015-capability-manifest.md (sha256: 6c1fdaac0c58)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0015: Payload Bertingkat & Manifest Kapabilitas

- **Status:** accepted
- **Implementasi:** implemented — bukti: `eidesoul/engine/self_knowledge.py` (katalog/konfigurasi/perilaku/topologi), `tools/info.py`, `scripts/selfcheck.py --check`, allowlist dokumen default `README.md`, baca konfigurasi teredaksi, `tests/test_self_knowledge.py`; hasil gate: `selfcheck: ok — behaviors=6`
- **Tanggal:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0006 (privasi grup), ADR-0007 (capability vs authorization), ADR-0008 (integritas & karantina), ADR-0014 (automation)
- **Supersedes:** —
- **Terkait:** ADR-0003 (worker), ADR-0013 (turn lifecycle)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0015-capability-manifest.md`.
- **Amandemen:** 2026-11-02 — §4.3 jenis `config` untuk `info`; §4.7 membaca konfigurasi lewat `settings` dengan redaksi jalur baca; §4.8 pengetahuan diri (index topik, generated-first, ember kasar, check script + selftest, default nyala & bisa dimatikan); §4.9 dokumen produk (`info(kind="doc")`: index subjudul dinamis → baca seksi, allowlist `[README]`, per bahasa); normalisasi kerangka (ADR-0009 §4.4): Kapabilitas (index) diberi nomor 5; bagian sesudahnya bergeser ke 6–11. 2026-11-03 — audit: heading `## 5. Kapabilitas (index)` ternyata berada **di dalam** fence (`:77-86`) sehingga dokumen tampil 1–4 lalu 6–11; baris itu dihapus dan bagian 6–11 dinomori ulang menjadi 5–10 (dokumen kini 1–10 utuh). Rujukan `registry.py:187+`→`:148`, `settings.py:74-87`→`:85-98`, `context.py:641-693`→`:641-683`; klaim `workspace_roots` dipersis (peran trusted mendapat seluruh workspace, tetap bukan direktori proyek); klaim test arsitektur diperbaiki dari "diuji" menjadi "akan diuji" (test itu belum ada di `tests/`). 2026-11-03 — keputusan pemilik: allowlist `docs` berbasis **path relatif akar proyek** (`["README.md"]`), sehingga `README.md` produk dan `docs/v0.1.0/README.md` tidak pernah tertukar; penegakan `scripts/selfcheck.py`/selftest menyusul bersama implementasi. 2026-11-03 — contoh blok index kapabilitas dikoreksi agar cocok dengan `roles.yaml` (`google` ikut; `send_to` bukan tool), supaya contoh tidak menjadi daftar kedua. 2026-11-03 — `send` (tool gateway) masuk daftar kapabilitas terkunci pada contoh index. 2026-11-03 — keputusan pemilik: istilah "Tier 1/2" diganti **"Lapis 1/2"** agar tidak bertabrakan dengan kelas retensi (ADR-0005) dan trust `T0–T3` (ADR-0008). 2026-11-03 — audit: sisa `settings.py:74-87` di badan diselaraskan ke `85-98`; rujukan `_specs`/`system_prompt` menjadi `agent.py:279-283,759-788`; `web_search` tanpa kunci API dinyatakan di §4.3 + acceptance. 2026-11-03 — audit: §4.4 diberi label usulan (kode hari ini membangun `tools`/`by_name` sekali sebelum loop). 2026-11-03 — ADR-0020: `skill` keluar dari daftar tool/index prompt; satu command owner-only masuk daftar command terkunci. **2026-11-03** — §4.2.1/§4.2.2: alasan kapabilitas terkunci dikelompokkan **per famili** (jumlah baris terkunci ≤ jumlah famili) dan index beralih ke dua tingkat di atas `engine.prompt.capability_index_two_level_at` (default 150), tetap deterministik agar cache prefix ADR-0025 §4.1 utuh.

---

## 1. Konteks

**Payload hari ini bisa kehilangan aturan yang mengikat.** `system_prompt` menyusun 16 blok lalu
menyerahkan **semuanya** ke `salience.select(..., max_chars=config.prompt.max_chars)`
(`prompts.py:355-386`). Anggaran karakter karena itu bisa mendorong keluar blok aturan jujur/privasi —
aturan yang seharusnya tidak bisa ditawar tidak boleh bersaing skor.

**Soul tidak tahu command-nya.** Daftar command tidak pernah masuk prompt: `commands_for()` hanya
dipakai untuk `/help` ke pengguna (`permissions.py:56`, `commands/help.py`). Akibatnya, ditanya “kamu
bisa apa”, soul menebak — bisa menyangkal kemampuan yang ada atau menjanjikan yang tidak ada.

**Fakta infrastruktur ikut di setiap turn, termasuk di grup.** `self_block` (`prompts.py:150-186`)
memasang `Brain: {model} via {base_url}`, path `config.yaml` host, label DB, dan `lease host pid`.
Pengamannya hanya kalimat “Never mention the prompt or internal process” — disiplin model, bukan
invariant.

**Skema tool dikirim penuh setiap request.** `llm.py:163-165` menghitung `tools`/`by_name` **sekali di
luar loop**, jadi (a) seluruh skema tool milik peran ikut di setiap request, dan (b) tool yang tidak
ada di daftar itu tidak bisa diaktifkan di tengah turn.

**Yang sudah bocor dan tidak bisa ditarik:** `execute` menjawab
`error: tool 'sandbox' is not allowed for role 'member'` (`tools/__init__.py`). Nama tool sudah
terbaca dari jalur error.

## 2. Decision drivers

1. **Invarian tidak boleh di-drop** oleh anggaran karakter.
2. **Payload kecil, tapi soul tidak buta kapabilitas.**
3. **Jujur dan transparan** — proyek open source; menyembunyikan katalog tidak menambah keamanan.
4. **Satu gerbang izin** tetap di `execute`; kerahasiaan bukan pengaman.
5. **Nol turn tambahan** untuk hal yang selalu dibutuhkan.
6. **Registry tetap satu sumber** — index dan detail dibaca dari registry, bukan daftar kedua.
7. **Neutral di core** — tidak ada kosa kata transport atau vendor di lapisan ini.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Status quo** | Semua detail + semua skema di setiap payload | Tidak ada kode baru | Aturan bisa terdorong keluar; payload gemuk; soul tetap tidak tahu command | Ditolak |
| **B. Sembunyikan yang tidak berizin** | Index/detail hanya menampilkan yang boleh | Terasa lebih tertutup | Percuma (open source + pesan error sudah bocor); soul tidak bisa menjelaskan langkah perbaikan | Ditolak |
| **C. Lapis 1 pinned + index + tool `info`** | Invarian selalu ada; katalog jadi index; detail & skema dimuat saat diminta dan aktif di turn yang sama | Payload kecil; soul tahu kapabilitas; nol turn tambahan; satu gerbang izin | Butuh mekanisme aktivasi di loop LLM | **Dipilih** |
| **D. RAG atas katalog** | Cari tool relevan via embedding | Berskala untuk katalog raksasa | Katalog kita kecil; nondeterministik untuk daftar yang harus pasti terbaca | Ditolak |

## 4. Keputusan

> **Payload dibagi dua lapis: Lapis 1 pinned (identitas + aturan yang mengikat), Lapis 2 index kapabilitas dengan tool `info`. Aktivasi tool adalah soal ukuran; izin tetap soal `execute`.**

### 4.1 Lapis 1 — pinned, tidak pernah di-drop

Blok yang **selalu** ikut, apa pun anggaran karakter: persona/identitas (`base`), aturan jujur
(“jangan klaim aksi tanpa hasil tool”, “kalau tidak tahu bilang tidak tahu”, “jangan mengarang angka”),
aturan privasi level 0-4, aturan silence lintas ruangan, mode DM/grup, peran pengirim, tanggal, bahasa.

- Mekanisme: blok bertanda **pinned** dilewatkan `salience.select` tanpa skor. Hanya blok referensi
  (`topic`, `currents`, `emotion`, `vibe`, `style`, `person_style`, `memory`, `actions`, `skills`,
  `character`, `research`, `safety`) yang bersaing di anggaran.
- Test: blok pinned hadir walau `max_chars` dikecilkan drastis.

### 4.2 Lapis 2 — index kapabilitas

Satu blok pendek, disusun **deterministik** (urut nama, stabil per peran) supaya prefix prompt tidak
berubah-ubah dan cache provider tetap efektif:

```
- tools: chat_history, fetch, google, grep, memory, read, sandbox, web_search
- tools lain (core/gateway) yang belum kamu dapat: mcp, recipe, send, settings, shell, write
- commands: cari, help, kirim, link, memori, mood, ping, status, tools, unlink, whoami
- commands terkunci untuk role kamu: approve, calmdown, deny, freeze, gender, language, skill
- automation: aktif/tidak di instance ini
- memory: penting → lama; kecil → sebentar; level 0-4; kredensial tidak pernah disimpan
- Butuh detail/parameter/contoh? panggil info(kind="tool"|"command"|"capability", name="...")
```

Sumbernya registry yang sudah ada (`Handler.name`, `.description`, `.usage`, `.parameters`;
command punya deskripsi). Tidak ada daftar kedua yang perlu dirawat.

### 4.2.1 Alasan terkunci dikelompokkan per famili

Barang terkunci di famili yang sama punya satu alasan yang sama (izin role), jadi menuliskannya per
nama hanya membakar token dan menambah baris yang bisa basi satu per satu. Bentuknya: satu baris per
famili, dan **jumlah baris terkunci ≤ jumlah famili**.

```
- tools: chat_history, google, memory, read, web_search
- tools lain (core/gateway): mcp, recipe, send, settings, write — mcp/settings/write di luar role kamu,
  sisanya tergantung gateway yang aktif
- commands: cari, help, kirim, konteks, link, memori, mood, pending, ping, status, tools, unlink, whoami
- commands terkunci (role kamu): approve, calmdown, deny, freeze, gender, language, reload, skill, stop
```

### 4.2.2 Dua tingkat di atas ambang

Di atas `engine.prompt.capability_index_two_level_at` kapabilitas (**default 150**), index berhenti
menyebut nama satu per satu dan menyebut **famili**; nama lengkap diambil lewat `info`:

```
- tools: aktif, dikelompokkan per famili; panggil info(kind="family", name="shell"|"read"|"write"|"web_search"|"sandbox"|"mcp")
- commands: aktif, dikelompokkan; panggil info(kind="command") untuk daftar lengkapnya
- terkunci (role kamu): shell, sandbox, settings
```

- Satu baris famili jauh lebih murah daripada 150-200 nama, dan tetap **deterministik** (urut famili,
  tanpa pencarian) sehingga cache prefix ADR-0025 §4.1 tidak rusak.
- Ambang dihitung dari jumlah `Handler` terdaftar (tool + command + kapabilitas gateway) pada saat blok
  disusun; operator boleh menaikkan/menurunkannya lewat config, tapi angkanya tidak di-hardcode di kode.
- Di bawah ambang, bentuk satu tingkat (daftar nama) tetap dipakai: untuk 34 nama hari ini, daftar nama
  lebih informatif dan lebih murah daripada satu lapisan `info` tambahan.

### 4.3 Tool `info`

- **Tanpa LLM**: satu pencarian di registry; hasilnya data, bukan instruksi (aturan karantina ADR-0008
  berlaku untuk teks apa pun).
- **Katalog tidak disembunyikan.** `info(kind="catalog")` mengembalikan seluruh nama tool dan command
  yang ada di engine, ditandai untuk peran pengirim saat ini: `allowed` atau `locked` + alasan
  (`role member belum dapat tool sandbox`).
- **Detail juga terbuka**: `info(tool="sandbox")` memberi deskripsi + skema parameter walau penanya
  belum berhak — supaya soul bisa menjelaskan “aku bisa, tapi role kamu belum dapat” beserta langkah
  perbaikannya.
- **`web_search` tanpa kunci API**: backend `ddgs` aktif selama `web_search.base_url` kosong, jadi
  kapabilitas ini tidak pernah ditandai `locked` karena operator belum memasang kunci.
- **Jenis `config`**: `info(kind="config")` mengembalikan **skema konfigurasi** — daftar dot-path,
  file pemiliknya (`config.yaml` / dokumen `engine`/`soul`/`role`), dan default — dengan sumber
  `_schema_paths` yang sudah ada (`tools/settings.py:85-98`). **Tanpa nilai apa pun**, jadi tidak ada
  jalur kebocoran baru; nilainya diambil lewat `settings get` yang wajib diredaksi (bagian 4.7).
- Alasan tidak menyembunyikan (keputusan sadar, ditulis agar tidak “diperbaiki” jadi penyembunyian):
  1. proyek open source — nama tool/command memang publik;
  2. sudah bocor lewat pesan error `execute`;
  3. menyembunyikan menghalangi soul membantu pengguna menaikkan izin.
- **Aktivasi**: hasil `info` memuat skema detail dan **mengaktifkan** tool itu untuk sisa turn. Jadi
  alurnya satu turn: *minta → `info` → keterima → panggil tool → kirim sekali*.

### 4.4 Aktivasi di loop LLM

- **Perubahan yang diusulkan:** `llm.py` menghitung `tools`/`by_name` **per putaran** dari
  `base_specs + activated`; `kwargs["tools"]` ikut diperbarui, `execute` memakai peta yang sudah
  diperluas. (Kode hari ini membangun keduanya **sekali** sebelum loop, `llm.py:163-164`.)
- Aktivasi hanya **menambah**; tidak pernah mengurangi atau mengubah izin.
- Guard/safety tetap dievaluasi **per panggilan** (`execute`); aktivasi tidak melewati apa pun.
- Penjaga “repeated call”, `run_budget_seconds`, dan jalur konfirmasi “sudah dikirim” (`ctx.sent`)
  tetap berlaku — `info` tidak boleh menambah pesan kedua ke pengguna.
- Aktivasi dicatat di ledger (tool apa yang disurfacekan pada turn itu) agar bisa diaudit.

### 4.5 Batas disclosure

| Terbuka | Tertutup |
|---|---|
| Nama tool/command dan status izin untuk penanya | `base_url` provider, path config host, label DB rinci, `lease host pid` |
| Deskripsi, `usage`, skema parameter | Isi ruangan lain, memori orang lain |
| Status kapabilitas (“automation: belum aktif”) | Peran orang lain |

Fakta infrastruktur hanya muncul di konteks DM/owner; di ruangan lain diganti label kasar
(`Brain: <model>`, `Surface: whatsapp`, `Mode: primary`).

### 4.6 Kejujuran kapabilitas

- Soul menyebut kapabilitas yang **ada**, walau penanya belum berhak, dan menyebut langkah
  perbaikannya (role/izin) — bukan berpura-pura tidak punya.
- Soul **tidak** menjanjikan yang belum ada: kalau automation belum aktif di instance itu, ia bilang
  belum aktif, bukan “nanti aku ingatkan”.
- Kalau ditanya soal cara kerjanya, ia menjawab dengan **perilaku** (apa yang ia ingat, kapan minta
  izin, kenapa tidak bisa membahas ruangan lain), bukan dengan detail infrastruktur (bagian 4.5).

### 4.7 Membaca konfigurasi: lewat `settings`, bukan `shell`/`read`

Cacat yang ditutup: hari ini soul cenderung membaca konfigurasi dengan `shell`/`read`, lalu memakai
`settings` hanya saat mengubah. Padahal:

- Baca lewat `settings` **tidak butuh approval** (`get`/`schema`/`reload`,
  `tools/settings.py:294-297`), jadi tidak ada alasan memakai shell.
- Menulis kunci sensitif sudah diblokir (`SECRET` → `ValueError`, `:346-347`), tetapi **menampilkan
  nilai sensitif belum diredaksi**: `action=get` mengembalikan `str(value)` apa adanya dan seluruh
  berkas mentah bila `path` kosong (`:371-385`), sedangkan `redact_dsn` hanya dipakai di jalur tulis
  (`:395-396`, `:436`).

Aturan:

| Aturan | Isi |
|---|---|
| Routing | Konfigurasi dibaca lewat `settings` (`schema` untuk struktur, `get` untuk nilai), **bukan** `shell`/`read` |
| Redaksi jalur baca | Kunci yang cocok `SECRET` → `***`; `database.url` → `redact_dsn`; URL dengan userinfo dibuang; `get` tanpa `path` tidak pernah mengembalikan berkas mentah |
| `.env` | Dijaga di jalur baca konfigurasi: `settings` tidak mengembalikan isinya dan `read` menolaknya. `shell` tetap bisa membacanya **hanya** lewat approval — untuk menutup jalur diam-diam, guard menambah pola `.env`/`printenv`/`/proc/*/environ` sebagai sensitive command. `info` hanya menyebut **nama** env, bukan nilainya |
| `read` | Menolak berkas konfigurasi host dan mengarahkan ke `settings` (deterministik, menutup jalur `cat config.yaml`) |
| Index | Menandai eksplisit: `settings`: baca gratis (`schema`/`get`), tulis butuh `/approve` |
| Audit | Baca tidak butuh approval, tetapi perubahan tetap lewat audit seperti sekarang |

Prinsipnya sama dengan 4.5: **struktur dan kapabilitas terbuka, nilai rahasia dan infrastruktur
tertutup.**

### 4.8 Pengetahuan diri: index topik, generated-first, default nyala

Soul harus bisa menjawab pertanyaan tentang dirinya dengan benar **tanpa** menaruh prosa panjang di
setiap payload, dan tanpa bisa basi saat arsitektur berubah.

**Index (tetap pendek, ada di Lapis 2):**

```
- kemampuan & izin (tool/command + status peran)   -> info(kind="catalog")
- cara kerjaku (ruangan, ingatan, izin, inisiatif, gagal, batas) -> info(kind="self", name=...)
- ambang yang berlaku (masa simpan, batas harian, mode izin)      -> info(kind="config")
- dokumentasi produk (README: apa ini, arsitektur, peran, kredit) -> info(kind="doc", name="README")
```

**Topik `self` dan sumbernya:**

| Topik | Isi | Sumber |
|---|---|---|
| `ruangan` | satu soul banyak ruangan; hanya ruangan penanya; yang lahir di satu ruangan tetap di sana | `behavior:rooms` |
| `ingatan` | apa yang diingat; yang memudar vs bertahan; tugas selesai ikut masa simpan; kredensial tidak disimpan; koreksi mengganti tapi menyimpan yang lama; kontradiksi ditandai | `behavior:memory` + bucket dari config |
| `izin` | peran menentukan; baca pengaturan gratis; ubah butuh `/approve`; approval tidak menambah izin | `behavior:permissions` + `approval.mode` |
| `inisiatif` | tidak menyapa lebih dulu; pengingat/aturan milik penanya, hanya di ruangannya | `behavior:initiative` + flag automation |
| `gagal` | tidak klaim tanpa bukti; tidak menjanjikan diam; tugas terlewat disebut + minta maaf + tawaran | `behavior:failure` |
| `batas` | yang boleh diceritakan vs tidak (penyedia/path/DB/isi ruangan lain/urusan orang lain/ambang internal) | `behavior:limits` |

**Empat generator (semuanya tanpa LLM):**

| Generator | Sumber tunggal | Catatan |
|---|---|---|
| `catalog` | `registry.discover` + `Handler.parameters` | skema sudah berbentuk JSON |
| `config_schema` | `_schema_paths` atas dataclass (`tools/settings.py:85-98`) | dot-path + default, tanpa nilai |
| `behavior` | satu modul `engine/self_knowledge.py` | satu-satunya prosa; tiap entri ber-anotasi `behavior:<id>` |
| `topology` | `ast.parse` atas `eidesoul/engine/**.py` | peta dependensi; data yang sama dipakai test arsitektur AGENTS (`engine` tidak mengimpor `gateways`) |

**Aturan supaya tidak basi:**

1. **Generated-first**: fakta terstruktur (schema, registry, flag config, grafik import) tidak pernah
   diketik ulang manusia.
2. **Tanpa angka hardcoded**: masa simpan dan batas dinyatakan lewat **ember kasar** yang dihitung
   dari config (`hitungan minggu` / `berbulan-bulan` / `> setahun`). Angka pasti hanya dari operator.
3. **Docstring bukan sumber kebenaran** — hanya fakta terstruktur yang dipercaya.
4. **Cache di boot**: hasil generator dihitung sekali dan di-cache; `info` hanya lookup string.
   Batas `max_chars` per topik.

**Penegakan (bukan disiplin)** — *menyusul bersama implementasi 0015; sampai `scripts/selfcheck.py` ada,
butir di bawah ini belum ditegakkan mesin*: `scripts/selfcheck.py --check` + `eidesoul selftest`:

- setiap `behavior:<id>` terdaftar; hapus/rename perilaku tanpa memperbarui daftar → **gagal**;
- setiap `config:`/`role:`/`registry` resolve ke kunci yang benar-benar ada;
- **daftar token terlarang** tidak pernah muncul di output topik mana pun: path host, `base_url`, DSN,
  nama env, id model, ambang internal, nama tabel;
- setiap topik non-kosong dan di bawah `max_chars`;
- `topology` konsisten dengan aturan AGENTS.

**Pinned tetap terpisah.** Blok Lapis 1 (silence lintas ruangan + tanpa oracle, tidak klaim tanpa bukti
tool, tidak mengarang ingatan/angka, kredensial tidak disimpan, arti approval, kejujuran AI) **selalu
ada**, terlepas dari fitur ini. Termasuk satu baris: *“kalau ditanya soal caraku/kemampuanku, panggil
index dulu, jangan menebak.”*

**Default nyala, bisa dimatikan.** `engine.self_knowledge.enabled: true` secara default. Saat
dimatikan:

- index kapabilitas, topik `self`, jenis `config`, dan `topology` tidak disuntik/dihasilkan;
- `info` mengembalikan sinyal jelas bahwa penjelasan diri sedang **dinonaktifkan** (data, bukan error);
- soul **tidak menebak**: ia menyatakan penjelasan diri nonaktif dan mengarahkan ke operator;
- blok Lapis 1 tetap berlaku, jadi aturan jujur/privasi tidak ikut mati.

### 4.9 Dokumen produk: index subjudul, baca seksi

README sudah menjelaskan produk (apa ini, arsitektur, peran, kredit). Karena itu **tidak ada topik
`stack` yang ditulis tangan** — dokumennya sendiri yang jadi sumber tunggal. Yang ditambahkan hanya
cara membacanya:

```
info(kind="doc", name="README")                        → daftar subjudul (slug) + petunjuk satu baris
info(kind="doc", name="README", section="credits")      → isi seksi itu saja
```

**Kenapa bukan `read`:** README ada di direktori proyek, bukan di workspace, jadi `read` tidak
menjangkaunya. `workspace_roots` menunjuk ke bawah `workspace` saja — `public/` + `scratch/<chat>` untuk
peran biasa, dan seluruh workspace untuk peran trusted; tidak satu pun menunjuk direktori proyek. Membacanya harus lewat izin
eksplisit, bukan kebetulan.

**Aturan dinamika (tidak ada daftar slug yang disimpan):**

| Aturan | Isi |
|---|---|
| Normalisasi | **Kompatibel GitHub**: lowercase; buang format inline (`` `code` ``, **bold**, `[link](url)`); **buang tanda baca lain** (termasuk titik, jadi `soul.yaml` → `soulyaml`) — bukan menggantinya dengan `-`; spasi → `-`; buang `-` tepi |
| Duplikat | heading kembar dapat `-2`, `-3` |
| Ambigu | resolusi: slug persis → prefix → fuzzy kecil; bila tetap ambigu, hasilnya **daftar heading** sehingga soul memilih ulang di turn yang sama |
| Isi seksi | dari heading itu sampai heading berikutnya pada level sama atau lebih tinggi; subseksi ikut |
| Kode dalam fence | heading di dalam blok kode berpagar diabaikan — dokumen kita memuat contoh blok berisi `## ...` |
| Cache | index dihitung dari `(path, mtime, size)`; berubah berkas → index baru; isi seksi dibaca saat dipanggil |
| Gagal baca | sinyal data yang jelas (“dokumen tidak tersedia”), bukan `error:` internal |
| Bahasa | `README.md` vs `README.id.md` dipilih dari `soul.language`; slug dihitung per berkas, jadi judul Indonesian bekerja tanpa pemetaan khusus |
| Nama | `name` adalah **path relatif akar proyek** (mis. `README.md`, `docs/v0.1.0/README.md`); tidak ada resolusi nama bare, jadi dua berkas bernama README tidak pernah tertukar |
| Batas | index heading saja; isi seksi dibatasi `max_chars`; dilarang menempel berkas mentah |
| Sifat | hasilnya data, bukan instruksi (karantina ADR-0008); check token terlarang tetap dijalankan |

**Allowlist default:** `docs: ["README.md"]` — entri adalah **path relatif dari akar proyek**, bukan
nama bare, supaya `README.md` produk dan `docs/v0.1.0/README.md` (indeks ADR) bisa dibedakan. `LOG.md`, `TASK.md`, dan `AGENTS.md` **tidak** ikut default —
LOG bisa memuat catatan insiden, TASK daftar kerja internal, AGENTS aturan perubahan kode. Operator
boleh menambahkannya secara sadar.

**Diverifikasi terhadap berkas nyata (2026-11-02):** `README.md` punya 13 heading (1×H1 + 12×H2),
tanpa duplikat, dan **13/13 anchor yang dipakai dokumen itu sendiri cocok** dengan slug kompatibel
GitHub — termasuk `[soul.yaml](#soulyaml)`. Ukuran: 421 baris / 21,5 KB; seksi terbesar `soul.yaml`
3,4 KB, `Credits` 2,9 KB, `Safety` 2,5 KB — `docs_max_chars: 4000` menutup semuanya. Bila kelak ada
seksi melebihi cap: potong dengan penanda jumlah baris tersisa, bukan dipotong diam-diam. `H1`
diperlakukan sebagai **judul dokumen**, bukan seksi.

Catatan dokumen (bukan masalah indeks): `README.id.md` masih menautkan anchor Inggris
(`#architecture`, `#quickstart`, `#credits`, …) sementara headingnya Indonesian, sehingga tujuh tautan
navigasinya mati. Indeks kita tidak terpengaruh karena slug dihitung dari heading.

**Aturan penentu** (melengkapi 4.5): **fakta produk yang sudah ada di README boleh disebut; keadaan
runtime milik operator tetap tertutup** (alamat/penyedia model, path host, nama env, DSN, host/pid
lease, id model persis, ambang internal). Menjelaskan produk ≠ membocorkan prompt/konfigurasi runtime.

**Uji wajib** (fixture kecil): heading kembar · heading beremoji · heading berisi link · heading di
dalam fence · heading bersarang 1–3 level · berkas hilang — assert slug, dedupe, penolakan heading
dalam fence, resolusi sampai batas level, dan sinyal saat berkas tidak ada.




## 5. Konfigurasi

```yaml
engine:
  prompt:
    pinned_blocks: [base, identity, honesty, privacy, cross_room]
    capability_index: true
    capability_index_max_chars: 1200
    capability_index_two_level_at: 150   # di atas ini, index menyebut famili (bukan nama); §4.2.2
    salience: true                 # hanya blok referensi yang ikut anggaran
    thread_chars: 4000             # kutipan thread
    tail_max_messages: 20          # jendela replay percakapan (sebelumnya hardcoded di assembly.py)
    tail_min_messages: 6           # minimum replay walau anggaran karakter sempit
    tail_chars: 1200               # anggaran karakter replay sebelum memakai minimum
  tools:
    hot: [read, chat_history, memory, info]   # skema selalu dikirim
    activate_on_demand: true                  # sisanya lewat info
  self_block:
    redact_infra: non_administrator        # non_administrator | never | always
  self_knowledge:
    enabled: true                  # default nyala; mematikannya tidak mematikan blok Lapis 1
    capability_index: true
    topics: [ruangan, ingatan, izin, inisiatif, gagal, batas]
    coarse_buckets: true           # masa simpan/batas dinyatakan kasar, bukan angka hardcoded
    topology: true                 # peta dependensi (dipakai juga test arsitektur AGENTS)
    max_chars_per_topic: 1200
    forbidden_token_check: true
    docs: ["README.md"]            # allowlist path relatif akar proyek; README.id mengikuti bahasa soul
    docs_max_chars: 4000           # batas per seksi yang dibaca
```

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Karakter prompt p50/p95 per turn | Turun dibanding status quo; dipantau per peran |
| `capability_index_chars` p95 | ≤ `capability_index_max_chars` (1200) di kedua mode |
| `capability_index_locked_lines` | ≤ jumlah famili (aturan §4.2.1) |
| `capability_index_level` | 1 (≤ ambang) atau 2 (famili); pergeseran hanya saat jumlah kapabilitas berubah |
| Panggilan `info` per turn setelah mode dua tingkat | naik wajar; lonjakan besar berarti index famili kurang informatif |
| Panggilan `info` per turn | Terukur; lonjakan berarti index kurang informatif |
| Aktivasi tool di tengah turn | Berfungsi; nol turn tambahan untuk kapabilitas yang sudah diindex |
| Blok pinned yang hilang | 0 (diuji dengan anggaran kecil) |
| Klaim kapabilitas palsu (eval probe) | 0 |
| Kebocoran kredensial lewat jalur baca konfigurasi | 0 (diuji: DSN, kunci `SECRET`, userinfo proxy) |
| `settings get` tanpa `path` yang mengembalikan berkas mentah | 0 (diuji) |
| `behavior:<id>` yang tidak terdaftar di check script | 0 (gagal check) |
| Token terlarang di output topik | 0 (diuji) |
| Probe pengetahuan diri (jawaban benar tanpa bocoran) | 100% |
| Resolusi slug dokumen (pertama kali cocok) | ≥ 95%; sisanya self-correct dari daftar heading |
| Heading di dalam fence yang ikut terindeks | 0 (diuji) |
| Token terlarang di keluaran seksi dokumen | 0 (diuji) |
| Kebocoran fakta infrastruktur di ruangan non-administrator | 0 (diuji) |

## 7. Acceptance criteria

- [ ] Blok pinned selalu hadir; blok referensi saja yang bersaing di `salience`.
- [ ] Index kapabilitas memuat **semua** nama tool dan command yang dimiliki engine, dengan penanda `allowed`/`locked` + alasan untuk peran pengirim.
- [ ] Katalog tidak disembunyikan; `info` memberi detail dan skema walau penanya belum berhak.
- [ ] `web_search` tetap `allowed` tanpa kunci API (backend `ddgs`); index tidak pernah menandainya `locked` karena kunci.
- [ ] `info` tidak memanggil LLM dan hasilnya diperlakukan sebagai data, bukan instruksi.
- [ ] `info` mengaktifkan tool untuk sisa turn; `llm.py` menghitung `tools`/`by_name` per putaran.
- [ ] Aktivasi hanya menambah; guard/safety dan permission tetap dievaluasi per panggilan di `execute`.
- [ ] `info` tidak menambah pesan kedua ke pengguna (jalur `ctx.sent` tetap).
- [ ] Fakta infrastruktur diredaksi di ruangan non-administrator; DM/owner tetap melihat detail.
- [ ] Index disusun deterministik (urut nama, stabil per peran) sehingga prefix prompt stabil.
- [ ] `info(kind="config")` mengembalikan skema konfigurasi (dot-path, file, default) tanpa nilai.
- [ ] `settings get` meredaksi kunci `SECRET`, `database.url`, dan userinfo URL; tanpa `path` ia tidak mengembalikan berkas mentah.
- [ ] `read` menolak berkas konfigurasi host dan mengarahkan ke `settings`; `.env` tidak terbaca lewat `read`/`settings`, dan guard menandai `.env`/`printenv`/`/proc/*/environ` sebagai sensitive command (approval).
- [ ] Index kapabilitas menyebut `settings`: baca gratis, tulis butuh `/approve`.
- [ ] `info` pengetahuan diri tanpa LLM; hasil dibangun dari generator (`catalog`, `config_schema`, `behavior`, `topology`) dan di-cache saat boot.
- [ ] Masa simpan dan batas dinyatakan lewat ember kasar dari config; tidak ada angka hardcoded di prosa.
- [ ] Setiap `behavior:<id>` terdaftar; `scripts/selfcheck.py --check` + selftest gagal bila ada yang hilang atau token terlarang muncul.
- [ ] `topology` konsisten dengan aturan AGENTS (engine tidak mengimpor gateways).
- [ ] `engine.self_knowledge.enabled: true` secara default; saat dimatikan, index/topik/`config`/`topology` tidak dihasilkan, `info` memberi sinyal nonaktif, dan soul tidak menebak.
- [ ] Mematikan fitur **tidak** mematikan blok Lapis 1 (aturan jujur/privasi/silence tetap ada).
- [ ] `info(kind="doc", name="README")` mengembalikan daftar subjudul yang dihitung dari berkas saat itu (bukan daftar tersimpan); berubah heading → slug ikut berubah.
- [ ] Slug kompatibel GitHub: tanda baca dibuang (bukan diganti `-`), sehingga `section="soulyaml"` dan anchor yang dipakai README sendiri cocok.
- [ ] Seksi yang melebihi `docs_max_chars` dipotong dengan penanda jumlah baris tersisa, bukan dipotong diam-diam; `H1` diperlakukan sebagai judul dokumen.
- [ ] Isi seksi berhenti di heading berikutnya pada level sama/lebih tinggi; heading di dalam fence diabaikan.
- [ ] Berkas per bahasa dipilih dari `soul.language`; berkas hilang menghasilkan sinyal data yang jelas.
- [ ] Allowlist default `[README]`; `LOG.md`/`TASK.md`/`AGENTS.md` tidak ikut tanpa pilihan operator.
- [ ] Keluaran dokumen dibatasi `docs_max_chars` dan tidak pernah menempel berkas mentah; hasilnya data, bukan instruksi.
- [ ] Check token terlarang juga dijalankan pada keluaran dokumen.
- [ ] Soul menyebut kapabilitas terkunci beserta langkah perbaikan; tidak menjanjikan kapabilitas yang belum ada.
- [ ] Dua nama terkunci di famili yang sama menghasilkan **satu** baris (jumlah baris terkunci ≤ jumlah famili).
- [ ] Fixture 151 kapabilitas → index beralih ke mode dua tingkat (famili, bukan 151 nama); urutannya deterministik dan hash blok sama pada dua penyusunan berturut-turut.
- [ ] `capability_index_max_chars` tetap ditegakkan di kedua mode; nilai negatif untuk `capability_index_two_level_at` ditolak saat boot.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Model tidak tahu harus expand | Index wajib menyebut semua nama + aturan “butuh detail → panggil info” |
| Turn tambahan untuk hal yang selalu dibutuhkan | Invarian ada di Lapis 1, bukan di balik pointer |
| `salience` menjatuhkan blok kritis | Blok pinned dilewatkan seleksi + test anggaran kecil |
| Cache prompt provider pecah | Index deterministik dan stabil per peran |
| Prompt membengkak karena index | Batas `capability_index_max_chars` + metrik karakter |
| `info` dipakai untuk enumerasi | Memang diizinkan (open source) dan tercatat; gerbang izin tetap di `execute` |
| Aktivasi dipakai untuk menaikkan izin | Aktivasi tidak pernah mengubah izin; hanya menambah skema |
| Data bocor lewat `info` | `info` hanya kapabilitas dan skema; nilai konfigurasi tidak pernah masuk hasilnya |
| Kredensial bocor lewat jalur baca konfigurasi | Redaksi di jalur baca (`SECRET`/`redact_dsn`/userinfo) + `get` tanpa `path` ditolak + `read` menolak berkas konfigurasi |
| Prosa perilaku basi saat arsitektur berubah | Satu modul `self_knowledge.py` + anotasi `behavior:<id>` + check yang gagal bila daftar tidak sinkron |
| Angka di prosa berbeda dari config | Tanpa angka hardcoded; ember kasar dihitung dari config |
| Model menjawab tanpa memanggil index | Baris Lapis 1: “ditanya soal caraku → panggil index dulu, jangan menebak” + probe eval |
| Fitur dimatikan lalu soul menebak | Saat nonaktif `info` memberi sinyal jelas, dan Lapis 1 menuntut “tidak menebak” |
| Generator membocorkan internal | Daftar token terlarang diuji di check script |
| Heading dokumen berubah bentuk (emoji/link/nested) | Slug dihitung saat itu, bukan disimpan; uji fixture mencakup emoji, link, kembar, nested, dan fence |
| Dokumen kerja ikut terbaca (LOG/TASK/AGENTS) | Allowlist default hanya `[README]`; sisanya pilihan sadar operator |
| README memuat contoh yang sensitif | Check token terlarang juga dijalankan pada keluaran dokumen |

## 9. Interaksi dengan ADR lain

- **ADR-0006** — redaksi dan batas disclosure mengikuti ceiling ruangan; `info` tidak pernah menyentuh data memori.
- **ADR-0007** — index menampilkan izin per penanya; satu gerbang izin tetap `execute`, dan aktivasi bukan pemberi izin.
- **ADR-0008** — hasil `info` adalah data dan tunduk pada aturan karantina; kapabilitas tidak menaikkan trust apa pun.
- **ADR-0013** — aktivasi terjadi di dalam satu turn yang sama; tidak ada turn kedua.
- **ADR-0014** — status automation (“aktif/belum”) muncul di index, jadi soul tidak menjanjikannya keliru.
- **AGENTS.md** — aturan bentuk repo (mis. `engine` tidak mengimpor `gateways`) **akan** diuji dari data `topology`, bukan dari prosa yang ditulis ulang. Test itu belum ada di `tests/`; pembuatannya bagian dari acceptance §7.

## 10. Referensi kode

- `eidesoul/engine/self_knowledge.py` — **modul baru**: empat generator (`catalog`, `config_schema`, `behavior`, `topology`) dan prosa `behavior:<id>`.
- `scripts/selfcheck.py` — **skrip baru**: verifikasi `behavior:<id>`, resolusi `config:`/`role:`/`registry`, daftar token terlarang, dan konsistensi `topology`.
- `eidesoul/engine/registry.py:148` — `discover` (sumber tunggal katalog tool/command).
- `eidesoul/engine/tools/settings.py:85-98` — `_schema_paths` (sumber tunggal skema config); `:294-297` — baca gratis tanpa approval.
- `scripts/affect.py`, `scripts/patterns.py` — preseden pola “data + skrip penjaga” (`--check`).
- `README.md`, `README.id.md` — sumber dokumen produk (indeks subjudul dihitung dari berkas ini).
- `eidesoul/engine/tools/read.py:28-30` + `tools/__init__.py:120-165` — batas `workspace_roots` (alasan `read` tidak bisa menjangkau direktori proyek).
- `eidesoul/engine/prompts.py:150-186` — `self_block` (tempat redaksi infra).
- `eidesoul/engine/prompts.py:190-386` — `system_prompt`, daftar blok, `salience.select`, `language_line`, `Date`.
- `eidesoul/engine/salience.py:38-60` — `JOIN`, `block_score`, `select` (tempat blok pinned dilewatkan).
- `eidesoul/engine/llm.py:150-260` — loop tool: `tools`/`by_name`, `kwargs["tools"]`, `execute`, budget, guard “repeated call”.
- `eidesoul/engine/tools/__init__.py:180-235` — `execute` (satu gerbang izin) dan pesan error yang sudah menyebut nama tool.
- `eidesoul/engine/permissions.py:56` + `eidesoul/engine/commands/help.py` — `commands_for` (sumber daftar command).
- `eidesoul/engine/registry.py` — `Handler` (`name`, `description`, `usage`, `parameters`) sebagai sumber detail `info`.
- `eidesoul/engine/context.py:641-683` + `FOOTER` (`:55-58`) — format user content yang tidak berubah.
- `eidesoul/engine/agent.py:279-283,759-788` — `_specs(roles)` dan perakitan `system_prompt`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0015-capability-manifest.md`.*
