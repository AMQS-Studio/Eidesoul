<p align="center">
  <img src="assets/logo.png" alt="MonoAgens" width="260">
</p>

<h1 align="center">MonoAgens</h1>

<p align="center"><em>agen mono persisten, selalu ingat, selalu belajar, orang yang sama di mana pun ia hidup.</em></p>

<p align="center">
  <a href="docs/v0.1.0/README.md"><img alt="Docs" src="https://img.shields.io/badge/docs-v0.1.0-111111?style=flat-square&labelColor=555555"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-2ea043?style=flat-square&labelColor=555555"></a>
  <a href="#status-hari-ini"><img alt="Status" src="https://img.shields.io/badge/status-pra--rilis-d4a017?style=flat-square&labelColor=555555"></a>
  <a href="pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.14%2B-3776ab?style=flat-square&labelColor=555555"></a>
  <a href="https://github.com/AMQStudio"><img alt="Built by AMQStudio" src="https://img.shields.io/badge/built%20by-AMQStudio-6f42c1?style=flat-square&labelColor=555555"></a>
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/lang-EN-1f6feb?style=flat-square&labelColor=555555"></a>
  <a href="README.id.md"><img alt="Bahasa Indonesia" src="https://img.shields.io/badge/lang-ID-d52b1e?style=flat-square&labelColor=555555"></a>
</p>

<p align="center">
  <a href="#apa-itu-monoagens">Apa itu MonoAgens</a> · <a href="#architecture">Arsitektur</a> ·
  <a href="#tools-are-capabilities">Tool</a> ·
  <a href="#quickstart">Mulai cepat</a> ·
  <a href="#soulyaml">soul.yaml</a> · <a href="#karakter-dan-riset">Karakter dan riset</a> ·
  <a href="#supported-gateways">Gateway</a> · <a href="#roles-and-permissions">Peran dan izin</a> ·
  <a href="#safety">Keamanan</a> · <a href="#credits">Kredit</a>
</p>

<a id="status-hari-ini"></a>
## Status hari ini

Engine, konsol CLI, gateway WhatsApp (extra opsional), memori, affect, guard, installer on-machine,
dan model plugin sudah jalan hari ini. Panel web masih rencana, belum dibangun; ia proses terpisah
yang tidak akan pernah diimpor engine. Sisa berkas ini menggambarkan desain yang sudah dikirim;
bagian ini adalah selisih yang berjalan sekarang.

<a id="apa-itu-monoagens"></a>
## Apa itu MonoAgens

MonoAgens adalah **persistent mono agent harness**: runtime di sekitar model yang state-nya adalah satu
diri yang berlanjut, bukan sesi. Ia menjaga satu identitas, satu memori, dan satu set izin lintas
restart, permukaan, dan tubuh. Setiap giliran menyusun ulang working set dari Postgres, dan sebuah
tubuh hanya menambah transport, tidak pernah menambah kepribadian. Ia bukan template chatbot atau framework
orkestrasi.

