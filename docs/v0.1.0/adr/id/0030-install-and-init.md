> Terjemahan turunan — jangan sunting; sumber: 0030-install-and-init.md (sha256: 71613bb76376)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0030: Install On-Machine dan Init Interaktif — Wizard Kita Sendiri, Satu Installer, Dua Stack

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0027 (distribusi & konfigurasi web-first), ADR-0007 (kelas approval), ADR-0016 (migrasi), ADR-0022 (role `administrator` yang dilindungi), ADR-0029 (store tes)
- **Supersedes:** —
- **Terkait:** ADR-0009 (rumah dokumen), ADR-0015 (manifest kapabilitas), ADR-0019 (observability), ADR-0028 (plugin)
- **Implementasi:** draft — belum ada yang ditulis: belum ada installer, belum ada `init` interaktif, belum ada panel. ADR ini rencana induk untuk jalur onboarding.
- **Catatan lokasi:** ADR ini mengikat semua permukaan (ia menentukan bagaimana sebuah host lahir), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0030-install-and-init.md`.
- **Amandemen:** 2026-11-03 — versi pertama. 2026-11-03 — wizard menanyakan lapisan suara sekali (jawaban default tetap nyala) dan membawa `--voice-layer on|off` untuk paritas headless (ADR-0027 §4.3).

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| Tidak ada jalur install on-machine | quickstart `README.md` dimulai dengan `podman compose up -d postgres`; satu-satunya jalur non-container adalah "clone lalu `uv sync`" dalam bentuk prosa |
| `init` non-interaktif dan menuntut flag | `eidesoul init --provider <name>` wajib (`eidesoul/__main__.py:709-714`), ditulis dari template kode (`eidesoul/admin/init.py:26-46`) |
| Default sudah hidup di satu tempat | `eidesoul/admin/templates.py:1` — "one source of truth, no separate example files" (ADR-0027 §4.6) |
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
| **D. Paket distro (RPM/COPR)** | `dnf install eidesoul` | Ditunda: butuh rilis yang belum ada (§Versioning) dan cerita maintainer |

## 4. Keputusan

### 4.1 `install.sh` di akar repo

Satu skrip Bash, dipanggil dari klon (`bash install.sh`) atau dari jaringan
(`curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash`). Ia seluruh jalur install untuk kedua stack.

| Aspek | Aturan |
|---|---|
| Izin menyentuh host | **tidak pernah** menjalankan `sudo`; paket yang hilang mencetak perintah tepatnya (`dnf install postgresql-server pgvector`, `postgresql-setup --initdb`) lalu menghentikan stage itu |
| `uv` | kalau absen: unduh rilis yang dipin untuk arsitektur yang berjalan dan verifikasi SHA-256 sebelum dipakai; pin hidup di satu blok berkomentar dengan baris "cara menaikkannya" |
| Python | menuntut 3.11+ dan melaporkan interpreter yang ia temukan; image container mempertahankan pin 3.12-nya — perbedaannya dinyatakan, tidak disembunyikan |
| Dependency | `uv sync --locked` di dalam direktori instalasi (`--dir`, default `~/.eidesoul/app`); tak ada dependency yang dipasang di luarnya |
| Setup | menyerahkan ke `eidesoul init` (§4.3): interaktif di terminal, digerakkan flag kalau bukan |
| Lanjutan | `eidesoul db upgrade`, lalu `eidesoul selftest` |
| Penutup | menawarkan pilihan (§4.5): buka panel, atau tetap di command line |
| Pengamatan | satu baris status selama child berjalan, semuanya ditambahkan ke `~/.eidesoul/logs/install.log`, dan kegagalan mencetak baris terakhir plus path log |
| Sinyal | `INT`/`TERM`/`HUP` menandai jalannya `interrupted` dan di-raise ulang, jadi exit status tetap milik sinyalnya |
| Jalan ulang | repair: tidak pernah menimpa berkas yang ada atau meng-clone ulang di atas perubahan lokal tanpa ditanya |
| Platform | Linux x86_64 dan aarch64 di versi ini; selain itu ditolak dengan satu baris |
| Pratinjau | `--dry-run` mencetak setiap aksi dan tidak menulis apa pun |

### 4.2 Bahasa visual wizard sendiri

Setup interaktif dirancang di sini, bukan dipinjam. Yang dipinjam dari referensi hanya *disiplinnya* (keluaran tenang, ada log, kegagalan jujur).

| Elemen | Desain kita |
|---|---|
| Header | blok berlebar tetap dengan **slot logo** (`[logo]`) dan satu baris peran; saat mark-nya datang ia menggantikan slot itu, dan terminal sempit atau `NO_COLOR` jatuh ke baris polos |
| Langkah | rail langkah bernama dengan penghitung (`1/5 provider`, `2/5 model`, …) di mana langkah aktif ditandai, langkah selesai tetap bertanda, dan satu langkah bisa diulang tanpa memulai dari awal |
| Prompt | pilihan bernomor secara default supaya numpad berguna; mengetik nilai selalu boleh di tempat nilai yang diharapkan; `Ctrl-C` membatalkan seluruh jalannya dan tak meninggalkan apa pun |
| Review | sebelum menulis, satu layar menampilkan setiap berkas yang akan dibuat dan setiap nilai yang akan disimpannya (rahasia ditampilkan **hanya sebagai nama env**) dengan `apply`/`change`/`cancel` |
| Receipt | sesudah menulis: berkas beserta modenya, dua perintah berikutnya, dan di mana log-nya — operator tak perlu menebak apa yang terjadi |
| Warna | hanya di terminal, `NO_COLOR` dihormati, tanpa animasi, dan tanpa box drawing yang rusak di terminal 60 kolom |
| Suara | teks operator berbahasa English (diagnostik internal, sesuai aturan repo); bahasa soul mengatur chat, bukan installer |

### 4.3 `eidesoul init` jadi interaktif di terminal

- Di TTY, `init` menanyakan hal yang sama seperti yang dibawa flag-nya: provider, model (opsional), URL database, label administrator pertama, dan **lapisan suara** — tetap nyalakan emosi, humanize, dan karakter (jawaban default) atau matikan salah satunya. Berkas yang sudah ada ditawarkan sebagai `keep` / `change` / `replace`.
- Di luar terminal (pipa, CI, `--non-interactive`), ia **gagal tertutup**: ia menyebut flag yang dibutuhkan alih-alih menunggu input yang tak mungkin datang.
- Rahasia tidak pernah masuk YAML: wizard menulis **nama** env dan menaruh nilainya di `.env` (`0600`), tepat seperti jalur non-interaktif sekarang (`eidesoul/admin/init.py:14-20`).
- Setiap nilai yang ditulisnya berasal dari `eidesoul/admin/templates.py` plus jawaban operator — wizard tidak memiliki default sendiri.
- Validasi terjadi sebelum menulis: nama provider yang tak dikenal, model yang tidak ada di daftar provider saat daftar itu terjangkau, atau URL database yang tak bisa dipakai dilaporkan di langkah yang menanyakannya, bukan setelah berkas ada di disk.
- Berkas ditulis hanya setelah seluruh wizard berhasil (tulis-lalu-rename), sehingga jalannya yang terputus tak bisa meninggalkan host setengah terkonfigurasi.

### 4.4 Apa yang ditulis wizard, dan apa yang tidak pernah disentuhnya

| Menulis | Tidak pernah |
|---|---|
`config.yaml`, `roles.yaml`, `soul.yaml`, `.env` di direktori yang dipilih, dari template | berkas config kedua, salinan `.example`, atau default yang tidak ada di `templates.py` |
`.env` dengan nama dan nilai kosong, mode `0600` | nilai rahasia di YAML, di log, atau di riwayat shell |
direktori `.eidesoul/workspace` | apa pun di luar `--dir`; tanpa `sudo`, tanpa layanan sistem, tanpa baris PATH tanpa penjaga duplikat |

### 4.5 Akhir instalasi adalah pilihan, bukan kejutan

Installer berakhir dengan menawarkan: **buka panel** (begitu panel ada dan `web.enabled` bernilai true — ADR-0027 §4.4) atau **tetap di command line** (cetak perintah headless-nya). Ia tidak pernah memutuskan untuk operator, dan selama panel belum ada, opsi kedua satu-satunya dan diberi label begitu.

Jalur compose mempertahankan bentuk yang sama: nyalakan stack-nya, lalu pakai panel atau jalankan `init` yang sama di dalam container (`docker compose exec eidesoul eidesoul init ...`). Tidak ada installer kedua untuk container.

### 4.6 Logo menyusul, slot-nya tidak berpindah

Banner memesan area logo sekarang supaya mark-nya bisa ditaruh tanpa menyentuh tata letak wizard, dan ia tidak pernah jadi kunci config: tampilan installer adalah kode, bukan konfigurasi.

## 5. Konfigurasi

| Flag | Arti |
|---|---|
| `--dir PATH` | direktori instalasi (default `~/.eidesoul/app`) |
| `--dry-run` | cetak setiap aksi, tak menulis apa pun |
| `--non-interactive` | tak pernah bertanya; gagal kalau nilai wajib tidak ada |
| `--provider NAME` / `--model ID` | diteruskan ke `init` |
| `--database-url URL` | diteruskan ke `init` dan ditulis ke `.env` sebagai nilai, tidak pernah ke YAML |
| `--skip-selftest` | lewati self-test penutup (untuk otomasi yang menjalankannya terpisah) |
| `--voice-layer on\|off` | tetap nyalakan emosi, humanize, dan karakter (default `on`) atau matikan ketiganya |
| `--no-color` | keluaran polos (juga dihormati lewat `NO_COLOR`) |

Tidak ada kunci yang ditambahkan ke `config.yaml` oleh ADR ini: perilaku installer adalah flag, dan konfigurasi host tetap seperti yang ditulis `init`.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Nilai default yang hidup di luar `eidesoul/admin/templates.py` | 0 |
| Pemanggilan `sudo` oleh installer | 0 |
| Prompt saat stdin bukan terminal | 0 (gagal dengan daftar flag) |
| Rahasia yang ditulis wizard ke YAML, log, atau riwayat | 0 |
| Berkas yang tertinggal oleh `Ctrl-C` di tengah jalan | 0 |
| Perbedaan perilaku antara `--dry-run` dan jalan sesungguhnya | 0 aksi, 0 tulisan |
| Langkah yang tanpa padanan flag | 0 |
| Tata letak yang bergantung logo | 0 (slot disiapkan sekarang, diisi nanti) |

## 7. Acceptance criteria

- [ ] `bash install.sh --dry-run` mencetak rencananya dan tidak mengubah filesystem (diperiksa dengan daftar sebelum/sesudah).
- [ ] Di mesin tanpa `uv`, rilis yang dipin diunduh, SHA-256-nya diverifikasi, dan ketidakcocokan membatalkan sebelum apa pun terjadi.
- [ ] Di mesin tanpa Postgres, installer mencetak perintah `dnf` tepatnya dan tidak menjalankannya.
- [ ] `install.sh` tidak melakukan pemanggilan `sudo` (dibuktikan dengan menjalankannya di PATH tanpa `sudo`).
- [ ] `eidesoul init` di TTY menyusuri provider → model → URL database → label administrator, menampilkan layar review, lalu menulis empat berkas dengan `.env` dan `roles.yaml` bermode `0600`.
- [ ] `eidesoul init` dengan stdin pipa dan tanpa flag keluar non-zero sambil menyebut flag yang kurang; ia tidak pernah menggantung.
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
| `curl \| bash` menjalankan sesuatu yang belum pernah dibaca operator | `--dry-run`, header pendek yang menyebut repositori dan revisinya, dan alternatif `bash install.sh` dari klon yang didokumentasikan |
| Wizard melenceng jadi sumber default kedua | §4.3: nilai datang dari `templates.py` plus jawaban; metrik §6 dan layar review membuat default kedua terlihat |
| Wizard yang menggantung di otomasi | stdin pipa tidak pernah di-prompt; flag yang kurang disebutkan sebagai gantinya |
| Host setengah tertulis setelah interupsi | Tulis-lalu-rename, dan tak ada yang ditulis sebelum langkah review diterima |
| Installer perlahan tumbuh jadi manajer paket | Ia tak memasang apa pun di luar direktorinya dan tak pernah menjalankan `sudo`; paket yang tak ditemukan dicetak, bukan dipasang |
| `uv` yang dipin membusuk | Blok pin membawa instruksi menaikkannya sendiri dan hanya satu suntingan; versinya dicetak di setiap jalan |
| Janji Python 3.11+ versus image 3.12 | Kedua angka dinyatakan di tempatnya masing-masing; installer melaporkan interpreter yang ia pakai |
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

- `eidesoul/admin/init.py:14-46` — penulisnya, mode `0600`-nya, dan aturan tidak-menimpa.
- `eidesoul/admin/templates.py:1,151,253` — sumber default tunggal yang dipakai ulang wizard.
- `eidesoul/__main__.py:587,709-714` — `_init`, subperintah `init`, dan `--provider` yang wajib.
- `eidesoul/providers/__init__.py` — nama provider dan variabel kuncinya, dipakai untuk validasi.
- `README.md:88-105`, `README.id.md:96-112` — quickstart yang jalur native ADR ini duduk di sampingnya.
- `compose.yaml` — jalur container yang mempertahankan `init` yang sama.
- `install.sh` — berkas baru di akar repo (usulan, belum dibuat).
- `pyproject.toml` — tidak berubah: installer tak menambah dependency, dan `uv` yang dipin diunduh, bukan di-vendor.

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0030-install-and-init.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/0030-install-and-init.md`.*
