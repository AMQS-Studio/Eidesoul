> Terjemahan turunan — jangan sunting; sumber: 0039-setup-terminal-and-mark.md (sha256: 1284df8459c8)

# ADR-0039: Init sebagai Antarmuka Terminal Kelas Satu, dan Mark Eidos

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** mengisi slot logo dan utang interaksi dari ADR-0030; mengikuti ADR-0009 (ADR home), ADR-0019 (observability), ADR-0022 (administrator terlindungi), ADR-0027 (distribusi), ADR-0033 (core netral)
- **Supersedes:** —
- **Terkait:** ADR-0030 (install dan init), ADR-0031 (struktur modul)
- **Implementasi:** implemented — `admin/terminal.py` (curses plus fallback bernomor), `admin/wizard.py` (mesin langkah, location, credential, picker provider/model, review), `admin/brand.py` (lockup Eidos), `admin/provider_catalog.py` (label operator), flag `--api-key-file`, `tests/test_init.py`, dan mark SVG di `docs/brand/`.
- **Catatan lokasi:** setup adalah permukaan host-wide, jadi ADR ini duduk di root ADR home. Salinan Indonesia di `docs/v0.1.0/adr/id/0039-setup-terminal-and-mark.md`.

---

## 1. Konteks

ADR-0030 membuat `eidesoul init` interaktif dan meninggalkan slot logo (`[logo]`) untuk mark yang belum ada. Wizard yang dikirim adalah urutan prompt baris: tanpa panah, tanpa pencarian, tanpa navigasi mundur, dan tanpa langkah lokasi yang terlihat. `eidesoul init` dari direktori kerja mana pun karena itu bertanya provider dan model sebelum operator mengonfirmasi di mana file akan mendarat.

Arahan operator eksplisit: pelajari setup Hermes, lalu bangun interaksi dan identitas sendiri, bukan menyalin frame atau mark-nya. Defect-nya konkret:

| Fakta | Bukti |
| --- | --- |
| Terminal hanya baris | `admin/init.py` memakai `_ask()` dan pilihan bernomor untuk setiap langkah |
| Location implisit | `--dir` default `.` dan tidak ada langkah yang menampilkan target hasil resolve |
| Logo masih placeholder | `LOGO_SLOT = "[logo]"` di `admin/init.py` |
| ID provider tanpa label operator | picker mencetak ID mentah seperti `opencode` |
| Wizard tidak bisa menerima secret | `.env` ditulis kosong untuk diisi operator nanti |
| Referensi punya loop lebih kaya | `hermes_cli/setup.py` dan `curses_ui.py` memakai radiolist, checklist, fuzzy `/`, navigasi Escape/Left, dan step runner |

Hermes berlisensi MIT dan tetap hanya referensi disiplin interaksi. Tidak ada kode, glyph, wordmark, atau aset Hermes yang disalin; mark Eidos orisinal.

## 2. Decision drivers

| Driver | Konsekuensi |
| --- | --- |
| Location sebelum kredensial | Direktori target adalah keputusan pertama dan tampil di Review serta Receipt |
| Keyboard dulu, pipe aman | TTY POSIX mendapat curses; pipe, `TERM=dumb`, atau test mendapat baris bernomor |
| Keputusan bisa dibalik | Left kembali ke langkah sebelumnya dengan jawaban lama; Escape membatalkan tanpa menulis |
| Secret tetap rahasia | Key dimasker, hanya ditulis ke `.env` mode `0600`, dan tidak pernah dicetak di Review atau log |
| Satu sumber data | `admin/templates.py` tetap satu-satunya sumber default; interaksi tidak menciptakan nilai |
| Identitas sendiri | Mark Eidos adalah kode dan SVG, bukan logo pinjaman |
| UI bisa diuji | Logika frame/key terpisah dari penulis file; test tidak perlu terminal curses nyata |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkasan | Nilai |
| --- | --- | --- |
| **A. Pertahankan prompt baris dan tambah warna** | bungkus output ADR-0030 dengan ANSI | Ditolak: tidak menambah navigasi, pencarian, atau keputusan lokasi |
| **B. Salin UI curses Hermes** | pindahkan menu dan langkahnya | Ditolak: kosakata dan layout produk lain; surface dependensi lebih besar dari yang dibutuhkan |
| **C. Curses di TTY, fallback bernomor di tempat lain** | menu stdlib-only; jalur baris lama tetap teruji | **Dipilih** |
| **D. Tambah framework TUI penuh** | Textual atau prompt_toolkit | Ditolak: surface setup saja tidak membenarkan dependensi runtime baru |
| **E. Jadikan location default tersembunyi** | pertahankan `--dir` saja | Ditolak: operator tidak bisa mengaudit di mana secret dan config mendarat |

