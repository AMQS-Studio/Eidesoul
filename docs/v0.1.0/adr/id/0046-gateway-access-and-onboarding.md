> Terjemahan turunan — jangan sunting; sumber: 0046-gateway-access-and-onboarding.md (sha256: 9d0088183e35)

# ADR-0046: Otorisasi Gateway di Role, Onboarding Milik Gateway, dan Pilihan Start Setelah Instalasi

- **Status:** accepted
- **Tanggal:** 2026-11-05
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** mengikuti ADR-0018 (kontrak gateway), ADR-0022 (administrator terlindungi), ADR-0027 (default bersih), ADR-0030 (install dan init), ADR-0031 (struktur engine), ADR-0033 (core netral), ADR-0036 (model role), ADR-0039 (setup terminal), ADR-0040 (penamaan), dan ADR-0042 (sinkronisasi saat start).
- **Supersedes:** aturan database-menang untuk `config.yaml`, `soul.yaml`, dan `roles.yaml` di ADR-0042 §4.1; bagian lain ADR-0042 tetap berlaku.
- **Terkait:** ADR-0007 (otorisasi), ADR-0018 (kontrak gateway), ADR-0022 (administrator), ADR-0027 (distribusi), ADR-0030 (installer), ADR-0033 (core netral), ADR-0036 (model role), ADR-0039 (init), ADR-0042 (sinkronisasi), ADR-0043 (riset)
- **Implementasi:** draft — ADR ini menetapkan kontrak; belum ada kode policy role, onboarding hook, prompt start, atau ekstraksi katalog yang mendarat.
- **Catatan lokasi:** keputusan ini melintasi core role, seam gateway, dan installer, jadi ia duduk di root ADR home. Salinan Inggris ada di `docs/v0.1.0/adr/0046-gateway-access-and-onboarding.md`.

---

## 1. Konteks

Instalasi WhatsApp bisa berhasil pairing tetapi tetap terlihat kosong:

- pairing membuat `.monoagens/whatsapp.db`, tetapi proses gateway tidak dijalankan;
- history hanya diimpor oleh gateway yang berjalan, bukan oleh pairing;
- pengirim yang belum di-link berakhir sebagai `guest`;
- setelan transport WhatsApp saat ini hanya `enabled`, `session_db`, dan `backlog_seconds`;
- katalog capability meng-index tool gateway berdasarkan nama lalu mengganti deskripsi aslinya dengan `gateway:<name>`;
- installer memiliki loop pairing, sedangkan QR terminal dan sesi dimiliki gateway.

Owner sudah menetapkan arah policy:

1. otorisasi gateway berada di `roles.yaml`, bukan di blok policy gateway kedua di `config.yaml`;
2. role dengan prioritas tertinggi yang menentukan, bukan gabungan semua role;
3. `guest` default menolak semua;
4. `roles.yaml` dan `config.yaml` adalah sumber kebenaran operator, dan command yang mengubahnya menulis ke sana;
5. setelah install, operator ditanya mau start sekarang atau memasang service yang restart otomatis.

Kontrak saat ini mengatakan dokumen role di database adalah sumber kebenaran dan YAML adalah seed plus mirror (`monoagens/engine/stores/documents.py:180-233`). Itulah konflik source-of-truth yang diselesaikan ADR ini untuk dua file operator tersebut.

## 2. Decision drivers

