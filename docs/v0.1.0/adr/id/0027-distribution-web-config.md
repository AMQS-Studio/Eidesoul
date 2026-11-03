> Terjemahan turunan — jangan sunting; sumber: 0027-distribution-web-config.md (sha256: b95dbab18209)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0027: Distribusi dan Konfigurasi Web-First — Satu Image Core, Kapabilitas Clean by Default

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0001 (permukaan dan body), ADR-0015 (manifest kapabilitas), ADR-0016 (skema), ADR-0018 (kontrak gateway), ADR-0022 (`administrator`), ADR-0028 (plugin)
- **Supersedes:** —
- **Terkait:** ADR-0009 (rumah dokumen), ADR-0012 (lease), ADR-0019 (observability), ADR-0028 (unit plugin yang didistribusikan ADR ini)
- **Implementasi:** partial — ada: build image lokal (core-only, `EXTRAS` kosong), pemisahan plugin/gateway, isi image di `eidesoul selftest`, gate panel-atau-CLI di installer; sisa: `panel/` (milik operator; `web.enabled: false` sampai mendarat) dan publikasi image ke GHCR (tidak ada kredensial registry di sini)
- **Catatan lokasi:** ADR ini lintas-potong (mengikat setiap permukaan dan setiap deployment), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0027-distribution-web-config.md`.
- **Amandemen:** 2026-11-03 — versi pertama. 2026-11-03 — lapisan suara (emosi, humanize, karakter) dinyatakan aktif secara default dan bisa dimatikan per bagian; clean by default tetap hanya mencakup gateway, tool, dan plugin.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| Image dibangun tapi belum dipublikasikan | `Dockerfile:1,28-35` membangun dari `python:3.12-slim` dengan `ARG EXTRAS=""`; tidak ada `.github/workflows` |
| Kapabilitas opsional hari ini ikut dikompilasi ke image | extra `pyproject.toml`: `whatsapp`, `google`; `Dockerfile:28` mendokumentasikan `--extra google` |
| Sebagian kapabilitas tinggal di core seperti first-class | `eidesoul/engine/tools/google.py`, `chat_history.py` |
| Semua tool dan command sudah deny-by-default | role membawa `tools`/`commands`; entri yang hilang menolak (aturan repo) |
| Belum ada permukaan operator untuk konfigurasi | konfigurasi lewat `eidesoul init`, `eidesoul config import`, cermin `roles.yaml`/`soul.yaml`, dan `.env` |
| Spine tidak opsional | Postgres+pgvector, Mem0, embedder bersama, prototipe affect, guard, dan lease (`AGENTS.md` §Capability spine) |

Keputusan pemilik: distribusi lewat `ghcr.io`, permukaan web jadi jalur utama init dan konfigurasi,
komponen dipasang dari situ, dan **tidak ada yang aktif secara default kecuali spine** — kode komponen
baru diunduh saat diaktifkan. Permukaan web sendiri direncanakan aktif secara default begitu selesai,
dengan saklar untuk mematikannya.

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Clean by default | Gateway, tool, dan plugin masing-masing mati sampai diaktifkan — install baru menjalankan spine dan tidak ada yang bisa bertindak ke dunia |
| Kategori berbeda | Gateway adalah body, tool adalah kapabilitas, plugin adalah bundel; tidak ada kategori yang menelan yang lain |
| Kode mengikuti pengaktifan | Image tetap kecil dan bisa diaudit; komponen yang aktif diunduh, dipin digest |
| Satu kebenaran konfigurasi | Permukaan web menulis dokumen yang sama dengan CLI; tanpa penyimpanan kedua |
| Permukaan untuk operator | Init, inspeksi, aktifkan, beri izin, cabut izin — tanpa menyunting YAML dengan tangan |
| Bisa dibalik | Setiap pengaktifan punya penonaktifan; mematikan permukaan web tidak mematahkan engine |
| Status jujur | Image yang belum dirilis tidak disajikan sebagai rilis; permukaan yang belum selesai tidak diaktifkan |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Satu image core, unduh saat aktif** | Publikasikan satu image berisi spine saja; mengaktifkan komponen mengunduh kodenya | **Dipilih** |
| **B. Image gemuk per bundel** | `core`, `core-google`, `core-whatsapp`, … | Ditolak sebagai default: matriks build/verifikasi membesar dan "tidak ada yang default" jadi tidak benar |
| **C. Hanya extra pip** | Pertahankan `--extra google`, konfigurasi lewat YAML | Ditolak: pengaktifan jadi keputusan build-time, bukan aksi operator |
| **D. Tanpa image, install dari sumber** | Dokumentasikan `uv sync` + unit systemd | Ditolak: reproduksibilitas dan upgrade jadi lebih buruk |
| **E. Engine jadi server HTTP** | Lipat konfigurasi dan API ke proses engine | Ditolak: engine tetap library plus gateway; panel adalah proses terpisah (§4.2) |

## 4. Keputusan

### 4.1 Artefak yang dipublikasikan

Satu image per rilis, dipublikasikan ke `ghcr.io/<owner>/<repo>`, dibangun dari `Dockerfile` yang ada
dengan `EXTRAS` kosong. Selama belum ada rilis, image hanya dibangun lokal dan tag berbasis `git` —
tidak ada tag yang menyerupai rilis (§Versioning di `AGENTS.md`).

- Image memuat **spine dan tool core saja**: `read`, `write`, `shell`, `sandbox`, `web_search`, `mcp`,
  `memory`, `recipe`, `skill`, `settings`, plus command core.
- Image **tidak memuat gateway, extra provider, maupun plugin**; semuanya diaktifkan kemudian (§4.3)
  dan dependency Python-nya bukan bagian dari set core yang di-lock.
- Image menyatakan isinya di satu label build dan mencetaknya di `eidesoul selftest`, sehingga apa yang
  bisa dilakukan container yang berjalan bisa diperiksa, bukan diasumsikan.

### 4.2 Permukaan web adalah jalur konfigurasi utama

Panel web adalah permukaan operator untuk init dan konfigurasi: jalan pertama (database, workspace,
provider dan model, administrator pertama), inspeksi (data `/status`, ledger, automation, kebenaran
memori), dan pengaktifan komponen (§4.3). CLI tetap setara untuk install headless (`eidesoul init`,
`eidesoul config import`, `eidesoul selftest`, `eidesoul models`).

- Panel adalah **proses terpisah** di akar repo (`panel/`), bukan modul di dalam `eidesoul/engine/`,
  dan engine tidak pernah mengimpornya. `AGENTS.md` §Shape diamandemen: larangan gateway HTTP tetap
  berlaku untuk **engine**, sedangkan panel adalah aplikasi operator yang membaca dan menulis dokumen
  yang sama.
- Konfigurasi yang ditulis panel menempuh jalur yang sama dengan suntingan manual lalu
  `eidesoul config import`: dokumen adalah sumber kebenaran, berkas YAML adalah seed + cermin, rahasia
  tetap di `.env` (panel menulis **nama** env, tidak pernah nilai rahasia ke YAML).
- Setiap perubahan dicatat di `config_changes` dengan aktor dan diff, dan panel menampilkan nilai
  efektif di samping sumbernya (`document`, `yaml mirror`, `env`, `default`).
- Panel tidak memiliki kebijakan apa pun: izin, approval, dan guard hidup di engine; panel tidak bisa
  memberi izin kepada dirinya sendiri, dan pengaktifan komponen adalah aksi operator (§4.3).

### 4.3 Tidak ada yang aktif secara default kecuali spine

**Clean by default adalah filosofi bersama, bukan kategori bersama.** `gateway`, `tool`, dan `plugin`
tetap tiga hal berbeda: gateway adalah body (transport), tool adalah kapabilitas yang dipanggil engine,
plugin adalah bundel terinstall (ADR-0028). Tidak ada yang menjadi yang lain, dan masing-masing
dipasang serta diaktifkan atas namanya sendiri — mengaktifkan gateway bukan "memasang plugin", dan
plugin tidak pernah berubah menjadi tool core.

| Layer | Kategori | Default | Cara menyalakan |
|---|---|---|---|
| Spine (Postgres+pgvector, Mem0, embedder, prototipe affect, guard, lease) | bukan kategori — wajib | **on** | tidak opsional |
| Lapisan suara (emosi, humanize, karakter) | engine | **aktif secara default** | `init` bertanya sekali, dengan jawaban default tetap nyala; tiap bagian satu sakelar, dan mematikannya adalah operator action |
| Tool core (`read`, `write`, `shell`, `sandbox`, `web_search`, `mcp`, `memory`, `recipe`, `skill`, `settings`) | tool | kode ada di image, **tanpa izin** | pemetaan `tools` di role (deny by default) |
| Paket tool tambahan (kapabilitas di luar image) | tool | **mati, belum dipasang** | pasang paket tool, lalu beri izin lewat role |
| Gateway (WhatsApp, robot/harness/editor nanti) | gateway | **mati, belum dipasang** | pasang gateway, lalu aktifkan — kodenya diunduh |
| Plugin (mis. `google`) | plugin | **mati, belum dipasang** | aktifkan, lalu kodenya diunduh (ADR-0028) |
| Panel web | permukaan operator | **on begitu selesai** (§4.4) | — |
| Automation | per orang | mati | ditulis eksplisit (ADR-0014) |

- **Lapisan suara** bukan kategori kapabilitas: emosi, humanize, dan karakter aktif secara default, dan
  masing-masing satu sakelar. Mematikan satu bagian mengubah suara, tidak pernah identitas, memori, atau
  izin.
- "Aktif" berarti dua hal terpisah dan keduanya wajib: kodenya ada **dan** ada role yang memberi
  kapabilitasnya. Aktif tanpa izin membuatnya inert; izin tanpa kode dilaporkan `unavailable`, bukan
  `denied`.
- Mengaktifkan komponen **mengunduh kodenya** (wheel atau layer OCI) yang dipin dengan digest,
  memverifikasi digest, baru menulis dokumen konfigurasi. Tanpa jaringan, pengaktifan gagal dengan satu
  pesan eksplisit dan tidak mengubah apa pun — tanpa salinan cadangan di dalam paket dan tanpa install
  separuh jalan.
- Menonaktifkan mematikan kapabilitas dan menyimpan berkas (cepat, bisa dibalik); menghapus membuang
  berkas dan statusnya. Keduanya aksi operator dan keduanya masuk ledger.

### 4.4 Permukaan web aktif secara default begitu selesai

Begitu panel selesai, ia dikirim dalam keadaan aktif: `web.enabled: true`, terikat ke loopback secara
default pada port pilihan operator, tanpa akses anonim (token administrator dibuat pada jalan pertama).
Mematikannya satu saklar (`web.enabled: false`) dan tidak pernah mematahkan engine yang berjalan —
engine tetap melayani gateway-nya; hanya panel yang berhenti. Selama panel belum selesai, saklarnya ada
tetapi bernilai `false` dan panel tidak dikemas ke image.

Bind di luar loopback menuntut host eksplisit, pernyataan listener eksplisit di log, dan rahasia di
`.env`; panel tidak pernah menyalakan akses jarak jauh atas kemauannya sendiri.

### 4.5 Apa yang tidak pernah dilakukan panel

- Tanpa sumber kebenaran kedua: ia menulis dokumen lewat jalur import yang sama, bukan penyimpanan paralel.
- Tanpa logika engine: tanpa penyusunan prompt, tanpa keputusan memori, tanpa melewati guard; ia memanggil
  entry point milik engine sendiri.
- Tanpa string yang dilihat pengguna di dalam kode: teks panel datang dari katalog bahasa yang sama
  (§Language di `AGENTS.md`).
- Tanpa kapabilitas diam-diam: tidak ada yang diaktifkan, dipasang, atau di-upgrade tanpa aksi operator
  eksplisit yang tercatat di `config_changes` dan ledger.

### 4.6 Default, bukan contoh

Berkas yang kita kirim harus bisa dipakai apa adanya; berkas yang hanya mendemonstrasikan dihapus.
Defaultnya hidup di kode, `eidesoul init` satu-satunya penulis berkas host, dan panel menulis lewat
jalur yang sama (§4.2).

- `eidesoul/admin/templates.py` adalah satu-satunya sumber: `config.yaml`, `roles.yaml`, `soul.yaml`, dan
  `.env` dihasilkan dari situ, dengan `.env` dan `roles.yaml` bermode `0600` dan tidak pernah ditimpa
  kecuali `--force`. `.env` membawa **nama dengan nilai kosong** (`EIDESOL_DATABASE_URL=`,
  `POSTGRES_PASSWORD=`) tanpa rahasia placeholder, jadi berkas yang baru terisi separuh tidak bisa
  disangka berkas yang sudah jalan.
- Tidak ada berkas `*.example` yang dikirim. `config.example.yaml` dan `.env.example` menduplikasi
  `templates.py` dan sudah dihapus: kunci yang ditambahkan ke template kini sampai ke setiap install yang
  menjalankan ulang `init`, bukan melenceng dari salinan yang dipelihara tangan.
- Kunci opsional jatuh ke default kode saat tidak ada (`pool_size=5`, `pool_timeout=30`,
  `timeout_seconds=120`, `pids_limit=256`), jadi berkas hanya membawa apa yang operator putuskan.
- Nilai yang memang pilihan operator ditanyakan, tidak pernah diberi default: provider, model id, dan URL
  database. `init --provider` wajib, `init --model` opsional, dan tidak ada model id yang dipin di kode.
- `compose.yaml` juga default yang nyata: ia jalan apa adanya, mengambil `POSTGRES_PASSWORD` dari `.env`,
  membangun image core (`EXTRAS: ""`), dan menaruh proxy sandbox di balik profil `sandbox` pada jaringan
  egress mandiri, bukan menuntut jaringan eksternal.

## 5. Konfigurasi

| Kunci (bagian host) | Default | Arti |
|---|---|---|
| `web.enabled` | `true` begitu selesai, `false` selama belum | proses panel |
| `web.host` | `127.0.0.1` | alamat bind; selain itu butuh `web.remote_ack: true` dan rahasia `.env` |
| `web.port` | dipilih operator saat jalan pertama | listener panel |
| `components.root` | `<workspace>/components` | tempat kode gateway/plugin hasil unduhan (§ADR-0028) |
| `components.fetch` | `true` | boleh mengunduh kode saat aktif; `false` = hanya install offline |
| `image.tag` | kosong selama belum rilis | tag image yang berjalan, dilaporkan `eidesoul selftest` |

Rahasia hanya di `.env` (`WEB_TOKEN`, kunci provider); YAML memuat nama, bukan nilai.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Kapabilitas aktif pada install baru (di luar spine dan pemberian izin role) | 0 |
| Isi image bisa diperiksa | `eidesoul selftest` mencantumkan ada/tidaknya gateway/plugin; label cocok |
| Divergensi panel ↔ CLI pada perubahan yang sama | 0 (satu jalur import) |
| Komponen terunduh tanpa verifikasi digest | 0 (fail closed) |
| Perubahan tanpa baris `config_changes` | 0 |
| Rahasia tertulis ke YAML oleh panel | 0 (test) |
| Mengaktifkan di mesin tanpa jaringan | gagal eksplisit, tanpa perubahan state (test) |
| Berkas yang dikirim berisi contoh alih-alih default | 0 |
| Berkas yang dikirim membawa rahasia placeholder (`CHANGE_ME`) | 0 |

## 7. Acceptance criteria

- [ ] Satu image terbangun lokal dengan `EXTRAS` kosong dan menjalankan `eidesoul selftest`; tag rilis baru dipublikasikan kalau rilisnya ada.
- [ ] Image tidak memuat dependency gateway/plugin: `pip list` di container tidak menampilkan `neonize` maupun `google-api-python-client`.
- [ ] Install baru mulai dengan spine saja: `/status` melaporkan setiap gateway dan plugin `absent/mati`, dan tidak ada kapabilitas yang diberikan.
- [ ] Mengaktifkan komponen mengunduh kode per digest; digest salah membatalkan tanpa perubahan config dan tanpa berkas terpasang.
- [ ] Mengaktifkan komponen tanpa izin role membuatnya inert (`unavailable`, bukan `denied`) dan `/tools` menjelaskan alasannya.
- [ ] Menonaktifkan menyimpan berkas dan menghentikan kapabilitas; menghapus membuang kode dan status; keduanya muncul di ledger.
- [ ] Panel menulis perubahan konfigurasi yang sama dengan hasil `eidesoul config import`, dengan baris `config_changes`; suntingan YAML manual tampil sebagai drift sampai di-import.
- [ ] `web.enabled: false` menghentikan panel dan membiarkan engine beserta gateway-nya tetap jalan.
- [ ] Tidak ada nilai rahasia di `config.yaml`, `roles.yaml`, atau `soul.yaml` sesudah sesi panel (test).
- [ ] `AGENTS.md` §Shape menyatakan engine bukan server sementara panel adalah aplikasi operator terpisah; `docs/**` masuk daftar dokumen tracked.
- [ ] Tidak ada berkas `*.example` yang di-track, dan `templates.files()` menulis tepat `config.yaml`, `roles.yaml`, `soul.yaml`, dan `.env`.
- [ ] `compose.yaml` menyalakan Postgres hanya dengan `POSTGRES_PASSWORD` di `.env`, dan tidak ada literal `CHANGE_ME` yang ikut dikirim.
- [ ] Kunci yang ditambahkan ke `eidesoul/admin/templates.py` sampai ke berkas yang ditulis `init`, tanpa salinan kedua yang harus diperbarui.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| "Tidak ada yang default" meluruh dan extra merembes kembali ke image | Metrik §6: daftar dependency container diperiksa; plugin membawa dependency-nya sendiri |
| Unduh-saat-aktif butuh jaringan di saat yang tidak nyaman | `components.fetch: false` memungkinkan operator menaruh artefak terverifikasi lebih dulu; kegagalannya eksplisit |
| Panel jadi jalur konfigurasi kedua yang paralel | Satu jalur import (§4.2), baris `config_changes`, dan tampilan drift di panel |
| Panel yang terbuka di luar loopback jadi permukaan serang | Default loopback, `remote_ack` eksplisit, rahasia `.env`, tanpa mode anonim |
| Web-first memancing logika engine masuk ke panel | §4.5 plus aturan repo bahwa `eidesoul/engine/` tidak pernah mengimpor panel |
| Panel on-by-default mengejutkan operator headless | Satu saklar, didokumentasikan di baris pertama runbook; CLI tidak pernah bergantung pada panel |
| Image yang belum dirilis diperlakukan sebagai rilis | Aturan versi dev (§Versioning); tanpa tag yang menyerupai rilis selama belum ada rilis |

## 9. Interaksi dengan ADR lain

- **ADR-0028**: mendefinisikan unit plugin, manifest, dan siklus hidupnya; ADR ini yang mengatur dari mana ia didistribusikan dan bagaimana ia dinyalakan.
- **ADR-0015**: manifest kapabilitas tetap kebenaran untuk model; panel hanya menyunting konfigurasi di belakangnya.
- **ADR-0018**: gateway tetap memegang kontraknya; mengaktifkannya tidak mengubah antarmukanya.
- **ADR-0016**: `config_changes` dan jalur dokumen tetap urusan pemilik skema.
- **ADR-0022**: administrator pertama dibuat pada jalan pertama; hanya `label` yang bisa diubah sesudahnya.
- **ADR-0009**: `docs/**` adalah permukaan dokumen tracked, jadi ADR ini dan dokumentasi panel hidup di bawah version control.

## 10. Referensi kode

- `Dockerfile:1,28-35` — build image hari ini dengan `ARG EXTRAS=""`.
- `pyproject.toml` `[project.optional-dependencies]` — extra `whatsapp`, `google`, `dev`.
- `plugins/google/` — kapabilitas Google setelah pindah; `eidesoul/gateways/whatsapp/tools/chat_history.py` — tool arsip setelah pindah.
- `eidesoul/__main__.py` — `init`, `models`, `config import`, `selftest` (permukaan CLI yang setara).
- `eidesoul/engine/config.py`, `eidesoul/engine/stores/documents.py` — jalur konfigurasi yang dipakai ulang panel.
- `eidesoul/engine/schema.py` — ledger `config_changes`.
- `panel/` — tree baru di akar repo (usulan, belum dibuat).

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0027-distribution-web-config.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/0027-distribution-web-config.md`.*