## 4. Keputusan

### 4.1 Location adalah langkah pertama

Rail-nya `location · provider · credential · model · database · identity · voice layer`, lalu Review. Langkah location menampilkan direktori hasil resolve dan status `config.yaml`, `roles.yaml`, `soul.yaml`, `.env`, dan `.eidesoul/`. Ia menawarkan `new`, `existing`, atau `keep` / `change` / `replace` lewat layar keputusan yang sudah ada. Path yang merupakan file, atau yang ancestor terdekatnya tidak writable, ditolak di langkah itu. Tidak ada direktori yang dibuat sebelum Apply.

`--dir PATH` mengisi jawaban; `--non-interactive` memakainya tanpa prompt. Path hasil resolve selalu tampil di Review dan Receipt.

### 4.2 Interaksi terminal

| Key | Aksi |
| --- | --- |
| `↑` / `↓`, `j` / `k` | pindahkan kursor |
| `Enter` | pilih |
| `/` | mulai filter case-insensitive atas label, detail, dan value |
| `Backspace` | edit filter |
| `←` | kembali ke langkah sebelumnya; jawaban dipertahankan |
| `Esc` | bersihkan filter aktif, jika tidak batalkan wizard |
| `q` / `Ctrl-C` | batalkan tanpa menulis |

Lapisan curses memakai standard library saja dan hanya jalan bila stdin dan stdout keduanya TTY, `TERM` usable, dan platform POSIX. Di tempat lain fallback baris bernomor menerima angka, label persis, atau value persis, dan jawaban kosong mempertahankan default.

### 4.3 Langkah dan operator

1. **Provider** — label operator dari `admin/provider_catalog.py`; ID dan variabel key tetap di `eidesoul/providers`.
2. **Credential** — input termask untuk `api_key_env` provider; kosong meninggalkan `.env` untuk diisi nanti. `--api-key-file PATH` adalah padanan headless, jadi key tidak perlu muncul di shell history.
3. **Model** — ID yang disajikan provider saat reachable, bisa dicari; entri manual saat daftar kosong atau tidak tersedia.
4. **Database** — validasi URL Postgres yang ada; password tidak dirender di Review.
5. **Identity** — label administrator plus language, locale, dan timezone dari default yang ada.
6. **Voice layer** — pertahankan emotion, humanize, dan character, atau sebut bagian yang dimatikan.

### 4.4 Review, secret, dan penulisan

Review menampilkan location, setiap file dan mode, provider, base URL, variabel key, model, nama env database, label administrator, dan status voice. Ia tidak pernah menampilkan nilai key atau password database. Apply tetap satu-satunya jalur ke `write_files()`; Cancel, Escape, Ctrl-C, dan penulisan yang terinterupsi tidak meninggalkan file baru. File yang sudah ada mempertahankan keputusan `keep` / `change` / `replace` dari ADR-0030. `.env` tetap `0600`.

### 4.5 Mark Eidos

Mark adalah cincin terbuka dengan titik di tengah: forma mengelilingi soul, dengan celah sebagai agency. Wordmark `EIDESOUL`; tagline `a soul engine you run on your own machine`. Terminal mencetak `◈` plus lockup; `--no-color`, `TERM=dumb`, dan output non-TTY memakai bentuk polos. Aset SVG adalah `docs/brand/eidesoul-mark.svg` dan `docs/brand/eidesoul-lockup.svg`; mark tidak pernah menjadi config key dan tidak mengubah lebar layout.

### 4.6 Batas modul

`admin/init.py` menyimpan default, validasi, `build()`, dan penulis atomik. `admin/wizard.py` menyimpan mesin langkah, prompt, review, dan navigasi. `admin/terminal.py` memiliki curses dan fallback baris. `admin/brand.py` memiliki lockup. `admin/provider_catalog.py` memiliki label operator. Tidak ada modul engine yang mengimpor pustaka terminal.

## 5. Konfigurasi

| Flag | Arti |
| --- | --- |
| `--dir PATH` | isi langkah Location; default `.` |
| `--provider NAME` | jawab Provider |
| `--api-key-file PATH` | baca key dari file; jangan pernah nilai di command line |
| `--model ID` | jawab Model |
| `--database-url URL` | jawab Database dan tulis ke `.env` |
| `--label TEXT` | jawab Identity |
| `--voice-layer on\|off` | jawab Voice |
| `--language`, `--locale`, `--timezone` | jawab default terkait |
| `--no-color` | output polos; juga lewat `NO_COLOR` |
| `--non-interactive` | jangan prompt; gagal tertutup saat flag wajib hilang |