| Driver | Konsekuensi |
| --- | --- |
| Otorisasi adalah data role | akses gateway dibaca dari role yang ter-resolve, bukan dari peta policy gateway paralel |
| Satu aturan prioritas | role dengan prioritas tertinggi menang; role bawah tidak bisa diam-diam memberi akses |
| Deny aman | policy hilang, pengirim tak dikenal, atau role yang ditolak dibuang sebelum memory dan model |
| Operasi file-first | `roles.yaml` dan `config.yaml` bisa diedit, di-diff, dan ditulis oleh command yang mengubahnya |
| Tanpa mutasi tersembunyi | gateway yang berjalan tidak pernah menulis ulang file role; hanya `init`, `gateway add`, atau doctor eksplisit yang boleh mengisi default |
| Transport tetap di luar core | core menyimpan mapping `gateways` yang opaque; hanya gateway yang menafsirkan policy-nya sendiri |
| Gateway memiliki onboarding | QR, file sesi, dan penantian pairing adalah urusan gateway; installer hanya memanggil hook |
| Lifecycle eksplisit | pairing tidak pernah menyalakan agent; langkah finish menanyakan foreground, service, atau keluar |
| Metadata capability lengkap | tool gateway menampilkan deskripsi dan parameter aslinya di katalog |
| Default bersih | kedua file tetap eksplisit; tidak ada allow implisit untuk gateway atau role baru |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkasan | Nilai |
| --- | --- | --- |
| **A. Policy per gateway di `config.yaml`** | `gateways.whatsapp.dm/groups/...` | Ditolak: menduplikasi otorisasi role dan tidak bisa membedakan guest/member dengan bersih |
| **B. Policy role di `roles.yaml`** | setiap role membawa `gateways.<name>` | **Dipilih** |
| **C. Auto-write runtime oleh gateway** | gateway mengisi blok role yang hilang saat menangani pesan | Ditolak: perubahan otorisasi tersembunyi, race, dan transport menulis role store |
| **D. Database-first dengan mirror YAML** | mempertahankan ADR-0042 apa adanya | Ditolak owner: perubahan tak terlihat dan file bukan benar-benar sumber operator |
| **E. File-first untuk semua dokumen** | membalik presedensi role, config, soul, dan semua mirror | Ditolak: migrasi lebih luas dari yang dibutuhkan; `soul.yaml` tetap di ADR-0042 |
| **F. `init` menyalakan agent** | QR, database, dan start proses di dalam init | Ditolak: init tetap penulis file deterministik, bisa headless dan dites |
| **G. Installer menyalakan agent otomatis** | tanpa prompt | Ditolak: proses foreground memblokir installer dan service adalah pilihan operator |
| **H. Hook onboarding milik gateway** | `gateway add --connect` memasang extra, enable, menulis policy, dan pairing | **Dipilih** |
| **I. Katalog mempertahankan placeholder** | hanya `gateway:<name>` | Ditolak: layar capability tidak bisa menjelaskan tool |
| **J. Auto-administrator** | pengirim WhatsApp tak dikenal menjadi administrator | Ditolak: eskalasi privilege; linking identitas harus eksplisit |

## 4. Keputusan

### 4.1 Permukaan operator file-first

Untuk `config.yaml`, `soul.yaml`, dan `roles.yaml`:

- file adalah sumber kebenaran operator;
- baris database hanyalah cache atau proyeksi;
- command yang mengubah file menulis file dulu, lalu menyegarkan cache;
- saat cache dan file berbeda ketika start, file menang;
- cache yang hilang atau rusak dibangun ulang dari file; cache tidak pernah menjadi fallback;
- definisi role dan daftar pemegang role sama-sama tinggal di `roles.yaml`; database menyimpan identitas/link dan cache turunan, bukan otoritas pemegang role;
- secret tetap di `.env`, dan perubahan yang butuh restart dilaporkan, bukan diterapkan diam-diam;
- sync lock tetap menjadi satu-satunya titik serialisasi untuk rebuild.

Ini amendemen terbatas pada ADR-0042, bukan aturan "file menang di mana-mana". Ia membuat otorisasi
gateway, setelan transport, state soul, dan keanggotaan role terlihat, bisa di-diff, dan bisa dipulihkan
dari repository atau backup. Cache database boleh dihapus kapan saja.

### 4.2 Policy gateway pada role

`Role` mendapat satu field opaque:

```yaml
gateways:
  whatsapp:
    dm: true
    groups: mention
    reply_to_bot: true
```

Core menyimpan mapping tetapi tidak menafsirkan nama gateway atau nilai policy. Gateway membaca
`role.gateways[self.name]` dan memiliki validasi.

Kosakata policy untuk sebuah gateway adalah:

| Key | Nilai | Arti |
| --- | --- | --- |
| `dm` | `true`, `false` | direct message dari role ini boleh masuk turn |
| `groups` | `none`, `mention`, `reply`, `both`, `all` | pesan grup mana yang boleh masuk turn |
| `reply_to_bot` | `true`, `false` | balasan ke pesan bot dihitung addressed |

`send` dan `chat_history` tetap izin tool; akses gateway bukan pengganti izin tool.

### 4.3 Resolusi role