MonoAgens dibangun oleh [AMQStudio](https://github.com/AMQStudio) (didirikan oleh Abdul Muqsith) dan
dikembangkan sebagai proyek open-source bersama. Repositorinya ada di
[AMQStudio/MonoAgens](https://github.com/AMQStudio/MonoAgens).

<a id="apa-arti-slogan-ini"></a>
## Apa arti slogan ini

- **satu diri** — satu identitas dan satu memori per jiwa, bukan sesi. Proses di-restart, permukaan
  diganti: diri yang sama tetap berlanjut.
- **selalu ingat** — yang penting dicatat: fakta tentang orang, siapa diri jiwa itu sendiri, tugas yang
  sudah selesai, dan apa yang baru saja dikerjakan. Fakta membawa tingkat privasi dan bisa diveto oleh
  orang yang menjadi subjeknya, identitas hidup di satu dokumen soul alih-alih di prompt, dan yang
  dibicarakan orang yang sama di satu tubuh bisa dilanjutkan di tubuh lain, karena memori milik jiwa dan
  orangnya, bukan milik transport. Lupa adalah keputusan (retensi, decay), bukan kecelakaan.
- **selalu belajar** — saat sebuah tugas selesai, worker membaca episode-nya dan bisa menulis prosedur
  yang bisa dipakai ulang dengan rujukan ke asalnya. Belajar mengubah prosedur, tidak pernah aturan,
  persona, atau izin — dan ia berjalan secara default.
- **orang yang sama di mana pun ia hidup** — sebuah tubuh membawa giliran: terminal, aplikasi chat,
  perangkat, robot. Chat adalah satu tubuh, bukan kelas dasar. Asisten adalah satu peran di antara
  pendamping, penasihat, dan perwujudan robot; mengganti peran mengubah apa yang boleh dilakukan jiwa,
  bukan siapa dirinya.

Memori adalah lantainya, bukan seluruh jiwa. Emosi punya engine sendiri: state PAD dengan baseline
kepribadian yang meluruh antar-giliran, drive yang berdenyut, dan relasi per orang yang bergerak lewat
tahap. Karakter adalah persona jiwa dan beberapa contoh nyata, bukan katalog pola lokal: model membawa
prior stabil, `web_search` memasok fakta terkini lewat `ResearchCard` yang efemeral, dan Mem0 memasok
fakta personal. Pacing yang dimanusiakan adalah timing di atasnya.

Clean by default berlaku untuk gateway, tool, plugin, dan voice: masing-masing mati sampai diaktifkan,
dan spine satu-satunya pengecualian. Lapisan voice — emosi, gaya, karakter, dan humanize — hidup di
`soul.voice`, mulai mati, dan satu master switch membuat setiap bagiannya inert.

<a id="di-mana-posisinya"></a>
## Di mana posisinya di antara harness

Agent adalah model plus harness. Harness adalah runtime di sekitar model: ia menjalankan pemanggilan
tool, menjaga state dan konteks, menerapkan approval, dan menjaga pekerjaan tetap bergerak. Harness
dinamai menurut tujuannya — autonomous harness agent berjalan tanpa pengawasan, developer harness agent
mengubah sebuah repository — dan yang sudah matang sudah membawa memori, skill, todo, dan aturan
approval.

MonoAgens adalah **persistent mono agent harness**: harness yang state-nya adalah diri. Ia mengingat
lintas sesi, menjaga satu persona, dan berperilaku sebagai satu karakter yang meyakinkan lintas setiap
embodiment.

| | Di mana state hidup | Bagaimana satu giliran mendapat konteks | Dioptimalkan untuk |
|---|---|---|---|
| Autonomous harness agent | riwayat tugas dan workspace-nya, cukup untuk melanjutkan dari titik terakhir | context window, dipadatkan saat penuh | menyelesaikan pekerjaan tanpa pengawasan |
| Developer harness agent | repository dan berkas-berkasnya | context window, diisi dari berkas dan keluaran tool | mengubah kode dengan benar |
| MonoAgens (persistent mono agent harness) | satu store kanonik — Postgres dengan index vektor — memuat memori, affect, dan transkrip | dibangun ulang setiap giliran dari store itu, dibatasi pada orang dan chat; window hanya working set, bukan memorinya | tetap orang yang sama, serba bisa — ingat, belajar, privasi, izin |

Harness konvensional memperlakukan context window sebagai memori kerja lalu memadatkannya saat penuh. Di
sini store adalah memorinya dan window hanya working set giliran ini, jadi percakapan panjang tidak
bergantung pada apa yang masih muat. Tingkat privasi, veto oleh orang yang menjadi subjek fakta, dan izin
berbasis role adalah sifat store itu, bukan sifat prompt.

<a id="apa-yang-coba-diselesaikan"></a>
## Apa yang coba diselesaikan

Dua belas dinding. Tiap baris menyebut bangunan biasanya, lalu apa yang dilakukan engine ini.

- **Asisten yang lupa.** Biasanya setiap sesi mulai dari prompt kosong, dan riwayat adalah transkrip
  yang Anda tempel kembali. Di sini fakta masuk Postgres dengan tingkat privasi, pemilik, dan subjek, dan
  giliran berikutnya mengambilnya lewat pgvector dan Mem0 per orang, thread, atau jiwa — jadi keputusan
  bulan lalu bergantung pada apa yang tersimpan, bukan pada apa yang masih muat.
- **Percakapan yang melampaui window.** Biasanya pemadatan meringkas giliran tertua lalu membuangnya
  selamanya. Di sini arsip adalah rekamannya dan window adalah tampilan: tiap giliran menyusun working
  set dari arsip di bawah budget karakter, lalu membuangnya lagi, jadi satu thread bisa berjalan
  berbulan-bulan tanpa model memegang semuanya.
- **Karakter yang bergeser.** Biasanya tiap klien membawa prompt dan riwayatnya sendiri. Di sini satu
  dokumen soul dan satu memori dibaca setiap tubuh — terminal, aplikasi chat, perangkat, robot — jadi
  orang yang sama menjawab di mana pun, dan tubuh hanya menambah transport, tidak pernah menambah
  kepribadian.
- **Memori yang bocor.** Biasanya satu index vektor melayani semua ruangan. Di sini pengambilan hanya
  menyentuh global, orang ini, dan thread ini; grup membatasi levelnya; subjek bisa memveto; dan
  keputusan pengungkapan bersifat deterministik, dibuat sebelum model mana pun melihat teksnya.
- **Agent yang bertindak tanpa otoritas.** Biasanya loop menyetujui semuanya atau bertanya terus. Di sini
  role memberi izin berdasarkan nama, guard deterministik jalan lebih dulu, dan target menentukan consent
  atau otorisasi — lingkup sendiri meminta peminta, lingkup bersama atau luar meminta role approver —
  dihitung ulang saat eksekusi, jadi role yang melebar membuka ulang pertanyaannya.
- **Role yang sebenarnya spreadsheet.** Biasanya flag admin, role default yang lebar, dan pemeriksaan saat
  login. Di sini entri yang tidak ada berarti menolak, kapabilitas, consent, dan otorisasi dijaga
  terpisah, approval hanya melanjutkan izin yang sudah ada, administrasi memori adalah kapabilitas
  tersendiri, dan role tertinggi tidak bisa dihapus atau diganti nama.
- **Dua instance, satu jiwa.** Biasanya dua replika menjawab dan penulis terakhir menang. Di sini satu
  baris lease di Postgres menentukan satu primary; instance lain standby dan beku, dan takeover adalah
  peristiwa database dengan jejak audit.
- **Giliran yang mati di tengah.** Biasanya restart mengulang efek samping. Di sini pesan dan gilirannya
  masuk antrean dalam satu transaksi, giliran adalah baris dengan checkpoint langkah, dan pengiriman
  dicatat per potongan: resume mengirim ulang hanya yang belum terkirim, dan aksi non-idempoten yang sudah
  jalan ditandai interrupted alih-alih diulang.
- **Tebakan yang menjadi aturan.** Biasanya catatan agent kembali sebagai instruksi. Di sini tulisan
  turunan wajib berujuk sumber, penurunan satu tingkat, aturan hanya dari dokumen soul, dan memori baru
  tetap dalam karantina sampai terkorroborasi.
- **Balasan yang mengarang apa yang tidak diketahuinya.** Biasanya model mengisi celah dengan percaya
  diri. Di sini persona dan beberapa contoh menjadi jangkar suara; fakta terkini datang dari
  `web_search` dengan sitasi, dan pass karakter boleh mengubah nada tapi tidak fakta, angka, atau hasil
  tool.
- **Balasan yang terdengar seperti mesin.** Biasanya prompt "jadilah ramah". Di sini emosi adalah engine
  sendiri — state PAD dengan baseline yang meluruh, drive, dan relasi per orang — lapisan konteks
  memoles draf yang robotik, dan pass humanize mengatur pacing; semuanya sakelar.
- **Automation yang sebenarnya cron job.** Biasanya baris crontab tanpa pemilik, izin, atau dedupe. Di
  sini event, condition, dan daftar steps tertutup; satu run per occurrence di-key dari jadwal; otoritas
  diperiksa dua kali; jam tenang, budget harian, dan kill switch.

Tidak satu pun dari ini trik prompt. Masing-masing adalah tempat state hidup, dan aturan siapa yang boleh
mengubahnya.

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

Tool adalah kemampuan yang dipanggil engine. Ia bukan tubuh, dan bukan bundel: tiga hal dijaga terpisah.

- **Gateway** — jalur komunikasi: tubuh yang membawa pesan masuk dan keluar (terminal, aplikasi chat,
  perangkat, robot) dan memiliki aksi yang hanya bermakna di sana. Kamera, motor, printer, atau bus
  otomasi rumah adalah milik perwujudan, bukan milik engine; gateway mengekspos aksi itu sebagai modul
  tool miliknya. Peran memberikan tool tersebut secara eksplisit, jadi peran pendamping di ponsel tidak
  pernah mewarisi kendali sebuah robot.
- **Tool** — kemampuan yang dipanggil engine. Enam keluarga inti hanyalah contoh, bukan taksonomi yang
  harus dipenuhi: `shell`, `read`, `write`, `web_search`, `sandbox`, dan `mcp`. Tubuh menentukan
  kemampuan mana yang ada; peran menentukan mana yang boleh dipakai jiwa di tubuh itu, dan entri yang
  tidak ada berarti menolak.
- **Plugin** — unit pengemasan dan siklus hidup, bukan jenis keempat: ia memasang kode dan mendaftarkan
  apa yang ia sediakan (tool, command, dan mungkin gateway) ke registry yang sama dengan modul inti.
  Kodenya diambil saat diaktifkan dan dipin dengan digest; ia tidak pernah menjadi tool inti, dan gateway
  yang ia bawa tetap gateway.

Ketiganya mati sampai diaktifkan, dan spine satu-satunya pengecualian. Engine tidak pernah
meng-hardcode tubuh dan tidak pernah meng-hardcode loop agent: tidak ada planner yang perlu
dikonfigurasi, tidak ada task graph yang diluncurkan, tidak ada kawanan di latar belakang — satu jiwa,
satu giliran, dan kemampuan yang dipegang saat itu.

<a id="quickstart"></a>
## Mulai cepat

Kebutuhan: Python 3.14, Postgres 14+ dengan `pgvector`, dan endpoint LLM yang kompatibel dengan
OpenAI. Image container memakai Python 3.14; menjalankan dari source butuh 3.14 atau lebih baru.

Jalur terpendek adalah installer. Ia memakai menu curses yang sama dengan `monoagens init` dan
menanyakan di mana engine berjalan, di mana berkas disimpan (`~/.monoagens/app` yang disarankan,
direktori saat ini, atau path custom), apakah Postgres berupa container atau server eksternal, dan
apakah container berjalan di Podman atau Docker. Setiap runtime ditampilkan sebagai terpasang atau
hilang; installer bertanya sebelum memasang yang hilang, dan `sudo` meminta password di terminal.
`config.yaml`, `roles.yaml`, dan `soul.yaml` yang dibuat sudah berisi default yang bisa dipakai, dan
installer mendaftarkan `cli local` sebagai administrator pertama.

```bash
bash install.sh                   # menu interaktif
bash install.sh --stack native    # engine di host, Postgres di container
bash install.sh --stack compose   # engine dan Postgres di container
bash install.sh --engine docker   # Docker, bukan Podman
bash install.sh --database external --database-url URL
bash install.sh --language id      # override locale host C/POSIX
bash install.sh --gateway whatsapp  # aktifkan WhatsApp dan pasang extra-nya
bash install.sh --dry-run         # cetak setiap aksi, tulis apa pun
```

Dari jaringan, unduh skripnya dulu supaya wizard tetap punya terminal:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/AMQStudio/MonoAgens/main/install.sh)
```

`curl ... | bash` tidak punya TTY, jadi wizard fail-closed; berikan `--provider` dan flag lain
untuk instalasi headless.

Dari source, langkah yang sama secara manual:

```bash
# 1. Scaffold config.yaml, roles.yaml, soul.yaml, dan .env; provider adalah pilihan operator
monoagens init --provider openai

# 2. Jadikan ini jiwa Anda, bukan jiwa default
$EDITOR soul.yaml

# 3. Isi .env: kunci provider; installer mengisi nilai database saat ia
#    membuat container database, atau isi URL eksternal sendiri
#      MONOAGENS_DATABASE_URL=postgresql+psycopg://monoagens:<password>@127.0.0.1:5432/monoagens
#      POSTGRES_PASSWORD=<password yang sama>
#      OPENAI_API_KEY=...

# 4. Satu database untuk satu jiwa; compose.yaml membaca POSTGRES_PASSWORD dari .env
podman compose up -d postgres

# 5. Buat skema dan seed berkas host ke dalamnya
monoagens db upgrade

# 6. Jalankan gateway yang diaktifkan di config.yaml
monoagens run

# REPL lokal terhadap jiwa dan database yang sama; mengambil lease
monoagens console --takeover
```

`monoagens run` memuat dokumen, mengambil lease primary, lalu menjalankan gateway yang aktif.
Instance kedua tetap standby dan beku sampai mengambil alih lewat lease, jadi
`monoagens console --takeover` adalah cara resmi berbicara manual dengan deployment yang sedang
berjalan.

`monoagens db check` mencetak ringkasan skema dan isi; `monoagens selftest` menguji store, tool, dan
LLM sekali tanpa menjalankan gateway. `monoagens people list` menampilkan identitas dan alamat
gateway-nya, dan `monoagens people adopt` memindahkan alamat lama (nomor telepon, `local`) ke id
`per_…`, ikut membawa state affect dan style-nya serta menulis ulang dokumen peran; ia idempoten,
jadi deployment yang di-upgrade cukup menjalankannya sekali.

Tes berjalan di toko asli, Postgres+pgvector. Jalankan database sekali pakai (container ini bukan
dev atau produksi) lalu ekspor DSN-nya; tanpa variabel itu suite gagal dengan perintah tersebut,
bukan jatuh ke SQLite:

```bash
podman run -d --name monoagens-test-pg -p 5433:5432 \
  -e POSTGRES_DB=monoagens_test -e POSTGRES_USER=monoagens -e POSTGRES_PASSWORD=monoagens \
  docker.io/pgvector/pgvector:pg17
export MONOAGENS_TEST_DATABASE_URL='postgresql+psycopg://monoagens:monoagens@127.0.0.1:5433/monoagens_test'
pytest -q
```

`uv sync` memasang tulang punggung kemampuan: penyimpanan Postgres+pgvector, memori Mem0, embedder
multibahasa, prototipe affect, dan guard. Hanya transport dan integrasi yang berupa extra:

| Extra | Membawa | Dibutuhkan untuk |
|---|---|---|
| `whatsapp` | Neonize + segno | gateway WhatsApp |

```bash
uv sync --extra whatsapp
```

Setiap extra bisa dilepas: tidak ada bagian core yang mengimpornya secara eager, dan tanpa extra,
tool atau gateway terkait melaporkan alasannya. Google bukan extra: ia **plugin**, diunduh dan
diverifikasi saat diaktifkan (lihat Integrasi opsional).

<a id="soulyaml"></a>
## soul.yaml

Satu database adalah satu jiwa, dan `soul.yaml` adalah tempat jiwa itu dituliskan. Setelah
`db upgrade` pertama, jiwa di database menjadi acuan dan `soul.yaml` adalah seed sekaligus
cerminnya. Edit biasa berlaku saat start berikutnya: engine membandingkan hash berkas dan DB,
mengimpor bila hanya berkas yang berubah, dan membiarkan DB menang saat keduanya berubah.
`monoagens config import` dan `monoagens config export` tetap ada untuk arah paksa.

```yaml
name: MonoAgens
description: ""
language: id                    # katalog terpasang: id | en
locale: id-ID
timezone: Asia/Jakarta          # zona IANA wajib; tidak ada default kode
pronouns: aku
role: assistant
roles: [assistant]
background: ""
persona: ""
voice:
  enabled: false                # satu saklar utama; semua bagian di bawah inert saat false
  sampling:
    daily_temperature: 0.9
    professional_temperature: 0.3
    top_p:                      # kosong = default provider
    top_k:
    min_p:
  affect:
    enabled: true
  style:
    enabled: true
  character:
    enabled: false
    max_chars: 400
    max_tokens: 500
    temperature: 0.7
  humanize:
    enabled: true
    cpms: 25.0
    min_delay: 0.5
    max_delay: 5.0
    between_delay: 0.7
    max_bubbles: 3
    reaction_stickers: []
  profanity: avoid              # free | avoid
  safety: inherit               # inherit | off; off butuh adapter native
examples: []
rules: []
```

Aturan loader:

- `language`, `locale`, dan `timezone` wajib; `monoagens init` menuliskannya.
- `roles` default `[role]`; bila `role` tidak ada di `roles`, ia ditambahkan di depan.
- `persona` dirender dengan urutan tetap: nama, deskripsi, peran aktif, peran tersedia, locale,
  background, persona bebas, lalu contoh dialog. Bagian kosong dihilangkan.
- `voice.enabled` adalah satu-satunya saklar utama. `sampling`, `profanity`, dan `safety` berlaku
  per request dan tetap bekerja saat master off; `affect`, `style`, `character`, dan `humanize`
  inert.
- `voice.profanity` adalah instruksi prompt, bukan tabel toksisitas. `voice.safety: off` hanya
  diterima bila wire provider melaporkan kemampuan safety request-level.
- Blok `voice` yang tidak ada memakai default aman: off, `profanity: avoid`, `safety: inherit`,
  dan `top_p` / `top_k` / `min_p` kosong.
- Kunci lama `engine.affect`, `engine.style`, `engine.character`, `engine.humanize`, dan
  `engine.llm.temperature` ditolak; semuanya pindah ke sini.

## Karakter dan riset

Karakter berasal dari `soul.yaml` dan model, bukan katalog pack runtime:

- `persona` dan `examples` adalah jangkar few-shot; contoh pendek dan sintetis.
- Pengetahuan umum yang stabil boleh dari prior model.
- Klaim terkini, spesifik, lokal, numerik, diperdebatkan, atau yang butuh sitasi memicu
  `web_search`. Hasilnya masuk prompt hanya sebagai `ResearchCard` tak tepercaya dengan sumber,
  ID, dan timestamp; jawaban mengutip `[R1]`, dan klaim terkini tanpa dukungan harus abstain.
- Fakta personal dan episode tetap di Mem0; `soul.yaml` bukan katalog fakta publik.
- Bila model ragu tetapi tidak ada trigger deterministik, ia boleh memanggil `web_search` sendiri.

Tidak ada `library/patterns`, `library/facts`, `library resolve`, atau tabel level karakter.
`library/` hanya menyimpan data detektor (`lexicon`, `privacy`, `safety`, `style`, `affect`, dan
`intent`), bukan contoh prompt atau baris fakta publik.

## Gateway yang didukung

Inilah daftar lengkapnya; tidak ada bagian lain dalam dokumentasi yang mendaftar gateway.

- **CLI** — bawaan. Config default mengaktifkannya, dan inilah cara `monoagens run` serta
  `monoagens console` berbicara dengan sebuah jiwa tanpa transport jaringan.
- **WhatsApp** — extra opsional. `monoagens gateway add whatsapp` memasang extra yang
  dideklarasikan dan mengaktifkannya; lakukan pairing dengan `monoagens gateway pair whatsapp`.
  Hilangkan keduanya dan gateway ini tidak ada untuk deployment tersebut. Core tidak pernah
  mengimpornya, dan peran member default tidak pernah menerima tool `send` miliknya.
- **Perwujudan Anda sendiri** — robot, loop suara, perangkat, atau endpoint HTTP privat hanyalah
  gateway lain. Implementasikan amplop masuk dan antarmuka gateway, kirim kelasnya sebagai entry
  point di grup `monoagens.gateways` atau aktifkan berdasarkan nama, dan ia bergabung ke jiwa yang
  sama.

```bash
monoagens gateway list
monoagens gateway add whatsapp
monoagens gateway pair whatsapp
monoagens gateway disable whatsapp
```

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
    commands: [help, ping, link, unlink, whoami, status, tools]
    context: ""
    memory_admin: false
  administrator:
    priority: 100
    users: ["per_xxxxxxxxxx"]
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
- Peran mencantumkan identitas opaque `per_…` (`monoagens people list`), bukan nomor telepon atau
  JID: `person_links` memetakan tiap alamat gateway ke identitas itu, `/link` menautkan gateway
  kedua ke orang yang sama, dan `/unlink` melepasnya. Identitas di beberapa peran digabung;
  blok `context` diurutkan berdasarkan priority, lalu name.
- Cakupan workspace mengikuti pemberian yang sama: peran tanpa `*` melihat area bersama yang
  read-only dan area scratch miliknya, dan sandbox mencerminkan itu dengan mount bersama read-only
  serta satu mount scratch yang bisa ditulis.

Mengedit `roles.yaml` secara manual didukung: jalankan `monoagens config import` setelahnya agar
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
uv sync --extra whatsapp                  # gateway opsional
monoagens plugins install google        # plugin: unduh + verifikasi digest
monoagens plugins enable google         # daftarkan tool-nya ke katalog yang sama
```

```bash
podman build --build-arg EXTRAS="--extra whatsapp" .
```

Memori, embedder, dan prototipe affect adalah bagian dari instalasi core; tidak ada extra untuk
mereka. Penyimpanan hanya Postgres+pgvector, toko yang sama dengan tes.

- **Vibe affect** membaca embedder bersama terhadap `monoagens/library/affect/*.json`; tanpa embedder
  suasana ruangan tetap netral, bukan menebak.
- **WhatsApp** (`whatsapp`): gateway opsional. Tidak pernah aktif secara default.
- **Google Workspace** (`google`): plugin, bukan tool core dan bukan extra. Selama absen,
  kemampuannya dilaporkan `unavailable`, tidak pernah difabrikasi; mengaktifkan akan mengunduh kode
  berdasarkan digest dan tanpa jaringan gagal eksplisit tanpa mengubah apa pun.

Rahasia hanya boleh ada di `.env`. `config.yaml`, `roles.yaml`, `soul.yaml`, dan `.env` tidak pernah
di-commit.

<a id="credits"></a>
## Kredit

Setiap komponen di bawah ini mempertahankan lisensi dan atribusinya masing-masing. Versinya adalah
yang dipasang lock file; kolom terakhir menjelaskan untuk apa MonoAgens memakainya.

| Komponen | Lisensi | Penulis / organisasi | Sumber | Dipakai untuk |
|---|---|---|---|---|
| Python | PSF-2.0 | Python Software Foundation | https://www.python.org/ | runtime |
| SQLAlchemy | MIT | Michael Bayer / SQLAlchemy authors | https://www.sqlalchemy.org/ | penyimpanan |
| psycopg | LGPL-3.0-only | Daniele Varrazzo | https://psycopg.org/ | driver Postgres |
| psycopg-pool | LGPL-3.0-only | Daniele Varrazzo | https://www.psycopg.org/ | pool koneksi |
| pgvector | PostgreSQL | pgvector contributors | https://github.com/pgvector/pgvector | kolom vektor |
| openai-python | Apache-2.0 | OpenAI | https://github.com/openai/openai-python | klien LLM |
| MCP Python SDK | MIT | Model Context Protocol authors | https://github.com/modelcontextprotocol/python-sdk | tool MCP |
| PyYAML | MIT | Kirill Simonov / PyYAML authors | https://pyyaml.org/ | berkas config + soul |
| ddgs | MIT | ddgs authors | https://github.com/deedy5/ddgs | web search |
| Mem0 (`mem0ai`) | Apache-2.0 | Mem0 | https://github.com/mem0ai/mem0 | memori semantik |
| sentence-transformers | Apache-2.0 | UKP Lab / Hugging Face | https://github.com/huggingface/sentence-transformers | embedding |
| Presidio (`presidio-analyzer`) | MIT | Microsoft | https://github.com/microsoft/presidio | orkestrasi NER |
| spaCy | MIT | Explosion | https://github.com/explosion/spaCy | pipeline NLP lokal |
| xx_ent_wiki_sm | MIT | Explosion (WikiNER) | https://github.com/explosion/spacy-models/releases/tag/xx_ent_wiki_sm-3.8.0 | bobot NER multibahasa |
| paraphrase-multilingual-MiniLM-L12-v2 | Apache-2.0 | sentence-transformers | https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | bobot embedder |
| PyTorch | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib | Meta / PyTorch contributors | https://github.com/pytorch/pytorch | runtime tensor |
| Transformers | Apache-2.0 | Hugging Face | https://github.com/huggingface/transformers | pemuatan model |
| Neonize | tidak dinyatakan upstream | krypton-byte | https://github.com/krypton-byte/neonize | transport WhatsApp |
| segno | BSD-3-Clause | Lars Heuer | https://github.com/heuer/segno | pemasangan QR |
| google-api-python-client | Apache-2.0 | Google | https://github.com/googleapis/google-api-python-client | plugin Workspace (manifest) |
| google-auth | Apache-2.0 | Google | https://github.com/googleapis/google-auth-library-python | plugin Workspace (manifest) |
| httpx | BSD-3-Clause | Encode | https://github.com/encode/httpx | klien HTTP |
| emotion-engine | MIT | PioneerJeff Labs | https://github.com/pioneerjeff-labs/emotion-engine | engine affect yang di-vendor |
| EmoSense-ID classifier | lihat model card | Aardiiiiy | https://huggingface.co/Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier | acuan kalibrasi saja |
| pytest | MIT | pytest authors | https://github.com/pytest-dev/pytest | dev |
| pytest-cov | MIT | pytest-cov authors | https://github.com/pytest-dev/pytest-cov | dev |
| ruff | MIT | Astral | https://github.com/astral-sh/ruff | dev |

<a id="license"></a>
## Lisensi

MonoAgens dirilis di bawah [Apache License 2.0](LICENSE). Hak cipta 2026 Abdul Muqsith.

Komponen pihak ketiga mempertahankan ketentuannya masing-masing; tabel di atas mencantumkan lisensi,
penulis, dan sumber tiap komponen. Emotion engine yang di-vendor membawa lisensinya di samping
source-nya sebagai `monoagens/vendor/LICENSE.emotion-engine`.
