> Terjemahan turunan — jangan sunting; sumber: 0028-plugin-model.md (sha256: 6fd6670d004b)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0028: Model Plugin — Satu Unit Terinstall untuk Kapabilitas Terunduh

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0027 (distribusi dan kapabilitas clean by default), ADR-0001 (permukaan), ADR-0015 (manifest kapabilitas), ADR-0007 (kelas izin), ADR-0022 (`administrator`)
- **Supersedes:** —
- **Terkait:** ADR-0018 (kontrak gateway), ADR-0019 (ledger), ADR-0021 (kontrak tool), ADR-0026 (tool core yang tetap di core)
- **Implementasi:** implemented — bukti: `eidesoul/engine/components.py` (manifest, digest tree, fetch-by-digest, satu import `entry`, siklus/status), `plugins/google/` (manifest + kredit + tool), extra `google` dihapus, CLI `eidesoul components`, `tests/test_plugins.py`, `tests/test_google_plugin.py`; hasil gate: `pytest -q` 798 lulus, `uv lock --check` bersih
- **Catatan lokasi:** ADR ini lintas-potong (mendefinisikan unit distribusi yang dipakai setiap permukaan), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0028-plugin-model.md`.
- **Amandemen:** 2026-11-03 — versi pertama.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| `google` hari ini tool core | `eidesoul/engine/tools/google.py` berdampingan dengan `read.py`, `shell.py`, … |
| `google` juga extra build-time | `pyproject.toml` `google = ["google-api-python-client…", "google-auth…", "httpx…"]` |
| Tool adalah modul dengan satu kontrak | aturan repo: modul di `eidesoul/engine/tools/` dengan `TOOL = Handler(...)`, nama berkas = `name` |
| Izin tidak pernah datang dari handler | aturan repo: hanya role yang memberi `tools`/`commands`; entri hilang menolak |
| Gateway sudah paket yang terpisah | `eidesoul/gateways/<name>/` dengan `tools|commands` sendiri, transport saja (ADR-0018) |
| Permukaan data untuk konten opsional sudah ada | paket `eidesoul/library/**`, dipilih oleh `soul.style` |

Keputusan pemilik: kapabilitas yang bukan spine adalah **plugin**, dan Google yang pertama — bukan tool
core, dan bukan extra build-time. Kode plugin diunduh saat plugin diaktifkan, jadi ia unit packaging
yang harus bisa dimuat engine tanpa mengimpor kodenya saat import.

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Satu unit packaging | Operator mengaktifkan "google", bukan "extra + kunci config + tool" |
| Tidak ada yang default | Plugin mati, belum diunduh, dan tanpa izin sampai operator bertindak |
| Core tetap bersih | Engine memegang loader dan kontrak manifest, bukan kode atau kosakata plugin |
| Izin tetap milik role | Plugin tidak bisa memberi kapabilitas kepada dirinya; install/aktifkan adalah aksi operator |
| Bisa dibalik | disable menyimpan berkas, remove menghapus; keduanya dicatat |
| Dependency jujur | Dependency plugin tinggal bersama plugin, jadi set terkunci image tidak membengkak |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Plugin sebagai unit terinstall, diunduh saat diaktifkan** | Manifest + kode di `components/<name>`; engine memuat lewat registry yang sudah ada | **Dipilih** |
| **B. Tetap pakai extra Python, tambah saklar config** | `--extra google` tetap, plugin hanya berarti "aktif" | Ditolak: pengaktifan tetap keputusan build dan image membawa semua dependency opsional |
| **C. Plugin sebagai layanan terpisah** | Setiap plugin berjalan di luar proses di balik API | Ditolak untuk sekarang: latensi, mode kegagalan, dan transport baru untuk sesuatu yang sebenarnya pemanggilan library |
| **D. Melipat plugin ke gateway** | Perlakukan `google` sebagai gateway | Ditolak: gateway adalah body (transport), Google adalah kode kapabilitas; dua kosakata itu harus tetap terpisah (ADR-0001 §4.3) |
| **E. Melipat plugin ke tool** | Perlakukan `google` sebagai satu modul tool core lagi | Ditolak: plugin diunduh dan dipasang lewat siklus hidup; tool core dikirim di image dan hanya butuh izin |

## 4. Keputusan

### 4.1 Apa itu plugin (dan apa yang bukan)

**Gateway, tool, dan plugin adalah tiga hal berbeda.** Engine memegang tiga kosakata terpisah
(ADR-0001 §4.3/§4.5), dan ADR ini tidak menambah apa pun pada dua yang pertama: gateway tetap body
(transport plus aksi tubuhnya sendiri), tool tetap kapabilitas yang dipanggil engine, dan plugin adalah
**unit packaging dan siklus hidup**. Tidak ada yang menjadi yang lain: gateway WhatsApp dipasang
*sebagai gateway*, `read`/`shell` adalah tool, dan `google` adalah plugin. Yang mereka bagi adalah satu
filosofi — **clean by default**: tidak ada yang tersambung, diberi izin, atau terpasang sampai operator
mengatakannya.

| Istilah | Arti | Contoh |
|---|---|---|
| **Spine** | Core engine yang selalu aktif | Postgres+pgvector, Mem0, embedder, guard, lease |
| **Tool core** | Kapabilitas yang dikirim di image, diberikan lewat role | `read`, `shell`, `memory`, `settings` |
| **Gateway** | Body: transport plus aksi tubuhnya sendiri (ADR-0018) | WhatsApp; robot nanti |
| **Plugin** | **Unit terinstall yang diunduh saat diaktifkan** dan isinya kode kapabilitas | `google` (mail, calendar) |
| **Extra** | Dependency Python build-time di image (dipakai hanya untuk kebutuhan spine) | `dev` |

Plugin tidak pernah jadi konsep baru dalam penyusunan prompt, pemeriksaan izin, atau memori: apa yang
disediakannya masuk lewat registry yang **sama** seperti modul core, dan gateway yang dibawanya
didaftarkan sebagai gateway — tidak pernah sebagai "gateway plugin".

### 4.2 Manifest

`components/<name>/plugin.yaml`:

```
name: google                  # ^[a-z0-9][a-z0-9-]{0,63}$ (aturan sama dengan paket library)
version: 1.0.0                # semver plugin
engine: ">=0.1,<0.2"          # rentang engine yang kompatibel; ketidakcocokan menolak dimuat
entry: plugin.py:register     # satu fungsi yang dipanggil loader
provides:
  tools: [mail, calendar]
  commands: [mail_digest]
  gateways: []
requires:                     # diunduh ke folder plugin sendiri, tidak pernah ke image
  - google-api-python-client>=2.150,<3
secrets:                      # hanya nama env, nilainya tetap di .env
  - GOOGLE_CLIENT_ID
  - GOOGLE_CLIENT_SECRET
credits:                      # baris kredit README yang harus dibayar plugin ini (lisensi, penulis, sumber, penggunaan)
  license: Apache-2.0
  author: Google LLC
  source: https://github.com/googleapis/google-api-python-client
  use: Gmail and Calendar access
```

- Manifest adalah data, bukan kode: ia dibaca sebelum apa pun diimpor, sehingga plugin yang tidak
  kompatibel atau tidak lengkap ditolak tanpa dieksekusi.
- Nama di `provides` divalidasi terhadap aturan loader sendiri (`name` cocok dengan `name` modul, tidak
  bertabrakan dengan tool core atau gateway) — plugin tidak bisa diam-diam menutupi kapabilitas core.

### 4.3 Siklus hidup

`available → installing → installed → enabled ⇄ disabled → removed`

| Langkah | Aktor | Efek |
|---|---|---|
| `installing` | operator | unduh per digest, verifikasi, urai di `components/<name>`, unduh `requires` ke `components/<name>/deps` |
| `installed` | operator | kode ada, tidak ada izin, tidak ada yang diimpor |
| `enabled` | operator | loader mengimpor `entry`, mendaftarkan yang disebut `provides`, menulis dokumen konfigurasi; role tetap harus memberi setiap kapabilitas |
| `disabled` | operator | kapabilitas mati; berkas dan status tetap; cepat dibalik |
| `removed` | operator | kode dan status plugin dihapus; ledger menyimpan riwayatnya |

- Setiap langkah adalah aksi operator, mendarat di `config_changes` dan ledger aksi, dan ditolak untuk
  non-operator (kelas `authorization`, ADR-0007).
- Install satu-satunya langkah yang butuh jaringan, dan ia fail closed: digest tidak cocok, tidak ada
  install — tanpa uraian separuh, tanpa salinan cadangan, tanpa status "terpasang tapi tak dipercaya".
- Tanpa auto-update dan tanpa unduhan latar: update adalah aksi operator yang memasang digest baru.

### 4.4 Pemuatan, isolasi, dependency

- Satu modul engine (`eidesoul/engine/components.py`, baru) memegang manifest, verifikasi digest,
  langkah unduh, dan satu impor `entry`. Tidak ada bagian lain engine yang mengimpor kode plugin.
- Loader menyerahkan `provides` ke registry yang sudah ada: tool sebagai modul `TOOL = Handler(...)`,
  command sebagai modul `COMMAND = Handler(...)`, gateway lewat registry gateway (ADR-0018). Loader
  tidak pernah membuat kasus khusus untuk nama plugin dan tidak pernah menumbuhkan kosakata plugin.
- Dependency plugin dipasang **di dalam folder plugin** dan diimpor dari sana; set dependency image
  yang di-lock tidak berubah, jadi `uv sync --locked` tetap jalan dan tabel kredit tetap jujur.
- Plugin yang melempar exception saat registrasi dinonaktifkan dengan satu warning dan tidak memblokir engine.
- Core tidak pernah mengimpor plugin saat import modul; plugin yang mati atau dihapus tidak terlihat saat boot.

### 4.5 Google adalah plugin pertama

- `eidesoul/engine/tools/google.py` keluar dari core dan menjadi plugin `google` yang menyediakan tool
  mail dan calendar (plus command digest bila perlu).
- **Extra** `google` dihapus dari `pyproject.toml` (commit yang sama meregenerasi `uv.lock`); baris
  kredit README-nya pindah ke `credits` di manifest plugin.
- `AGENTS.md` §Optional integrations ditulis ulang: Google bukan extra dan bukan tool core; ia plugin,
  diunduh saat diaktifkan, inert saat tidak ada (perilaku "mengembalikan `error:` yang jelas" hari ini
  menjadi jawaban `unavailable` seragam dari loader).
- Soul yang meminta mail tanpa plugin aktif mendapat penolakan jujur dari model, dirumuskan dari fakta
  ketersediaan di manifest — tidak pernah hasil yang dikarang.

### 4.6 Kepercayaan dan izin

- Plugin tidak bisa memberi izin kepada dirinya: izin hanya dari role (`tools`/`commands`), dan manifest
  plugin tidak bisa menulis role.
- Install/enable/disable/remove adalah aksi kelas `authorization`: hanya role dengan kapabilitas itu
  yang boleh, dan panel web adalah salah satu tempat melakukannya (ADR-0027 §4.2).
- Kode dipercaya lewat digest dan asal (sumber yang dikonfigurasi operator); plugin tanpa tanda tangan
  hanya boleh dari sumber yang operator namai eksplisit, dan manifest tidak pernah mengklaim lisensi
  yang tidak dibawanya.
- Tool dari plugin berjalan lewat guard, approval, dan kontrak keluaran yang sama seperti tool core
  (ADR-0007, ADR-0021) — tidak ada jalur pengecualian.

## 5. Konfigurasi

| Kunci | Default | Arti |
|---|---|---|
| `components.root` | `<workspace>/components` | tempat kode plugin hasil unduhan dan dependency-nya |
| `components.fetch` | `true` | unduh kode saat install; `false` = operator menaruh artefak lebih dulu |
| `components.source` | kosong | sumber yang dinamai operator (registry/URL) tempat install diunduh |
| `plugins.<name>.enabled` | `false` | apakah loader mendaftarkan plugin saat boot |
| `plugins.<name>.digest` | kosong | digest terpin yang harus cocok dengan salinan terpasang |

Rahasia hanya nama di `.env`; plugin yang butuh nilai yang tidak ditemukan berstatus `unavailable`,
bukan rusak.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Kode plugin diimpor `eidesoul/engine` saat import modul | 0 |
| Dependency plugin di dalam set terkunci image | 0 |
| Install tanpa digest terverifikasi | 0 (fail closed) |
| Kapabilitas yang tidak diberikan role tapi bisa dijangkau plugin | 0 |
| Tool dari plugin yang melewati kontrak guard/approval/keluaran | 0 |
| Plugin yang versinya dipin di kode, bukan di manifest | 0 |
| Baris kredit yang hilang untuk dependency plugin terunduh | 0 |

## 7. Acceptance criteria

- [ ] Install plugin tidak menulis apa pun sampai digest terverifikasi; digest salah meninggalkan tanpa berkas dan tanpa perubahan config.
- [ ] Tool plugin tidak terlihat di manifest kapabilitas sampai plugin aktif **dan** ada role yang memberikannya.
- [ ] Mengaktifkan plugin dengan entri `secrets` yang belum terpenuhi melaporkan `unavailable` beserta nama env yang hilang, tanpa mematahkan engine dan tanpa mencatat nilainya.
- [ ] Plugin yang menutupi nama tool core ditolak saat pemuatan dengan tabrakan itu disebut.
- [ ] Plugin yang melempar exception saat registrasi dinonaktifkan dengan satu warning dan engine tetap jalan.
- [ ] `google` tidak lagi bisa diimpor dari `eidesoul/engine/tools/`; extra `google` hilang dari `pyproject.toml` dan `uv.lock` cocok.
- [ ] Tanpa plugin `google`, permintaan mail menghasilkan penolakan jujur dari model, bukan data karangan dan bukan traceback plugin.
- [ ] Disable menyimpan berkas dan menghentikan kapabilitas; remove menghapus berkas dan status; keduanya meninggalkan baris ledger.
- [ ] Non-operator tidak bisa install, enable, disable, atau remove plugin (test guard).
- [ ] `eidesoul selftest` mencantumkan setiap plugin dengan `absent | installed | enabled`.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Plugin jadi cara menyelundupkan tool tanpa guard | §4.6: kontrak guard, approval, dan keluaran yang sama; cek tabrakan saat pemuatan |
| Dependency drift antara plugin dan engine | Rentang `engine` di manifest diperiksa sebelum impor; dependency tinggal bersama plugin |
| Unduh-saat-aktif mematahkan install air-gapped | `components.fetch: false` plus artefak yang ditaruh operator; kalau tidak, gagal eksplisit |
| Kosakata plugin merembes ke core | §4.4: satu modul loader, registry tidak berubah, tanpa nama plugin di logika engine (bisa dicek AST) |
| Penghapusan `google` mematahkan install yang sudah ada | Pra-rilis, jadi cutover-nya bersih: tanpa shim dan tanpa alias (aturan repo); runbook menyebutkan memasang plugin |
| Plugin mengklaim lisensi yang tidak dibawanya | `credits` di manifest divalidasi terhadap distribusi terpasang saat pemuatan; lisensi yang hilang dilaporkan, tidak pernah ditebak |

## 9. Interaksi dengan ADR lain

- **ADR-0027**: memegang image, kebijakan unduh-saat-aktif, dan aksi panel yang mengaktifkan plugin.
- **ADR-0015**: manifest kapabilitas melaporkan tool plugin seperti kapabilitas lain, termasuk `unavailable` saat plugin tidak ada.
- **ADR-0007**: install/enable kelas `authorization`; tool plugin mewarisi aturan approval yang sama.
- **ADR-0018**: gateway yang dibawa plugin menaati kontrak gateway yang sama dengan gateway first-party.
- **ADR-0021** / **ADR-0024**: tool plugin mengikuti kontrak input/keluaran yang sama.
- **ADR-0016**: tidak perlu tabel baru; status plugin hidup di dokumen plus ledger yang sudah ada.

## 10. Referensi kode

- `plugins/google/` — plugin yang menggantikan `eidesoul/engine/tools/google.py`.
- `pyproject.toml` `[project.optional-dependencies] google` — extra yang akan dihapus (beserta `uv.lock`).
- `eidesoul/engine/tools/__init__.py` — registry yang diberi makan loader; `execute` tetap satu-satunya cek izin.
- `eidesoul/engine/commands/__init__.py` — registry command.
- `eidesoul/gateways/` — paket gateway yang bisa diekspos loader.
- `eidesoul/library/**` — aturan id paket (`^[a-z0-9][a-z0-9-]{0,63}$`) yang dipakai ulang untuk nama plugin.
- `eidesoul/engine/components.py` — modul baru: manifest, digest, unduh, satu impor (usulan).

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0028-plugin-model.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/0028-plugin-model.md`.*