Resolusi memakai urutan yang sudah ada dari `RoleRegistry.for_person`: prioritas menurun, lalu nama
role (`monoagens/engine/access/roles.py:61-74`).

1. Resolve semua role pengirim.
2. Ambil **role pertama** dalam urutan itu.
3. Baca `gateways.<gateway>` hanya dari role tersebut.
4. Kalau blok tidak ada atau tidak valid, tolak; jangan jatuh ke role yang lebih rendah.
5. Kalau pengirim tidak punya role ter-link, resolve `engine.default_role`.
6. Policy `guest` bawaan adalah deny-all; pesan yang ditolak dibuang sebelum history, memory, dan
   panggilan model, dan satu diagnostik terstruktur dicatat.

Aturan prioritas yang sama membuat perubahan role terlihat: menaikkan role bisa membuka akses, dan
menurunkannya bisa menutup akses tanpa mengedit config gateway.

### 4.4 Materialisasi policy yang hilang

File adalah sumber kebenaran, jadi policy yang hilang dimaterialisasi ke file, tidak pernah oleh runtime:

- `init` menulis blok `gateways` eksplisit untuk setiap gateway yang dipilih dan setiap role seed;
- `gateway add`/`gateway enable` menulis blok yang hilang ke `roles.yaml` dan mengimpor proyeksi;
- gateway runtime memperlakukan blok yang hilang sebagai deny dan mencatat `gateway_policy_missing`;
- `gateway doctor` melaporkan blok yang hilang, dan `gateway doctor --fix` adalah jalur perbaikan eksplisit.

Seed yang dihasilkan adalah:

| Role | `dm` | `groups` | `reply_to_bot` |
| --- | --- | --- | --- |
| `administrator` | true | all | true |
| `member` | true | mention | true |
| `guest` | false | none | false |
| role lain/baru | false | none | false |

Owner tetap bisa membuat `member` deny-all, atau membuat `guest` read-only, dengan mengedit
`roles.yaml`. Matriks ini adalah seed, bukan pengecualian runtime.

### 4.5 Onboarding milik gateway

Descriptor gateway mendeklarasikan command onboarding, extra, schema, dan capability-nya. Command-nya
generik:

```bash
monoagens gateway add whatsapp --connect
```

Ia melakukan, berurutan:

1. memasang extra yang dideklarasikan;
2. mengaktifkan gateway di `config.yaml` (file-first);
3. mematerialisasi policy role seed di `roles.yaml` (file-first);
4. memanggil fungsi `pair`/onboarding milik gateway;
5. membiarkan gateway mencetak QR dan memiliki `.monoagens/whatsapp.db`;
6. kembali tanpa menyalakan agent.

`init` tidak pernah melakukan langkah 1, 4, 5, atau 6. `install.sh` mengorkestrasi langkah tersebut
setelah database ada, tetapi tidak mem-parsing atau menggambar QR. Pemanggilan `pair_gateways` di
installer saat ini adalah bentuk sementara dari hook ini; bentuk finalnya adalah command gateway.

Pairing bukan runtime. Agent yang berjalan diperlukan untuk pesan masuk dan history sync.

### 4.6 Pilihan start setelah install

Setelah schema database, registrasi administrator, onboarding gateway, dan self-test:

```text
1) start now in this terminal
2) install and start a user service (auto restart)
3) exit
```

- Default interaktif adalah start now; tidak ada proses yang start sebelum operator menjawab.
- Instalasi non-interaktif mencetak command yang setara lalu keluar.
- Service adalah systemd user unit dengan `Restart=on-failure`, delay restart, direktori install sebagai
  `WorkingDirectory`, environment proyek, dan `python -m monoagens run`.
- Lease tetap satu-satunya penjaga single-primary; proses kedua membeku, bukan menjalankan dua heart.
- `run` foreground bukan service dan berhenti bersama terminal.

### 4.7 Deskripsi katalog capability

Tool gateway ditemukan dari deklarasi `TOOL = Handler(...)`. Katalog harus mempertahankan `description`
dan metadata parameter, bukan menggantinya dengan `gateway:<name>`. Tampilan `? details` pada
multi-select merender deskripsi penuh yang di-wrap; tampilan baris tetap ringkas.

Sumber metadata adalah modul tool gateway; katalog adalah index, bukan tabel deskripsi kedua.

