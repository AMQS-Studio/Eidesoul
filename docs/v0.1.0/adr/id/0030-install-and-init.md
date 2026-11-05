> Terjemahan turunan — jangan sunting; sumber: 0030-install-and-init.md (sha256: ea4166c90076)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0030: Install On-Machine dan Init Interaktif — Wizard Kita Sendiri, Satu Installer, Dua Runtime

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0027 (distribusi & konfigurasi web-first), ADR-0007 (kelas approval), ADR-0016 (migrasi), ADR-0022 (role `administrator` yang dilindungi), ADR-0029 (store tes)
- **Supersedes:** —
- **Terkait:** ADR-0009 (rumah dokumen), ADR-0015 (manifest kapabilitas), ADR-0019 (observability), ADR-0028 (plugin)
- **Implementasi:** partial — ada: `install.sh` (runtime opt-in, digest uv dipin, `--dry-run`, log, signal, dua runtime), menu curses yang sama dengan `monoagens init`, `--label`/`--stack`/`--engine`/`--database`/`--voice-layer`, kredensial database yang dibuat, administrator `cli local` otomatis, `tests/test_init.py`, `tests/test_install.py`; sisa: cabang panel di layar akhir menunggu `panel/` (ADR-0027)
- **Catatan lokasi:** ADR ini mengikat semua permukaan (ia menentukan bagaimana sebuah host lahir), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0030-install-and-init.md`.
- **Amandemen:** 2026-11-05 — installer memakai lapisan curses yang sama dengan `init`, menanyakan native/compose, container/external, dan Podman/Docker dengan deteksi langsung, meminta sudo lewat terminal, menyiapkan database sebelum `init`, dan mendaftarkan administrator lokal. 2026-11-05 — installer menanyakan native versus compose, kedua stack memakai container Postgres secara default, dan pemasangan runtime yang hilang bersifat opt-in dengan `sudo`. 2026-11-03 — versi pertama. 2026-11-03 — wizard menanyakan lapisan suara sekali (jawaban default tetap nyala) dan membawa `--voice-layer on|off` untuk paritas headless (ADR-0027 §4.3). 2026-11-03 — `--label` (alamat administrator) dan `--stack native|compose` masuk tabel flag: aturan §6 "langkah tanpa padanan flag = 0" menuntutnya.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| Tidak ada jalur install on-machine | quickstart `README.md` dimulai dengan `podman compose up -d postgres`; satu-satunya jalur non-container adalah "clone lalu `uv sync`" dalam bentuk prosa |
| `init` non-interaktif dan menuntut flag | `monoagens init --provider <name>` wajib (`monoagens/__main__.py:709-714`), ditulis dari template kode (`monoagens/admin/init.py:26-46`) |
| Default sudah hidup di satu tempat | `monoagens/admin/templates.py:1` — "one source of truth, no separate example files" (ADR-0027 §4.6) |
| Panel belum ada | tidak ada tree `panel/`; ADR-0027 §4.4 merencanakannya aktif secara default begitu selesai |
| Referensi, bukan template | bootstrap Hermes (`hermes-agent.nousresearch.com/install.sh`, 1302 baris) dipelajari: stage protocol, `uv` dipin dengan SHA-256 per arch, disiplin keluaran tenang, install log, penanganan sinyal, repair idempoten |
| Arahan pemilik | setup interaktif tidak boleh meniru: gaya kita sendiri, lebih mampu, dengan slot logo yang diisi belakangan |

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Identitas sendiri | Wizard dirancang untuk produk ini: rail langkah, langkah review, dan receipt — bukan bingkai proyek lain |
| Operator dulu | Satu perintah memasang, satu perintah menginisialisasi, dan wizard mengatakan tepat apa yang ia tulis |
| Default tetap di kode | Installer dan wizard mengorkestrasi `templates.py`; tidak ada yang membawa default kedua |
| Dua stack, satu installer | Native (mesin ini) dan compose (server) memakai skrip yang sama dan `init` yang sama |
| Tidak mengejutkan host | Tanpa `sudo` diam-diam, tanpa pemasangan paket di belakang operator, tanpa menyunting PATH tanpa penjaga |
| Paritas headless | Setiap pertanyaan punya flag-nya; input pipa tidak pernah menggantung dan tidak pernah menebak |
| Bertahap jujur | Panel belum dibangun, jadi skrip berakhir di jalur CLI dan mengatakannya |
| Bisa dibalik | Menjalankannya lagi berarti repair; ia tidak pernah menimpa berkas yang sudah ada tanpa ditanya |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Meniru installer referensi** | menyalin banner, stage protocol, dan kalimatnya | Ditolak: itu identitas produk lain, dan stage protocol ada untuk driver GUI mereka, yang kita tidak punya |
| **B. Installer dan wizard kita sendiri** | `install.sh` di akar repo, `init` interaktif dengan bahasa visual kita, satu slot logo | **Dipilih** |
| **C. Prompt hanya di bash** | installer yang bertanya, Python tak pernah | Ditolak: paritas headless dan kemampuan uji jadi korban, dan default akan mengalir balik ke shell |
| **D. Paket distro (RPM/COPR)** | `dnf install monoagens` | Ditunda: butuh rilis yang belum ada (§Versioning) dan cerita maintainer |

## 4. Keputusan

### 4.1 `install.sh` di akar repo

Satu skrip Bash, dipanggil dari klon (`bash install.sh`) atau dari jaringan
(`bash <(curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh)`). Ia seluruh jalur install untuk kedua runtime. `curl ... | bash` tidak meninggalkan TTY untuk wizard, jadi bentuk jaringan mengunduh skrip dulu atau memakai process substitution; `curl | bash` tetap fail-closed.

| Aspek | Aturan |
|---|---|
| Pilihan runtime | `--stack native` menjalankan engine di host dengan Postgres di container; `--stack compose` menjalankan keduanya di container. Installer bertanya kalau `--stack` tidak diberikan. |
| Pilihan container | `--engine podman\|docker` memilih runtime Postgres; menu menampilkan **terpasang dan berjalan**, **terpasang**, atau **belum terpasang** untuk keduanya, dan menawarkan pemasangan yang hilang. |
| Pilihan database | `--database container\|external` (native saja). URL eksternal ditanyakan di langkah itu dan diteruskan ke `init`; compose memakai Postgres-nya sendiri. |
| Izin menyentuh host | **tidak pernah** menjalankan `sudo` tanpa persetujuan eksplisit. Runtime yang hilang ditawarkan untuk dipasang; `sudo -v` meminta password di terminal dan tidak menyimpannya. `--install-runtime` adalah padanan headless. Tidak ada paket sistem lain yang dipasang. |
| `uv` | kalau absen: unduh rilis yang dipin untuk arsitektur yang berjalan dan verifikasi SHA-256 sebelum dipakai; pin hidup di satu blok berkomentar dengan baris "cara menaikkannya" |
| Python | menuntut 3.14+ dan melaporkan interpreter yang ia temukan; image container mempertahankan floor 3.14 yang sama — perbedaannya dinyatakan, tidak disembunyikan |
| Dependency | `uv sync --locked` di dalam direktori instalasi (`--dir`, default `~/.monoagens/app`); tak ada dependency yang dipasang di luarnya |
| Setup | memakai menu curses yang sama dengan `init` untuk runtime, lokasi, database, dan engine, lalu menyerahkan ke `monoagens init` dengan lokasi terkunci (§4.3): interaktif di terminal, digerakkan flag kalau bukan |
| Database | kedua stack memakai Postgres Podman/Docker secara default. Installer membuat `POSTGRES_PASSWORD` dan `MONOAGENS_DATABASE_URL` **sebelum** `init` supaya review menampilkannya; `--database-url` eksternal yang eksplisit melewati container di mode native |
| Lanjutan | nyalakan Postgres (atau seluruh stack compose), `monoagens db upgrade`, daftarkan `cli local` sebagai administrator, lalu `monoagens selftest` |
| Penutup | menawarkan pilihan (§4.5): buka panel, atau tetap di command line |
| Pengamatan | satu baris status selama child berjalan; keluaran child non-interaktif ditambahkan ke `~/.monoagens/logs/install.log`, sedangkan wizard interaktif tetap di terminal (tidak ada rahasia di log); kegagalan mencetak baris terakhir plus path log |
| Sinyal | `INT`/`TERM`/`HUP` menandai jalannya `interrupted` dan di-raise ulang, jadi exit status tetap milik sinyalnya |
| Jalan ulang | repair: checkout yang ada di-refresh dari `main` sementara `config.yaml`, `roles.yaml`, `soul.yaml`, `.env`, `.monoagens`, `.venv`, `plugins`, dan `panel` tetap; checkout git dengan perubahan lokal dipertahankan; checkout parsial tanpa `pyproject.toml` ditawarkan untuk dihapus sebelum retry |
| Platform | Linux x86_64 dan aarch64 di versi ini; selain itu ditolak dengan satu baris |
| Pratinjau | `--dry-run` mencetak setiap aksi dan tidak menulis apa pun |

### 4.2 Bahasa visual wizard sendiri

Setup interaktif dirancang di sini, bukan dipinjam. Yang dipinjam dari referensi hanya *disiplinnya* (keluaran tenang, ada log, kegagalan jujur).

| Elemen | Desain kita |
|---|---|
| Header | logo dan wordmark hasil generate yang sama dengan `init`, penghitung langkah, dan satu baris peran; terminal sempit atau `NO_COLOR` jatuh ke baris polos |
| Langkah | rail langkah bernama dengan penghitung (`1/5 provider`, `2/5 model`, …) di mana langkah aktif ditandai, langkah selesai tetap bertanda, dan satu langkah bisa diulang tanpa memulai dari awal |
| Prompt | pilihan bernomor secara default supaya numpad berguna; mengetik nilai selalu boleh di tempat nilai yang diharapkan; `Ctrl-C` membatalkan seluruh jalannya dan tak meninggalkan apa pun |
| Review | sebelum menulis, satu layar menampilkan setiap berkas yang akan dibuat dan setiap nilai yang akan disimpannya (rahasia ditampilkan **hanya sebagai nama env**) dengan `apply`/`change`/`cancel` |
| Receipt | sesudah menulis: berkas beserta modenya, dua perintah berikutnya, dan di mana log-nya — operator tak perlu menebak apa yang terjadi |
| Warna | hanya di terminal, `NO_COLOR` dihormati, tanpa animasi, dan tanpa box drawing yang rusak di terminal 60 kolom |
| Suara | teks operator berbahasa English (diagnostik internal, sesuai aturan repo); bahasa soul mengatur chat, bukan installer |

### 4.3 `monoagens init` jadi interaktif di terminal

- Di TTY, `init` menanyakan hal yang sama seperti yang dibawa flag-nya: lokasi (kecuali installer sudah menanyakannya dan mengirim `--no-location`), provider, model (opsional), URL database, label administrator pertama, dan **lapisan suara** — tetap nyalakan emosi, humanize, dan karakter (jawaban default) atau matikan salah satunya. Berkas yang sudah ada ditawarkan sebagai `keep` / `change` / `replace`.
- Di luar terminal (pipa, CI, `--non-interactive`), ia **gagal tertutup**: ia menyebut flag yang dibutuhkan alih-alih menunggu input yang tak mungkin datang.
- Rahasia tidak pernah masuk YAML: wizard menulis **nama** env dan menaruh nilainya di `.env` (`0600`), tepat seperti jalur non-interaktif sekarang (`monoagens/admin/init.py:14-20`).
- Setiap nilai yang ditulisnya berasal dari `monoagens/admin/templates.py` plus jawaban operator — wizard tidak memiliki default sendiri.
- Validasi terjadi sebelum menulis: nama provider yang tak dikenal, kunci yang ditolak provider dengan 401/403, model yang tidak ada di daftar provider saat daftar itu terjangkau, atau URL database yang tak bisa dipakai dilaporkan di langkah yang menanyakannya, bukan setelah berkas ada di disk. Kunci yang ditolak menawarkan **coba lagi**, **tetap simpan**, atau **kosongkan** agar operator tidak terjebak di loop retry.
- Berkas ditulis hanya setelah seluruh wizard berhasil (tulis-lalu-rename), sehingga jalannya yang terputus tak bisa meninggalkan host setengah terkonfigurasi.

### 4.4 Apa yang ditulis wizard, dan apa yang tidak pernah disentuhnya

| Menulis | Tidak pernah |
|---|---|
`config.yaml`, `roles.yaml`, `soul.yaml`, `.env` di direktori yang dipilih, dari template | berkas config kedua, salinan `.example`, atau default yang tidak ada di `templates.py` |
`.env` dengan nama dan nilai kosong, mode `0600` | nilai rahasia di YAML, di log, atau di riwayat shell |
direktori `.monoagens/workspace` | apa pun di luar `--dir`; tanpa `sudo` tanpa persetujuan, tanpa layanan sistem, tanpa baris PATH tanpa penjaga duplikat |

### 4.5 Akhir instalasi adalah pilihan, bukan kejutan

Installer berakhir dengan menawarkan: **buka panel** (begitu panel ada dan `web.enabled` bernilai true — ADR-0027 §4.4) atau **tetap di command line** (cetak perintah headless-nya). Ia tidak pernah memutuskan untuk operator, dan selama panel belum ada, opsi kedua satu-satunya dan diberi label begitu.

Jalur compose mempertahankan bentuk yang sama: installer menyiapkan kredensial database, `init` menulis berkas host, lalu menyalakan stack dan menjalankan `db upgrade` plus `people add` di dalam container (`docker compose exec monoagens monoagens ...`). Tidak ada installer kedua untuk container.

### 4.6 Logo menyusul, slot-nya tidak berpindah

Banner memesan area logo sekarang supaya mark-nya bisa ditaruh tanpa menyentuh tata letak wizard, dan ia tidak pernah jadi kunci config: tampilan installer adalah kode, bukan konfigurasi.

## 5. Konfigurasi

| Flag | Arti |
|---|---|
| `--dir PATH` | direktori instalasi (default `~/.monoagens/app`) |
| `--dry-run` | cetak setiap aksi, tak menulis apa pun |
| `--non-interactive` | tak pernah bertanya; gagal kalau nilai wajib tidak ada |
| `--provider NAME` / `--model ID` | diteruskan ke `init` |
| `--engine podman\|docker` | runtime container untuk Postgres; menu menampilkan status deteksi |
| `--database container\|external` | pilihan database native; external butuh `--database-url` |
| `--install-runtime` | izinkan pemasangan Podman/Docker dan compose provider dengan `sudo` saat hilang |
| `--database-url URL` | URL Postgres eksternal; melewati container database di mode native |
| `--skip-selftest` | lewati self-test penutup (untuk otomasi yang menjalankannya terpisah) |
| `--voice-layer on\|off` | tetap nyalakan emosi, humanize, dan karakter (default `off`, ADR-0041) atau matikan ketiganya |
| `--no-color` | keluaran polos (juga dihormati lewat `NO_COLOR`) |

Tidak ada kunci yang ditambahkan ke `config.yaml` oleh ADR ini: perilaku installer adalah flag, dan konfigurasi host tetap seperti yang ditulis `init`.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Default konfigurasi wizard di luar `monoagens/admin/templates.py` | 0 |
| Pemanggilan `sudo` tanpa persetujuan eksplisit | 0 |
| Kredensial database yang dibuat untuk container | 1 password, hanya ditulis ke `.env` mode `0600` |
| Prompt saat stdin bukan terminal | 0 (gagal dengan daftar flag) |
| Rahasia yang ditulis wizard ke YAML, log, atau riwayat | 0 |
| Berkas yang tertinggal oleh `Ctrl-C` di tengah jalan | 0 |
| Perbedaan perilaku antara `--dry-run` dan jalan sesungguhnya | 0 aksi, 0 tulisan |
| Langkah yang tanpa padanan flag | 0 |
| Tata letak yang bergantung logo | 0 (slot disiapkan sekarang, diisi nanti) |

## 7. Acceptance criteria

- [ ] `bash install.sh --dry-run` mencetak rencananya dan tidak mengubah filesystem (diperiksa dengan daftar sebelum/sesudah).
- [ ] Di mesin tanpa `uv`, rilis yang dipin diunduh, SHA-256-nya diverifikasi, dan ketidakcocokan membatalkan sebelum apa pun terjadi.
- [ ] Di mesin tanpa Postgres atau runtime container, installer mencetak perintah pemasangan tepatnya dan tidak menjalankannya tanpa persetujuan.
- [ ] `install.sh` tidak melakukan pemanggilan `sudo` di jalan normal; `--install-runtime` atau jawaban `y` eksplisit adalah satu-satunya jalur yang memasang runtime.
- [ ] `monoagens init` di TTY menyusuri provider → model → URL database → label administrator, menampilkan layar review, lalu menulis empat berkas dengan `.env` dan `roles.yaml` bermode `0600`.
- [ ] `monoagens init` dengan stdin pipa dan tanpa flag keluar non-zero sambil menyebut flag yang kurang; ia tidak pernah menggantung.
- [ ] `Ctrl-C` di langkah wizard mana pun tidak meninggalkan berkas baru maupun berkas separuh (diperiksa dengan daftar sebelum/sesudah).
- [ ] Nama provider yang tak dikenal atau daftar model yang tak terjangkau gagal di langkah yang menanyakannya, sebelum berkas apa pun ditulis.
- [ ] Menjalankan wizard dua kali menawarkan `keep` / `change` / `replace` dan tidak pernah menimpa diam-diam.
- [ ] Pilihan penutup menawarkan panel dan command line; tanpa panel yang terbangun, opsi panel tidak ada dan langkah command line dicetak.
- [ ] Pemanggilan `init` yang sama bekerja di dalam container compose tanpa flag yang berbeda.
- [ ] `NO_COLOR=1` dan terminal 60 kolom menghasilkan keluaran polos yang terbaca, dan slot logo turun ke teks.
- [ ] Log install tidak memuat nilai rahasia (diuji dengan password penanda).
- [ ] `pytest -q` dan `python scripts/adr.py --check` tetap lulus; perilaku wizard ditutup oleh tes CLI.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| `curl \| bash` menjalankan sesuatu yang belum pernah dibaca operator | `--dry-run`, header pendek yang menyebut repositori dan revisinya, dan alternatif `bash install.sh` atau `bash <(curl ...)`. Bentuk pipa tidak punya TTY, jadi wizard fail-closed, bukan menggantung. |
| Wizard melenceng jadi sumber default kedua | §4.3: nilai datang dari `templates.py` plus jawaban; metrik §6 dan layar review membuat default kedua terlihat |
| Wizard yang menggantung di otomasi | stdin pipa tidak pernah di-prompt; flag yang kurang disebutkan sebagai gantinya |
| Host setengah tertulis setelah interupsi | Tulis-lalu-rename, dan tak ada yang ditulis sebelum langkah review diterima |
| Installer perlahan tumbuh jadi manajer paket | Ia hanya memasang Podman/Docker dan compose provider-nya, hanya setelah persetujuan eksplisit; paket lain dicetak, bukan dipasang |
| `uv` yang dipin membusuk | Blok pin membawa instruksi menaikkannya sendiri dan hanya satu suntingan; versinya dicetak di setiap jalan |
| Janji Python 3.14+ versus image 3.14 | Kedua angka dinyatakan di tempatnya masing-masing; installer melaporkan interpreter yang ia pakai |
| Logo datang terlambat dan merusak tata letak | Slot-nya berukuran tetap dan turun ke teks; tidak ada konfigurasi yang bergantung padanya |
| Panel nanti butuh keluaran yang bisa dibaca mesin | `--non-interactive` sudah ada; hasil JSON bisa ditambahkan tanpa mengubah desain interaktifnya (keputusan untuk ADR panel) |

## 9. Interaksi dengan ADR lain

- **ADR-0027**: panel tetap permukaan konfigurasi utama; ADR ini hanya memutuskan bagaimana sebuah host dibuat dan bagaimana operator mencapainya.
- **ADR-0022**: wizard menanyakan label administrator pertama; role-nya tetap dilindungi dan hanya bisa diganti nama lewat `roles.yaml` plus `config import`.
- **ADR-0016**: `db upgrade` setelah `init` adalah satu-satunya langkah skema yang dijalankan installer.
- **ADR-0029**: perilaku wizard ditutup oleh tes CLI; tes yang menyentuh store memakai store nyata, bukan yang disimulasikan.
- **ADR-0019**: log install adalah artefak operator dengan himpunan kelas gagal yang tertutup; tidak ada konten, path, maupun rahasia yang dicatat di luar yang dibutuhkan sebuah kegagalan.
- **ADR-0028**: "aktifkan plugin" tetap aksi operator sesudah install; installer tidak pernah mengaktifkan, mengunduh, atau memberi izin apa pun.

## 10. Referensi kode

- `monoagens/admin/init.py:14-46` — penulisnya, mode `0600`-nya, dan aturan tidak-menimpa.
- `monoagens/admin/templates.py:1,151,253` — sumber default tunggal yang dipakai ulang wizard.
- `monoagens/__main__.py:587,709-714` — `_init`, subperintah `init`, dan `--provider` yang wajib.
- `monoagens/providers/__init__.py` — nama provider dan variabel kuncinya, dipakai untuk validasi.
- `README.md:88-105`, `README.id.md:96-112` — quickstart yang jalur native ADR ini duduk di sampingnya.
- `compose.yaml` — jalur container yang mempertahankan `init` yang sama.
- `monoagens/admin/install_ui.py` — menu installer di lapisan terminal bersama.
- `install.sh` — pilihan runtime, database, dan engine; pemasangan runtime opt-in; kredensial database yang dibuat; bootstrap curl.
- `pyproject.toml` — tidak berubah: installer tak menambah dependency, dan `uv` yang dipin diunduh, bukan di-vendor.

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0030-install-and-init.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/0030-install-and-init.md`.*