Tidak ada key yang ditambahkan ke `config.yaml`. Nilai API key hanya hidup di `.env`.

## 6. Metrik dan gate

| Metrik | Target |
| --- | ---: |
| Prompt saat stdin bukan terminal | 0 (gagal dengan daftar flag) |
| Secret tercetak di frame, Review, Receipt, atau log | 0 |
| File tersisa oleh Escape/Ctrl-C sebelum Apply | 0 |
| Langkah tanpa padanan headless | 0 |
| ID provider dirender tanpa label operator di picker TTY | 0 |
| Placeholder logo di wizard yang dikirim | 0 |
| Dependensi runtime baru untuk UI terminal | 0 |

## 7. Kriteria penerimaan

- [ ] `eidesoul init` di TTY POSIX menampilkan lockup Eidos dan rail location-first.
- [ ] Picker provider dan model bergerak dengan panah, memfilter dengan `/`, memilih dengan Enter, dan kembali dengan Left.
- [ ] Escape dan Ctrl-C tidak meninggalkan file baru atau file parsial.
- [ ] Location kustom menulis keempat host file dan `.eidesoul/workspace` di sana.
- [ ] Path yang merupakan file, atau ancestor terdekatnya tidak writable, gagal di Location.
- [ ] Review dan Receipt menampilkan location; Review tidak pernah menampilkan API key atau password database.
- [ ] Credential termask hanya mendarat di `.env` mode `0600`; `--api-key-file` melakukan hal yang sama secara headless.
- [ ] Non-TTY, `TERM=dumb`, dan `--no-color` menghasilkan fallback bernomor yang terbaca.
- [ ] File yang sudah ada tetap menawarkan `keep` / `change` / `replace`.
- [ ] `pytest -q`, `ruff`, `pyright`, dan gate ADR lolos.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
| --- | --- |
| Curses rusak di terminal sempit atau dumb | guard TTY/TERM dan fallback bernomor; jalur baris tidak bergantung lebar |
| Location tersembunyi menulis ke direktori salah | Location adalah langkah pertama, menampilkan path hasil resolve, dan diulang di Review/Receipt |
| Secret bocor lewat frame atau log | input termask, tanpa nilai di Review, `.env` `0600`; test memastikan marker tidak ada di stdout |
| Pemisahan menduplikasi validasi | `admin/init.py` tetap satu-satunya validator dan penulis; `wizard.py` memanggilnya |
| Mark menjadi permukaan konfigurasi | Hanya kode dan SVG; tanpa key dan tanpa ketergantungan layout |
| Panel Web masa depan butuh alur sama | `--non-interactive` dan batas modul memungkinkan panel menggerakkan `init` tanpa lapisan curses |

## 9. Interaksi dengan ADR lain

- **ADR-0030**: ADR ini mengisi slot logonya dan mengganti interaksi linear; penulis, aturan never-overwrite, mode `.env`, dan aturan headless tetap.
- **ADR-0022**: Identity tetap hanya meminta label administrator; aturan role terlindungi tidak berpindah.
- **ADR-0019**: lapisan terminal tidak mencatat secret dan isi frame.
- **ADR-0031**: pemisahan menjaga kedua file di bawah cap 800 baris (`init.py` 442, `wizard.py` 475).
- **ADR-0033**: label provider hidup di `admin/`, bukan di engine netral; engine tetap tanpa kosakata transport atau vendor.

## 10. Referensi kode

- `eidesoul/admin/init.py` — `Answers`, validasi, `build()`, `write_files()` atomik, `banner()`.
- `eidesoul/admin/wizard.py` — location, provider, credential, model, database, identity, voice, review, navigasi, dan fallback bernomor.
- `eidesoul/admin/terminal.py` — radiolist curses, filter `/`, Back/Cancel, secret termask.
- `eidesoul/admin/brand.py` — lockup Eidos dan fallback ASCII.
- `eidesoul/admin/provider_catalog.py` — label operator dan detail variabel key.
- `eidesoul/__main__.py` — `--api-key-file` dan dispatch `init`.
- `docs/brand/eidesoul-mark.svg`, `docs/brand/eidesoul-lockup.svg` — mark dan lockup.
- `tests/test_init.py` — location, credential, lockup, filter, review, cancel, dan fallback.

---

*ADR ini tinggal di `docs/v0.1.0/adr/0039-setup-terminal-and-mark.md`.*