### 4.8 Identitas administrator tidak pernah otomatis

Akun gateway yang pairing adalah akun agent, bukan akun pribadi operator. Pengirim yang belum di-link
tetap `guest` dan ditolak oleh policy seed. Installer boleh menawarkan linking nomor operator setelah
pairing, tetapi harus berupa jawaban eksplisit atau claim code first-contact, tidak pernah grant
administrator otomatis.

### 4.9 Reload adalah command owner yang dibawa gateway mana pun

Reload adalah command core, bukan command khusus WhatsApp. Gateway hanya mengangkut pesan; `/reload`
yang sama bekerja dari CLI, WhatsApp, atau body lain yang mengekspos command.

```text
/reload
```

Command tersedia hanya bila role dengan prioritas tertinggi pengirim mencantumkan `reload` di
`commands` (`administrator` secara default). Policy gateway normal tetap berlaku: administrator yang
policy `gateways.whatsapp`-nya menolak DM atau mention tidak pernah mencapai command. Command hanya
diterima di private thread, jadi grup tidak bisa memicu swap konfigurasi.

Pada invokasi, primary:

1. mengambil config lock;
2. membaca `config.yaml`, `soul.yaml`, dan `roles.yaml` dari disk;
3. memvalidasi semua file sekaligus;
4. membangun ulang cache dan menaikkan satu generation;
5. menukar snapshot runtime secara atomik;
6. membalas receipt singkat (section yang berubah dan hal yang masih butuh restart).

Kalau validasi gagal, tidak ada yang ditukar dan runtime lama tetap jalan. Balasan menyebut file yang
gagal dan error validasinya, tidak pernah secret. Instance standby menolak; `/reload` tidak mengambil
lease. Edit file tidak berlaku sampai command ini, restart, atau reload startup eksplisit.

Perubahan `.env`, database URL, workspace, runtime sandbox, dan nilai host-level lain dilaporkan sebagai
restart-required; command tidak berpura-pura semuanya hot-reloadable.

## 5. Konfigurasi

### 5.1 Setelan gateway hanya transport

```yaml
gateways:
  whatsapp:
    enabled: true
    session_db: .monoagens/whatsapp.db
    backlog_seconds: 600
```

Tidak ada key `dm`, `groups`, atau `reply_to_bot` di sini. Itu otorisasi dan tinggal di `roles.yaml`.

### 5.2 Otorisasi role

```yaml
roles:
  administrator:
    gateways:
      whatsapp:
        dm: true
        groups: all
        reply_to_bot: true

  member:
    gateways:
      whatsapp:
        dm: true
        groups: mention
        reply_to_bot: true

  guest:
    gateways:
      whatsapp:
        dm: false
        groups: none
        reply_to_bot: false
```

Contoh ini menghilangkan field `priority` yang sudah ada; resolver role memakainya. Role dengan
prioritas tertinggi dari pengirim yang otoritatif. Kalau seseorang memiliki `member` dan
`administrator`, `administrator` menang berdasarkan prioritas.

### 5.3 Rekonsiliasi file-first

```text
startup:
  read config.yaml + soul.yaml + roles.yaml
  validate all three
  build a cache keyed by content hash
  load runtime from the files
  keep the old runtime if validation fails

/reload:
  lock
  read and validate the same files
  rebuild cache, bump generation
  swap runtime atomically
  report restart-required host changes
```

Command yang mengubah file menulis file dulu, lalu menyegarkan cache. Cache yang hilang atau rusak
dibangun ulang; cache tidak pernah menjadi fallback. Tidak ada gateway yang menulis file saat runtime.

### 5.4 Konfigurasi command `/reload`

`reload` hanyalah command core untuk keperluan izin:

```yaml
roles:
  administrator:
    commands:
      - "*"
    gateways:
      whatsapp:
        dm: true
        groups: all
        reply_to_bot: true
```

Role yang tidak boleh reload cukup menghilangkan `reload` dari `commands`. Command hanya untuk
private thread dan tetap tunduk pada policy akses gateway. Balasannya memakai katalog bahasa normal;
tidak ada string chat-visible yang di-hardcode di command.

## 6. Metrik dan gate

