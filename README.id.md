# Eidesoul

mesin jiwa yang menetap: satu identitas, satu memori, tubuh apa pun

[English](README.md) | [Bahasa Indonesia](README.id.md)

[Arsitektur](#architecture) · [Mulai cepat](#quickstart) · [soul.yaml](#soulyaml) ·
[Pustaka karakter](#character-library) · [Gateway yang didukung](#supported-gateways) ·
[Peran dan izin](#roles-and-permissions) · [Keamanan](#safety) · [Kredit](#credits)

<a id="what-it-is"></a>
## Apa ini

Eidesoul adalah mesin jiwa (soul engine). Ini bukan agent, bukan templat asisten, dan bukan
kerangka kerja agent otonom.

Sebuah mesin jiwa menyimpan satu identitas dan satu memori, lalu membiarkannya menghuni tubuh apa
pun: terminal, aplikasi chat, perangkat, robot. Engine memegang giliran — ia membaca apa yang
datang, mengingat apa yang ia tahu, memutuskan, lalu bicara — dan tidak lebih dari itu. Semua yang
membawa pesan masuk dan keluar hanyalah tambahan di sekeliling giliran tersebut.

Asisten adalah satu peran di antara peran pendamping, penasihat, dan perwujudan robot. Jiwa
menyatakan peran aktif dan peran yang bisa ia ambil di `soul.yaml`; identitas, memori, dan suara
yang sama ikut berpindah ke semuanya. Mengganti peran mengubah apa yang boleh dilakukan jiwa, bukan
siapa dirinya.

<a id="architecture"></a>
## Arsitektur

Tiga lapisan, satu arah ketergantungan.

- **Core engine** — giliran plus layanan yang dibutuhkannya: penyusunan prompt, katalog bahasa,
  memori semantik dan privasi, affect dan gaya, klien LLM, serta eksekusi tool. Core tidak pernah
  mengimpor gateway, pustaka transport, atau SDK vendor secara eager.
- **Kontrak gateway** — setiap transport mengimplementasikan antarmuka kecil yang sama:
  menormalkan pesan masuk menjadi satu amplop, menyerahkannya ke engine, lalu mengantarkan balasan.
  Gateway adalah plugin. Mereka ditemukan berdasarkan nama, dan satu deployment hanya mengaktifkan
  yang diinginkannya.
- **State** — satu database menyimpan satu jiwa: transkrip per percakapan, memori semantik dengan
  tingkat privasi, affect, approval, dan lease primary tunggal. Berkas host (`config.yaml`,
  `roles.yaml`, `soul.yaml`) adalah seed sekaligus cermin dari apa yang disimpan database.

```mermaid
flowchart LR
  GA[Gateway A<br/>transport chat] -->|amplop masuk| CORE
  GB[Gateway B<br/>terminal atau REPL lokal] -->|amplop masuk| CORE
  GC[Gateway C<br/>robot atau tubuh khusus] -->|amplop masuk| CORE

  subgraph CORE[Core engine]
    TURN[Giliran<br/>konteks - ingat - putuskan - balas]
    TURN --> PROMPT[Prompt + katalog bahasa]
    TURN --> MEM[Memori semantik + privasi]
    TURN --> AFFECT[Affect, gaya, karakter]
    TURN --> TOOLS[Tool<br/>kemampuan yang bisa dilepas]
    TURN --> LLM[Klien LLM]
  end

  MEM <--> STORE[(Postgres + pgvector)]
  PROMPT <--> STORE
  LEASE[Lease primary tunggal] --- TURN

  CORE -->|balasan| GA
  CORE -->|balasan| GB
  CORE -->|balasan| GC

  OWNED[Tool milik gateway] -.diberikan lewat peran.-> TOOLS
```

Lease adalah cara satu proses tetap menjadi primary: instance tanpa lease tetap beku dan standby,
sehingga dua proses tidak pernah bicara dengan mulut yang sama. Semua yang diketahui jiwa
tersimpan, jadi restart, deployment yang dipindah, atau tubuh yang berbeda melanjutkan jiwa yang
sama, bukan memulai yang baru.

<a id="tools-are-capabilities"></a>
## Tool adalah kemampuan

Tool adalah kemampuan yang bisa diberikan kepada jiwa, bukan tubuh yang tetap. Tubuh menentukan
kemampuan mana yang ada; peran menentukan mana yang boleh dipakai jiwa di tubuh itu.

Enam keluarga inti hanyalah contoh, bukan taksonomi yang harus dipenuhi: `shell`, `read`, `write`,
`web_search`, `sandbox`, dan `mcp`. Itu mencakup kasus umum — menjalankan perintah, membaca berkas,
mencari di web, memanggil server MCP — dan tidak lebih.

Robot, perangkat, atau gateway khusus mengekspos aksi khas tubuhnya sebagai modul tool milik
gateway, berdampingan dengan gateway yang memberi makna pada aksi itu. Kamera, motor, printer, atau
bus otomasi rumah adalah milik perwujudan, bukan milik engine. Peran memberikan tool tersebut secara
eksplisit, jadi peran pendamping di ponsel tidak pernah mewarisi kendali sebuah robot.

Engine tidak pernah meng-hardcode tubuh dan tidak pernah meng-hardcode loop agent. Tidak ada planner
yang perlu dikonfigurasi, tidak ada task graph yang diluncurkan, tidak ada kawanan di latar
belakang: satu jiwa, satu giliran, dan kemampuan yang dipegang saat itu.

<a id="quickstart"></a>
## Mulai cepat

Kebutuhan: Python 3.12, Postgres 14+ dengan `pgvector`, dan endpoint LLM yang kompatibel dengan
OpenAI. Image container sudah memakai Python 3.12; menjalankan dari source di 3.11+ juga bisa.

```bash
# 1. Satu database untuk satu jiwa
cp compose.example.yaml compose.yaml          # isi password yang sebenarnya
podman compose -f compose.yaml up -d postgres

# 2. Scaffold config.yaml, roles.yaml, soul.yaml, dan .env
eidesoul init

# 3. Jadikan ini jiwa Anda, bukan jiwa default
$EDITOR soul.yaml

# 4. Arahkan ke database dan model
#    .env:
#      EIDESOUL_DATABASE_URL=postgresql+psycopg://eidesoul:CHANGE_ME@127.0.0.1:5432/eidesoul
#      OPENCODE_GO_API_KEY=...

# 5. Buat skema dan seed berkas host ke dalamnya
eidesoul db upgrade

# 6. Jalankan gateway yang diaktifkan di config.yaml
eidesoul run

# REPL lokal terhadap jiwa dan database yang sama; mengambil lease
eidesoul console --takeover
```

`eidesoul run` memuat dokumen, mengambil lease primary, lalu menjalankan gateway yang aktif.
Instance kedua tetap standby dan beku sampai mengambil alih lewat lease, jadi
`eidesoul console --takeover` adalah cara resmi berbicara manual dengan deployment yang sedang
berjalan.

`eidesoul db check` mencetak ringkasan skema dan isi; `eidesoul selftest` menguji store, tool, dan
LLM sekali tanpa menjalankan gateway.

Pasang extra opsional yang benar-benar dipakai, atau tidak sama sekali:

| Extra | Membawa | Dibutuhkan untuk |
|---|---|---|
| `memory` | Mem0 + `sentence-transformers` | memori semantik dan pengambilan pack |
| `vibe` | torch + transformers | sinyal affect dari teks |
| `whatsapp` | Neonize + segno | gateway WhatsApp |
| `google` | Google API client, google-auth, httpx | tool Google Workspace |

```bash
uv sync --extra memory --extra vibe
```

Tanpa `memory`, jiwa tetap bicara; fitur yang bergantung pada memori menurun dan melaporkan
alasannya. Setiap extra bisa dilepas: tidak ada bagian core yang mengimpornya secara eager.

<a id="soulyaml"></a>
## soul.yaml

Satu database adalah satu jiwa, dan `soul.yaml` adalah tempat jiwa itu dituliskan. Semua kunci
kecuali `name` opsional; kunci yang tidak dikenal diabaikan. Setelah `db upgrade` pertama, jiwa yang
tersimpan di database menjadi acuan dan `soul.yaml` adalah seed sekaligus cerminnya — ubah berkasnya,
jalankan `eidesoul config import` untuk memasukkan perubahan, atau `eidesoul config export` untuk
menuliskan cerminnya kembali.

```yaml
name: Eidesoul                  # wajib; jika kosong jatuh ke "Eidesoul"
description: ""                 # satu baris, muncul di blok persona
language: id                    # katalog terpasang: id | en
pronouns: aku
gender: unspecified             # male | female | nonbinary | unspecified
role: assistant                 # peran aktif
roles: [assistant, companion, advisor]   # peran yang boleh dihuni jiwa ini
locale: id-ID                   # locale konteks untuk prompt dan currents
background: ""                  # teks bebas: asal-usul jiwa ini
persona: ""                     # markdown bebas: kepribadian, suara, nilai
style:
  patterns: []                  # id pack pattern dari eidesoul/library/patterns/
  facts: []                     # id pack fakta dari eidesoul/library/facts/
  registers: []                 # allow-list register opsional; kosong = default pack
  max_patterns: 0               # gerakan pattern per giliran, 0..6
  max_facts: 0                  # fakta per giliran, 0..6
examples: []                    # opsional [{user: "...", soul: "..."}]
```

Aturan yang ditegakkan loader:

- `name` harus tidak kosong, jika tidak dipakai nilai default.
- `gender` harus salah satu dari `male`, `female`, `nonbinary`, atau `unspecified`; nilai lain
  jatuh ke `unspecified` dengan peringatan.
- `roles` tidak pernah kosong; jika `role` tidak ada di `roles`, ia ditambahkan di depan.
- Id pack dan register harus cocok dengan `^[a-z0-9][a-z0-9-]{0,63}$`; id tidak valid adalah error,
  bukan dilewati diam-diam.
- `max_patterns` dan `max_facts` dijepit ke `0..6`.
- `persona` dirender dengan urutan tetap: nama, deskripsi, peran aktif, peran tersedia, locale,
  background, persona bebas, lalu contoh dialog. Bagian kosong dihilangkan.

Peran aktif dan pack gaya adalah dua tombol yang membentuk satu giliran. Peran aktif menentukan izin
dan sikap kerja; pack gaya menentukan bagaimana jiwa terdengar dan seberapa banyak data karakter
yang boleh dipakai per giliran. Jiwa tanpa pack tidak meminjam gaya bahasa daerah dan tidak memakai
fakta — hanya `persona` dan `background` miliknya sendiri.

Contoh singkat:

```yaml
name: Eidesoul
language: id
role: companion
roles: [assistant, companion]
locale: id-ID
background: |
  Tumbuh di sekitar pelabuhan yang ramai dan masih berpikir dengan cerita dermaga.
persona: |
  ## Kepribadian
  Lugas, kering, hangat setelah kepercayaan terbentuk.
style:
  patterns: [jawatimur]
  facts: [indonesia]
  max_patterns: 3
  max_facts: 2
examples:
  - user: "capek banget hari ini"
    soul: "sini dulu, duduk"
```

<a id="character-library"></a>
## Pustaka karakter

Data karakter adalah pustaka yang dipilih jiwa, bukan sesuatu yang dikirim engine lewat suaranya.
Tidak ada yang dipilih secara default: `style` kosong berarti tanpa pattern dan tanpa fakta.

Pack berada di bawah `eidesoul/library/`:

- `patterns/<pack>.json` — gerakan bersyarat ("jika begini, maka begitu") yang ditandai register,
  target gender, serta gate toksisitas dan konten dewasa. Satu pack membawa `version`, `model`,
  `language`, sebuah `lexicon` token percakapan, dan baris `patterns`.
- `facts/<pack>.json` — fakta pendek yang berdasar, masing-masing dengan setidaknya satu URL
  sumber. Satu pack membawa `version`, `model`, `language`, dan baris `facts`.

Dua pack disertakan sebagai contoh kerja: `jawatimur` (gerakan percakapan Jawa Timur) dan
`indonesia` (fakta Indonesia). Pilih keduanya, abaikan, atau tulis sendiri:

```yaml
style:
  patterns: [jawatimur]
  facts: [indonesia]
  registers: [serious, laughter]   # opsional: hanya register ini
  max_patterns: 3
  max_facts: 2
```

Seleksi bersifat deterministik per giliran: engine membaca pesan terakhir, menurunkan register
ruangan, lalu memilih paling banyak `max_patterns` gerakan dan `max_facts` fakta. Hit kata kunci
didahulukan, lalu kemiripan kosinus terhadap vektor yang sudah dihitung. Pack yang hilang, tidak
terbaca, atau namanya di luar pola id tidak menyumbang apa pun dan mencatat peringatan alih-alih
menggagalkan giliran.

Tidak ada transkrip mentah yang dikirim. Baris `say` di pack pattern adalah contoh yang sudah
disetujui, bukan pesan pribadi yang disalin, dan baris fakta membawa sumber, bukan percakapan.

Ubah pack lewat skrip, jangan manual:

```bash
python scripts/patterns.py --pack jawatimur --check     # skema, gate, duplikat, keterjangkauan
python scripts/patterns.py --pack jawatimur --embed     # bangun ulang vektor pack
python scripts/fakta.py --pack indonesia --check        # skema dan vektor
python scripts/fakta.py --pack indonesia --embed
python scripts/grow.py --pack jawatimur --patterns      # usulkan baris baru dari korpus
```

`--embed` harus dijalankan setelah setiap perubahan konten agar vektor sesuai dengan barisnya. Pack
baru adalah pasangan berkas baru di direktori yang sama dengan nama yang cocok dengan pola id; tidak
perlu mengubah kode untuk memilihnya.

<a id="supported-gateways"></a>
## Gateway yang didukung

Inilah daftar lengkapnya; tidak ada bagian lain dalam dokumentasi yang mendaftar gateway.

- **CLI** — bawaan. Config default mengaktifkannya, dan inilah cara `eidesoul run` serta
  `eidesoul console` berbicara dengan sebuah jiwa tanpa transport jaringan.
- **WhatsApp** — extra opsional. Pasang dengan `uv sync --extra whatsapp` dan aktifkan di bagian
  `gateways`; hilangkan keduanya dan gateway ini tidak ada untuk deployment tersebut. Core tidak
  pernah mengimpornya, dan peran member default tidak pernah menerima tool `send` miliknya.
- **Perwujudan Anda sendiri** — robot, loop suara, perangkat, atau endpoint HTTP privat hanyalah
  gateway lain. Implementasikan amplop masuk dan antarmuka gateway, kirim kelasnya sebagai entry
  point di grup `eidesoul.gateways` atau aktifkan berdasarkan nama, dan ia bergabung ke jiwa yang
  sama.

Gateway adalah transport, bukan otak. Ia menormalkan input, mengantarkan balasan, dan memiliki modul
tool khas tubuh yang ingin dieksposnya. Tool milik gateway diberikan ke peran secara eksplisit,
sama seperti tool inti.

<a id="roles-and-permissions"></a>
## Peran dan izin

Izin hanya datang dari peran. Setiap peran mendaftar `tools` dan `commands` miliknya sendiri; tidak
ada handler yang membawa default, jadi peran yang menghilangkan satu bagian ditolak untuk seluruh
bagian itu. Satu `roles.yaml` menyimpan semua peran, dan peran yang tersimpan di database menjadi
acuan dengan berkas sebagai cerminnya.

```yaml
roles:
  member:
    priority: 10
    users: []
    tools: [read, web_search, sandbox, memory, google]
    commands: [help, ping, reset, whoami, status, tools]
    context: ""
    memory_admin: false
  owner:
    priority: 100
    users: ["<alamat Anda>"]
    tools: ["*"]
    commands: ["*"]
    memory_admin: true
```

- `tools` dan `commands` menerima nama persis dan `*`. Daftar yang tidak ada berarti menolak semua.
- Izin tool diperiksa di satu tempat, saat tool dieksekusi, sehingga tool yang terlihat tetapi tidak
  diberikan tidak bisa berjalan.
- `approve` adalah izin command seperti yang lain: hanya peran yang mencantumkannya bisa
  menyelesaikan aksi yang tertunda.
- `memory_admin: true` wajib untuk menulis memori global jiwa.
- Alamat di beberapa peran digabung; blok `context` diurutkan berdasarkan priority, lalu name.
- Cakupan workspace mengikuti pemberian yang sama: peran tanpa `*` melihat area bersama yang
  read-only dan area scratch miliknya, dan sandbox mencerminkan itu dengan mount bersama read-only
  serta satu mount scratch yang bisa ditulis.

Mengedit `roles.yaml` secara manual didukung: jalankan `eidesoul config import` setelahnya agar
database melihatnya.

<a id="safety"></a>
## Keamanan

Dua mekanisme terpisah, keduanya fail-closed.

**Approval.** Aksi sensitif — `shell`, `sandbox`, dan perubahan `settings` internal — melewati
kebijakan keamanan sebelum berjalan. `approval.mode` default-nya `yolo` (setujui otomatis, untuk
deployment satu operator yang tepercaya), `strict` menahan setiap aksi non-read-only, dan `off`
mematikan pemeriksaan sepenuhnya. `off` adalah risiko yang didokumentasikan, bukan kemudahan: tidak
ada apa pun antara model dan host. `/approve` menyelesaikan aksi yang tertunda, tidak pernah memanggil
LLM, dan memeriksa ulang izin peran lebih dulu.

**Sandbox guard.** Setiap perintah yang mencapai tool `sandbox` melewati dua lapisan, berurutan:

1. *Deterministik.* Satu set aturan tetap menolak pola keluar dari host dan pola destruktif —
   menghapus filesystem, menulis ke device, tool kernel dan namespace, kendali container dan
   firewall, pipe fetch-to-shell, eskalasi hak, manajemen layanan, dan fork bomb klasik. Perintah
   read-only sederhana langsung diizinkan tanpa panggilan model. Selain itu dianggap belum pasti.
2. *Classifier.* Hanya perintah yang belum pasti yang sampai ke classifier LLM kecil dan cepat
   dengan timeout pendek. Ia menjawab satu pertanyaan: apakah perintah ini mencoba keluar,
   menyerang infrastruktur, mengeksfiltrasi rahasia, menetap, atau menghancurkan data host? Yang
   tidak dikenal dianggap mencurigakan.

Verdict menentukan siapa yang boleh lanjut. Perintah yang diblokir lapisan deterministik ditolak
untuk semua orang, termasuk approver. Perintah yang tertunda atau mencurigakan menjadi approval
tertunda bagi peran yang memegang `approve`, dan ditolak langsung untuk peran lain — member tidak
pernah tertahan di antrean yang tidak bisa ia selesaikan. `approved=True` dari `/approve` melewati
kedua lapisan, setelah pemeriksaan ulang izin. Jika classifier tidak tersedia, timeout, atau
mengembalikan sesuatu yang tidak bisa diurai, hasilnya tertutup: tertunda untuk approver, ditolak
untuk peran lain. Perintah yang lebih panjang dari `guard.max_command_chars` diperlakukan sama.

`guard.enabled: false` mematikan kedua lapisan; sandbox lalu menjalankan apa pun yang diberikan.
Pakai hanya saat men-debug sebuah aturan, jangan di deployment yang menerima input tidak
terpercaya. Log mencatat verdict, alasan, dan percakapan — tidak pernah isi perintah.

Sandbox sendiri berjalan di container sekali pakai dengan hanya mount bersama dan scratch, di
jaringan internal khusus di belakang proxy egress, dengan timeout, batas PID, memori, dan CPU.
Tujuan privat diblokir di proxy, jadi sebuah perintah tidak bisa mencapai host atau jaringan lokal
bahkan ketika ia diizinkan berjalan.

<a id="optional-integrations"></a>
## Integrasi opsional

```bash
uv sync --extra memory --extra vibe                     # memori + affect dari source
uv sync --extra whatsapp --extra google                 # gateway dan integrasi opsional
```

```bash
docker build --build-arg EXTRAS="--extra memory --extra vibe --extra whatsapp --extra google" .
```

- **Memori** (`memory`): Mem0 dengan embedder multibahasa lokal dan pgvector. Tanpanya, jiwa tetap
  membalas dan melaporkan mengapa memori tidak tersedia.
- **Affect** (`vibe`): classifier emosi kecil yang memberi masukan pada nada. Tanpanya, nada datang
  dari pengaturan gaya dan karakter saja.
- **WhatsApp** (`whatsapp`): gateway opsional. Tidak pernah aktif secara default.
- **Google Workspace** (`google`): tool in-process untuk berkas yang dibagikan ke service account,
  dengan penautan identitas per pengirim. Tanpa extra ini tool tetap terdaftar tetapi inert dan
  mengembalikan error yang jelas, bukan traceback.

Rahasia hanya boleh ada di `.env`. `config.yaml`, `roles.yaml`, `soul.yaml`, dan `.env` tidak pernah
di-commit.

<a id="credits"></a>
## Kredit

Eidesoul berdiri di atas karya banyak pihak lain.

**Runtime dan dependensi:** Python, SQLAlchemy, psycopg, pgvector, OpenAI Python SDK, MCP Python
SDK, PyYAML, dan DDGS.

**Memori dan karakter:** Mem0; sentence-transformers dengan model
`paraphrase-multilingual-MiniLM-L12-v2`.

**Affect:** `Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier` dan emotion engine yang
di-vendor, yang lisensinya disertakan sebagai `eidesoul/vendor/LICENSE.emotion-engine`.

**Gateway dan integrasi opsional:** Neonize, segno, Google API Python Client, google-auth, dan
httpx.

**Terima kasih:** grup diskusi dan para penguji yang dirujuk selama pengembangan (lihat
`references/`), serta semua yang mencoba build awal dan melaporkan apa yang rusak. Tidak ada
transkrip mentah dari percakapan itu yang dikirim: pustaka karakter berisi contoh yang ditulis
sendiri dan fakta bersumber, bukan log chat.

<a id="license"></a>
## Lisensi

Repositori ini tidak menyatakan lisensi proyek. Setiap komponen pihak ketiga mempertahankan lisensi
dan atribusinya masing-masing; emotion engine yang di-vendor membawa lisensinya di samping
source-nya, dan dependensi lain didistribusikan dengan ketentuan proyek upstream masing-masing.
Tinjau ketentuan tersebut sebelum mendistribusikan ulang sebuah build.