| Gate | Pemeriksaan |
| --- | --- |
| Prioritas | orang dengan `member` dan `administrator` memakai policy administrator |
| Deny default | pengirim tak ter-link resolve ke `guest` dan tidak menghasilkan panggilan model atau tulisan memory |
| Policy hilang | role tanpa `gateways.<name>` ditolak dan mengeluarkan satu diagnostik |
| File-first | mengedit `roles.yaml` lalu restart atau `/reload` mengubah akses tanpa command import terpisah |
| Tanpa mutasi runtime | pesan yang ditolak maupun diterima meninggalkan `roles.yaml` byte-identik |
| Reload | `/reload` dari administrator memvalidasi semua file, menukar runtime, dan melaporkan perubahan yang butuh restart; file invalid mempertahankan runtime lama |
| Cache | menghapus cache lalu restart membangunnya ulang dari file; cache rusak tidak pernah dipakai sebagai otoritas |
| Kelengkapan seed | `init` menulis blok policy untuk setiap role seed saat gateway dipilih |
| Onboarding | `gateway add --connect` memasang extra, enable gateway, menulis policy, dan memanggil fungsi onboarding gateway tepat sekali |
| Pilihan start | installer mencetak tiga pilihan; jalur service menjalankan satu proses di bawah lease; non-interaktif mencetak command |
| Katalog | setiap tool gateway yang di-index punya deskripsi tidak kosong; `? details` menampilkannya |
| Netralitas | tidak ada nama gateway atau cek transport di `monoagens/engine/` |

## 7. Acceptance criteria

- [ ] `Role` menerima mapping `gateways` tanpa mengetahui nama gateway.
- [ ] Role pertama dari `for_person` adalah satu-satunya sumber policy; role lebih rendah tidak bisa memberi akses.
- [ ] Policy seed `guest` menolak DM, grup, dan reply-to-bot.
- [ ] Pesan yang ditolak dibuang sebelum history, memory, LLM, dan dispatch tool; diagnostik menyebut role dan gateway.
- [ ] `init --capabilities` menulis policy gateway seed ke `roles.yaml`.
- [ ] `gateway add --connect` menulis policy yang hilang ke `roles.yaml` dan memanggil onboarding gateway.
- [ ] Policy yang hilang saat runtime ditolak dan dilaporkan `gateway doctor`; tidak ditulis otomatis oleh gateway yang berjalan.
- [ ] Installer tidak menggambar QR; gateway yang mencetaknya.
- [ ] Setelah install sukses, operator melihat pilihan start-now, user-service, dan exit.
- [ ] User service restart saat gagal dan tidak pernah menjalankan dua primary.
- [ ] Katalog capability menampilkan deskripsi asli `chat_history`, `send`, dan `send_to`.
- [ ] `config.yaml`, `soul.yaml`, dan `roles.yaml` adalah target penulisan untuk perubahan operator; cache DB boleh dihapus dan dibangun ulang.
- [ ] `roles.yaml` memuat definisi role dan daftar pemegang role; DB hanya meng-cache keduanya.
- [ ] `/reload` adalah command core yang diberikan lewat `commands: ["reload"]`, hanya private thread, dan tunduk pada policy gateway.
- [ ] `/reload` melaporkan sukses dan field yang butuh restart; YAML invalid mempertahankan runtime lama.
- [ ] Paragraf otoritas file/DB di `AGENTS.md` diamendemen pada commit implementasi, menunjuk ke ADR ini.
- [ ] Gate ADR yang ada dan full test suite lolos.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
| --- | --- |
| Membalik presedensi DB membuat dua penulis | satu jalur import file-first dengan advisory lock; command menulis file dulu; proyeksi diturunkan |
| Deny-all `guest` membuat install baru tampak rusak | installer menjelaskan akses, menawarkan linking nomor operator, dan `gateway doctor` melaporkan alasannya |
| Policy hilang diam-diam mengunci orang | diagnostik startup, `gateway doctor`, dan materialisasi seed eksplisit saat init/enable |
| Auto-materialisasi mengejutkan operator | hanya `init` dan `gateway add`/`doctor --fix` eksplisit yang menulis file; runtime tidak pernah |
| Auto-admin dari pairing | tidak pernah auto-link; hanya nomor eksplisit atau claim code |
| QR terekspos di grup | onboarding tetap di terminal; tidak pernah dikirim ke grup |
| Primary ganda setelah install service | lease tetap otoritatif; service memakai `python -m monoagens run`; tanpa `--takeover` |
| Nama gateway bocor ke core | core memperlakukan `gateways` sebagai data opaque; gateway memvalidasi bloknya sendiri |
| Deskripsi tool berbeda dari handler | metadata diekstrak dari deklarasi handler; tidak ada tabel deskripsi kedua |

## 9. Interaksi dengan ADR lain

- **ADR-0007**: penolakan akses melengkapi kelas approval; pesan gateway yang ditolak tidak pernah mencapai action.
- **ADR-0018**: kontrak gateway tetap; onboarding dan otorisasi adalah data milik gateway, bukan cabang core.
- **ADR-0022**: `administrator` tetap `tools: "*"`, tetapi policy gateway-nya tetap eksplisit; auto-admin tetap dilarang.
- **ADR-0027**: gateway tetap default bersih dan bisa dilepas; tidak ada import gateway di engine.
- **ADR-0030**: installer mendapat pilihan start dan mendelegasikan onboarding; `init` tetap file-only.
- **ADR-0031**: kode akses role tetap di `access/`; parsing policy gateway tetap di gateway; tidak ada modul core baru tanpa alasan.
- **ADR-0033**: tidak ada nama gateway atau transport di core; data role opaque.
- **ADR-0036**: bundel role dan prioritas tetap satu model role.
- **ADR-0039**: init tetap deterministik; pemilihan capability menulis file.
- **ADR-0042**: file menang untuk `config.yaml`, `soul.yaml`, dan `roles.yaml`; sisa sinkronisasi dokumen tetap.
- **ADR-0045**: konten `soul.yaml` tetap mengikuti kontrak soul; ADR ini hanya mengubah presedensi sumber kebenarannya.

## 10. Referensi kode

- `monoagens/engine/access/roles.py:28-39` — dataclass `Role` saat ini; field `gateways` adalah **usulan**.
- `monoagens/engine/access/roles.py:61-74` — urutan prioritas untuk satu orang.
- `monoagens/engine/access/permissions.py:51-56` — otorisasi tool/command tetap terpisah dari akses gateway.
- `monoagens/engine/config.py:1163-1178` — opsi transport `GatewaySettings` saat ini.
- `monoagens/engine/stores/documents.py:180-233` — sinkronisasi DB-menang saat ini; ADR-0042 diamendemen untuk dua file.
- `monoagens/gateways/whatsapp/config.py:9-24` — setelan WhatsApp hanya transport.
- `monoagens/gateways/whatsapp/ingress.py:51-60,194-198` — hint mention/reply; policy gateway mengeceknya sebelum turn.
- `monoagens/engine/turn/decision.py:269-284` — pemaksaan direct/mention/reply saat ini; akses harus terjadi sebelum titik ini.
- `monoagens/gateways/__init__.py:102-166` — index gateway, dispatch CLI, dan loader; metadata onboarding adalah **usulan**.
- `monoagens/admin/gateway.py:30-79` — CLI enable/add/disable/pair saat ini; materialisasi policy file-first adalah **usulan**.
- `monoagens/engine/turn/commands.py` — dispatch command; `/reload` adalah **usulan**.
- `monoagens/engine/commands/` — modul baru `reload.py` untuk command core adalah **usulan**.
- `monoagens/engine/stores/documents.py:235-300` — sinkronisasi saat ini; rebuild cache dan loading file-first adalah **usulan**.
- `install.sh:951-986` — `pair_gateways` saat ini; memindahkan kepemilikan QR ke `gateway add --connect` adalah **usulan**.
- `install.sh:1626-1646` — output finish saat ini; prompt start/service adalah **usulan**.
- `monoagens/admin/wizard.py:295-379` — pemilihan capability; pembuatan policy role seed adalah **usulan**.
- `monoagens/admin/catalog.py:23-50` — placeholder `gateway:<name>` saat ini; ekstraksi deskripsi asli adalah **usulan**.
- `monoagens/__main__.py:209-230` — dispatch gateway saat ini; onboarding berbasis descriptor adalah **usulan**.
- `tests/test_install.py`, `tests/test_catalog.py` — test saat ini yang akan diperluas untuk gate di atas.

---

*ADR ini tinggal di `docs/v0.1.0/adr/0046-gateway-access-and-onboarding.md`.*
