# Kontrak Gateway, Plugin, dan MCP

**Cakupan:** permukaan ekstensi engine Eidesoul: bagaimana sebuah body (gateway) dan bundel
kemampuan yang dapat dipasang (plugin) terhubung, apa yang dimiliki masing-masing, dan apa yang
tidak boleh dilakukan keduanya.
**Konteks:** ditulis setelah ADR-0034 (prompt parts) dan ADR-0035 (skill ber-scope app). Klausa yang
bergantung pada keduanya ditandai **[setelah 0034]** dan **[setelah 0035]** dan mengikat ketika ADR
masing-masing keluar dari `draft`.
**Status:** kontrak normatif. Jarak antara berkas ini dan kode adalah cacat, bukan tafsir alternatif.
**Versi English:** `gateway-plugin.md`.

---

## 1. Empat kata

Sebuah **gateway** adalah body. Ia memindahkan event antara dunia luar dan engine serta memiliki aksi
transport-specific dari body itu.

Sebuah **plugin** adalah unit kode kemampuan yang dapat dipasang. Ia diambil, diverifikasi,
diaktifkan, dinonaktifkan, dan dihapus sebagai satu kesatuan; ia menyumbang tool, command, dan
(setelah 0034) prompt part statis.

Sebuah **tool** adalah kemampuan yang dipanggil engine. Sebuah **command** adalah kemampuan
operasional yang didispatch engine. Tool dan command bukan unit ekstensi: keduanya adalah *isi*
yang dapat disediakan oleh modul core, gateway, atau plugin.

Sebuah **server MCP** adalah penyedia tool eksternal yang dijangkau hidup-hidup lewat Model Context
Protocol. Ia dikonfigurasi, bukan dipasang: tanpa manifest, tanpa digest, tanpa `register()`, tanpa
prompt part, tanpa body. Ia memasok tool dan tidak yang lain.

Ketiganya tidak dapat dipertukarkan:

| Pertanyaan | Jawaban |
| --- | --- |
| Apakah ia memiliki transport, run loop, dan aksi tubuhnya sendiri? | gateway |
| Apakah ia kode yang dapat dipasang/dihapus dengan manifest dan digest? | plugin |
| Apakah ia kemampuan yang dipanggil model atau operator? | tool atau command |
| Apakah ia server tool eksternal yang dijangkau lewat protokol standar, tanpa langkah pemasangan? | server MCP |
| Bolehkah gateway "sekadar plugin"? | Tidak. Ia boleh *dikirim* oleh plugin, tetapi didaftarkan dan diaktifkan sebagai gateway (ADR-0028 §4.1). |
| Bolehkah plugin membuka transportnya sendiri? | Hanya lewat gateway yang ia kirim dan deklarasikan; tidak pernah sebagai efek samping saat import. |
| Bolehkah core memuat aksi gateway? | Tidak. Aksi gateway tinggal di `eidesoul/gateways/<name>/tools|commands`; aksi plugin tinggal di pohon plugin. |

Uji satu baris: **gateway adalah dari mana byte datang; plugin adalah apa yang dapat dipasang;
server MCP adalah pemasok tool yang kamu konfigurasi; tool adalah apa yang boleh dipanggil model.**

---

## 2. Kontrak gateway

### 2.1 Lokasi dan penemuan

- Gateway yang dikirim core adalah paket di bawah `eidesoul/gateways/<name>/`.
- Gateway yang dikirim plugin adalah kode di bawah `plugins/<name>/`, dideklarasikan di
  `plugin.yaml: provides.gateways`, dan didaftarkan lewat jalur gateway. Ia tidak pernah disebut
  "plugin gateway" dan tool-nya tidak pernah masuk daftar tool plugin global.
- Engine tidak pernah mengimpor gateway secara eager. `eidesoul/engine/**` tidak boleh mengimpor
  `eidesoul.gateways` atau modul plugin saat import.
- Gateway diaktifkan lewat config (`gateways.<name>.enabled: true`), tidak pernah dengan memasang
  plugin sebagai efek samping. Body default yang dikirim adalah `eidesoul/gateways/cli`.

### 2.2 Permukaan wajib

Sebuah body mengimplementasikan `GatewayCore` dari `eidesoul/engine/inbound.py`:

```python
name: str
def tools(self) -> list[Handler]: ...
def commands(self) -> list[Handler]: ...
async def run(self, agent) -> None: ...
async def stop(self) -> None: ...
async def dispatch(self, action: Action) -> str: ...
def self_handles(self) -> set[str]: ...
def chat_name(self, thread: str) -> str: ...      # default: thread id
def prompt_section(self) -> str: ...               # default: ""
```

`CoreDefaults` menyediakan `chat_name` dan `prompt_section`; sisanya wajib.

### 2.3 Siklus hidup

1. Engine membangun body dari `GatewaySettings`-nya dan runtime bersama.
2. `run(agent)` adalah satu-satunya loop. Ia menghasilkan nilai `Percept` dan memanggil
   `agent.handle(percept, self)`; ia tidak pernah memanggil store, model, atau turn secara langsung.
3. `stop()` mengakhiri loop dan melepaskan sumber daya transport. Restart membangun instance baru;
   tidak ada body yang menyimpan state global proses yang bertahan setelah `stop()` kecuali yang
   sengaja ia persist.
4. Hanya primary lease yang menjalankan body (ADR-0017). Body standby tidak dijalankan; body tidak
   boleh melawan lease.
5. `dispatch(action)` adalah satu-satunya pintu keluar untuk verb body. Capability yang tidak dikenal
   memunculkan `GatewayError`; ia tidak boleh diam-diam berhasil atau jatuh ke verb lain.

### 2.4 Event, alamat, receipt

- **Masuk.** Satu event body menjadi satu `Percept`: `source`, `thread`, `actor`, `kind`, `payload:
  Message`, `hints`, dan opsional `raw` (khusus transport; tidak pernah disimpan sebagai kebenaran).
- **Hint adalah fakta yang dapat dibuktikan body.** Key yang dikenal: `dm`, `mention`, `reply`,
  `group`, `presence`, `self`, `roles`; setelah 0035, `app`. Body tidak pernah mengarang hint yang
  tidak dapat ia buktikan dan tidak pernah menghilangkan hint yang dibutuhkan keputusan. Engine tidak
  pernah mewajibkan hint yang tidak dapat disediakan body.
- **Keluar.** Engine mengalamatkan `Address(gateway, thread, proof)` dan mengeluarkan `Action`
  (`capability`, `payload`, `approval_class`). Body merutekan berdasarkan `capability` dan menjawab
  dengan `Receipt(id, state, reference, reason)`.
- Identitas adalah tugas body: ia menormalisasi id aktornya sendiri (`person_id`) sebelum mencapai
  engine. Core tidak pernah mem-parsing JID, sufiks URL, nomor telepon, atau alamat transport.

### 2.5 Apa yang dimiliki body

| Dimiliki body | Tidak pernah pindah ke core |
| --- | --- |
| koneksi transport, sesi, reconnect, backoff | parsing alamat transport |
| rendering teks dan quoting untuk mediumnya | aturan format chat di prompt |
| unduh/unggah media dan batasnya | kebijakan penanganan file untuk transport tertentu |
| resolusi target (`Directory`) dan keanggotaan | tabel kontak/room di skema skill atau memori |
| pemetaan private thread (`PrivateThread`) | inferensi identitas transport per orang |
| aksi body di `<body>/tools|commands` | aksi tersebut sebagai tool core |
| satu `prompt_section()` statis | jalur prompt body kedua |

### 2.6 Error dan teks prompt

- `GatewayError` membawa pesan yang aman ditampilkan ke model atau pengguna. Stack trace transport,
  kredensial, dan payload mentah tetap di log, bukan di turn.
- `SendRefused` dan `StandbyError` adalah penolakan: pemanggil tidak pernah mencoba ulang.
- `prompt_section()` mengembalikan teks English statis, maksimal `PROMPT_SECTION_MAX = 1200`
  karakter, tanpa data pengguna, tanpa rahasia, dan tanpa interpolasi runtime. Ia adalah satu-satunya
  prompt part `body` (stable, pinned, tepat setelah `base`) setelah 0034.

---

## 3. Kontrak plugin

### 3.1 Unit pemasangan

```text
plugins/<name>/
  plugin.yaml          # manifest: data, dibaca sebelum import apa pun
  plugin.py            # modul entry, satu fungsi register()
  deps/                # dependensi plugin sendiri (tidak pernah masuk set terkunci image)
  ...                  # pohon kode plugin; digest mencakupnya
```

`plugin.yaml` mewajibkan: `name`, `version`, `engine`, `entry`, `digest`, `provides`, `requires`,
`secrets`, `credits`. `provides` memetakan nama ke daftar: `tools`, `commands`, `gateways`, dan
(setelah 0034) `prompt`. `secrets` adalah *nama* environment, tidak pernah nilai. `credits` membawa
baris yang utang tabel kredit README.

### 3.2 Verifikasi sebelum import

Loader melakukan, berurutan:

1. membaca dan memvalidasi manifest (key tak dikenal, nama, semver, bentuk entry, daftar provides);
2. menghitung SHA-256 pohon kode (path relatif terurut dan byte-nya; `plugin.yaml`, `deps/`, dan
   bytecode dikecualikan) dan membandingkannya dengan digest manifest;
3. membandingkannya lagi dengan `plugins.<name>.digest` yang dipin;
4. memeriksa rentang `engine` terhadap versi engine yang berjalan;
5. memeriksa bahwa setiap nama secret yang dideklarasikan ada di environment;
6. baru kemudian mengimpor `entry` dan memanggil `register()`.

Kegagalan apa pun meninggalkan plugin `unavailable` dengan pesan yang tidak membawa rahasia, dan
tidak mengubah config. Tidak ada unpack parsial, tidak ada salinan cadangan, dan tidak ada status
"terpasang tapi tidak tepercaya".

### 3.3 Registrasi

`register()` tidak menerima argumen dan mengembalikan mapping:

```python
{
    "tools": [Handler, ...],      # dideklarasikan 1:1 di provides.tools
    "commands": [Handler, ...],   # dideklarasikan 1:1 di provides.commands
    "prompts": [PromptPart, ...], # setelah 0034; dideklarasikan 1:1 di provides.prompt
}
```

Aturan:

- Setiap handler yang dikembalikan harus cocok persis dengan nama yang dideklarasikan; nama yang
  hilang atau tidak dideklarasikan adalah `PluginError`.
- Tabrakan nama dengan kemampuan core atau yang sudah dimuat adalah `PluginError`; plugin tidak dapat
  menutupi tool core atau plugin lain.
- Handler divalidasi dengan aturan `Handler` yang sama seperti modul core (`family`, `target`,
  `approval_class`, `destructive`, `path_arg`, `scan`, parameter). Plugin tidak mendapat pengecualian
  izin.
- Gateway yang dikirim plugin mengikuti §2 dan diserahkan ke registry gateway; ia bukan tool, bukan
  command, dan bukan bagian dari `provides.tools`.
- `register()` yang gagal membersihkan modul plugin dan meninggalkannya `unavailable`; engine tetap
  berjalan.

### 3.4 Siklus hidup

`absent → installed → enabled ⇄ disabled → removed` (ADR-0028 §4.3).

- **installing**: ambil berdasarkan digest, verifikasi, unpack, ambil `requires` ke `deps/`.
- **installed**: kode ada, tidak ada yang diimpor, tidak ada yang diberikan.
- **enabled**: verifikasi, impor sekali, registrasi, tulis `plugins.<name>.enabled: true` hanya
  setelah verifikasi; role tetap harus memberikan setiap capability.
- **disabled**: capability mati; berkas dan state tetap.
- **removed**: kode dan state plugin dihapus; ledger menyimpan riwayat.

Setiap langkah adalah aksi operator di bawah `authorization` dan tercatat di `config_changes` serta
action ledger. Tidak ada auto-update dan tidak ada pengambilan di latar belakang.

### 3.5 Apa yang dimiliki plugin

| Dimiliki plugin | Bukan milik plugin |
| --- | --- |
| manifest, pohon kode, dan digest-nya | skema engine |
| dependensinya di `deps/` | set dependensi terkunci image |
| logika tool/command-nya | pemeriksaan izin (`execute` yang memilikinya) |
| prompt part statisnya (setelah 0034) | urutan atau anggaran prompt (loader dan core yang memiliki) |
| berkas datanya sendiri di bawah root-nya | tabel core; penulisan lewat store engine |

Plugin menulis memori, skill, atau setting hanya lewat store/port publik engine. Ia tidak pernah
membuka database, melewati `execute`, atau menulis tabel core secara langsung.

---

## 4. Kontrak MCP

Sebuah **server MCP** adalah penyedia tool eksternal yang dijangkau engine saat runtime lewat Model
Context Protocol. Ia bukan plugin, bukan gateway, dan bukan paket ekstensi: tidak ada manifest,
digest, `register()`, atau permukaan prompt. Seluruh kontribusinya adalah tool.

### 4.1 Konfigurasi

- Key host `mcp.servers.<name>`. Setiap entri memiliki tepat satu transport:
  - **stdio:** `command`, opsional `args`, opsional `env`;
  - **Streamable HTTP:** `url` (`http://` atau `https://`).
- `command` dan `url` saling eksklusif; keduanya atau tidak keduanya adalah `ConfigError`.
- Nama server dan nama tool-nya di-slug menjadi nama yang diekspos; nama adalah data.
- Tidak ada langkah install, enable, disable, atau remove. Perubahan config plus restart adalah
  seluruh siklus hidupnya.

### 4.2 Penemuan dan eksposur

- `Hub.start()` terhubung ke setiap server yang dikonfigurasi (timeout 20 detik) dan memanggil
  `list_tools()` (timeout 20 detik lagi).
- Server yang gagal mencatat satu peringatan, tidak menyumbang tool, dan tidak pernah memblokir boot.
- Setiap tool menjadi `Handler` bernama `mcp__<server>__<tool>`, `family="mcp"`,
  `pattern="mcp__*"`, deskripsi `[<server>] ...`, dan parameter sama dengan `input_schema` server.
- `Hub.stop()` menutup koneksi saat shutdown.

### 4.3 Izin dan approval

- `Hub.specs(roles)` memfilter dengan `tool_allowed`, jadi sebuah tool hanya terlihat ketika pola
  role cocok dengan `mcp__<server>__<tool>`. `execute` memeriksa lagi; tidak ada bypass khusus MCP.
- Grant `mcp__*` mencakup semua server. Utamakan `mcp__<server>__*` per server.
- Handler mempertahankan default fail-closed: `approval_class="authorization"`, tanpa metadata
  `target`, `path_arg`, `scan`, atau `destructive`. MCP tidak menegosiasikan kelas approval.
- Handler mengabaikan konteks yang diterimanya: tanpa memori, tanpa skill, tanpa ledger, tanpa
  `ctx.app`. Server hanya melihat argumennya dan environment-nya sendiri. Inilah garis antara "tool"
  dan "integrasi mendalam".

### 4.4 Hasil dan kegagalan

- Blok konten teks digabung; output kosong menjadi `(no output)`; `is_error` menjadi `error: ...`.
- Panggilan memiliki timeout 60 detik. Nama terekspos yang tidak dikenal mengembalikan string error,
  tidak pernah exception.
- Hasil MCP adalah teks tidak tepercaya: data bagi model, tidak pernah instruksi, dan tidak pernah
  menjadi alasan untuk menulis memori sendiri.

### 4.5 Apa yang bukan MCP

- Ia tidak memasang kode ke engine; ia berjalan di luar pohon dan digest engine.
- Ia tidak dapat menyediakan command, prompt part, gateway, atau skill ber-scope app.
- Ia tidak pernah membaca `ctx.app` (setelah 0035); pengkabelan per app bersifat eksternal (entri
  server dan pola role terpisah).
- Ia tidak memperoleh secret dari loader plugin. `env`-nya adalah config literal; jauhkan secret
  asli dari `config.yaml` (lihat §10).
- Ia tidak menambah jalur izin kedua; role tetap satu-satunya grant.

### 4.6 Plugin versus MCP

| | Plugin | Server MCP |
| --- | --- | --- |
| Unit | manifest + digest + `register()` | server eksternal yang hidup |
| Siklus hidup | ambil, verifikasi, enable, disable, remove | entri config, konek saat boot |
| Menyediakan | tool, command, gateway yang dikirim, prompt part statis (setelah 0034) | tool saja |
| Ikatan versi | semver + rentang engine + pin digest | miliknya sendiri; engine tidak memeriksa apa pun |
| Konteks | handler menerima `ToolContext` | argumen saja; tanpa konteks |
| Prompt | part statis (setelah 0034) | tidak ada |
| App (setelah 0035) | `ctx.app` | tidak terlihat |
| Kegagalan | `unavailable`, tanpa import | satu peringatan, tool absen, engine lanjut |
| Izin | role di `execute` | role di `execute` |
| Kepercayaan | kode dipasang operator, dipin digest | proses/endpoint eksternal yang dikonfigurasi operator; output tidak tepercaya |

Perbedaannya dalam satu kalimat: **plugin berintegrasi mendalam (konteks, prompt, command, gateway
opsional, pin versi); server MCP hanya memasok tool lewat protokol kabel.**

---

## 5. Aturan komposisi

Inilah aturan yang menjaga "modular" tidak berarti "tumpang tindih".

1. **Satu transport, satu gateway.** Body yang membuka socket/serial/endpoint HTTP dan memancarkan
   `Percept` adalah gateway. Core tidak memiliki satu pun darinya.
2. **Satu unit terpasang, satu plugin.** Kode yang menambah capability tetapi tidak memiliki
   transport adalah plugin. Plugin boleh berisi beberapa tool/command; ia tidak menjadi gateway.
3. **Plugin boleh mengirim gateway; gateway tetap gateway.** Gateway dideklarasikan di
   `provides.gateways`, didaftarkan lewat jalur gateway, dan diaktifkan lewat config gateway. Ia tidak
   masuk daftar tool plugin dan tidak memperoleh semantik siklus hidup plugin.
4. **Aksi body tetap di body.** Tool/command gateway tinggal di
   `eidesoul/gateways/<name>/tools|commands` (atau pohon plugin yang mengirimnya). Core tidak pernah
   menyimpan command balas WhatsApp, tool alarm robot, atau API kalender.
5. **Tool tidak memilih transportnya.** Capability menerima `ToolContext`; ia tidak membaca nama
   transport untuk memutuskan perilaku. Jika ia butuh fakta body, fakta itu ada di `ctx`.
6. **Tidak ada jalur opsional yang diam.** Gateway, plugin, atau tool yang hilang adalah
   `unavailable`, tidak pernah sukses palsu dan tidak pernah traceback di turn (ADR-0027 §4.3).
7. **Penghapusan bersih.** Menonaktifkan plugin tidak pernah meninggalkan handler terdaftar;
   menghentikan gateway tidak pernah meninggalkan loop berjalan; kode plugin yang dihapus benar-benar
   dihapus, bukan ditutupi.
8. **MCP dikonfigurasi, bukan dipasang.** Server menambah tool ke indeks dan pemeriksaan izin yang
   sama dengan tool lain. Ia tidak menambah command, prompt part, gateway, siklus hidup, atau digest.
   Plugin boleh membungkus server MCP, tetapi plugin tetap unit yang dapat dipasang dan tetap
   memiliki packaging.

### 5.1 Setelah ADR-0034: permukaan prompt

- Gateway menyumbang tepat satu bagian body statis lewat `prompt_section()`; ia tidak dapat
  mendaftarkan prompt part dan tidak dapat menambah bagian kedua.
- Plugin menyumbang `PromptPart` statis yang dideklarasikan di `provides.prompt`, dikembalikan oleh
  `register()["prompts"]`, bernamespace `plugin:<plugin>:<slug>`, `stable=True`, `produce=None`,
  `text` tidak kosong, `len(text) <= 1200`, dan `order` diberikan loader setelah part core.
- Prompt part bukan tool: tidak ada pemeriksaan izin, tidak ada `ToolContext`, tidak ada panggilan
  async, tidak ada data pengguna. Part yang gagal validasi adalah `PluginError` sebelum plugin masuk
  jalur turn.
- Bagian body adalah satu-satunya kontribusi prompt gateway; plugin tidak dapat berpura-pura menjadi
  body.

### 5.2 Setelah ADR-0035: hint app

- Gateway boleh melaporkan `Inbound.hints["app"]` (aplikasi atau korpus tempat event itu berada). Ia
  opsional; body yang menghilangkannya melaporkan "tanpa app".
- Engine membaca hint hanya untuk memilih korpus ber-scope app. Ia tidak pernah menurunkan app dari
  nama body, thread id, atau URL.
- Tool plugin menerima `ctx.app` (dari hint yang sama) dan boleh memfilter pack-nya sendiri dengan
  itu. Ia tidak boleh membaca `ctx.gateway.name` untuk menyimpulkan app.
- Gateway yang mengimpor skill menulis lewat `ctx.skills`/`SkillStore` dengan app yang sama seperti
  yang ia laporkan di hint; ia tidak pernah menulis tabel `skills` secara langsung.
- Hint yang hilang gagal tertutup: skill ber-scope app disembunyikan, skill bersama tetap terlihat.

---

### 5.3 Ketika gateway dan plugin bekerja bersama

Keduanya boleh berkolaborasi; keduanya tidak boleh saling bergantung dua arah.

1. **Dependensi satu arah.** Entah gateway berdiri sendiri dan plugin memperkuatnya, atau satu
   plugin mengirim keduanya. Plugin tidak boleh menjadi syarat body start, dan body tidak boleh
   menjadi syarat plugin registrasi.
2. **Satu paket berarti satu plugin, satu digest.** Jika keduanya satu produk dan berversi bersama,
   kirim sebagai satu plugin yang mendeklarasikan `provides.gateways`. Gateway tetap didaftarkan dan
   diaktifkan sebagai gateway, bukan sebagai tool plugin. Satu instalasi memverifikasi satu pohon;
   config gateway tetap yang mengaktifkan body. Menonaktifkan plugin itu juga membuat body yang
   dikirimnya unavailable, dan operator melihat kopling itu di manifest.
3. **Terpisah berarti dua unit.** Jika gateway generik dan capability opsional, pertahankan dua
   paket: gateway di pohonnya, plugin di pohonnya. Plugin boleh mendokumentasikan bahwa ia
   mengharapkan gateway, tetapi engine tidak menegakkan tautan itu; gateway yang hilang membuat
   capability `unavailable`, bukan engine rusak.
4. **Tanpa import silang.** Kode gateway tidak pernah mengimpor kode plugin; kode plugin tidak
   pernah mengimpor internal gateway. Seam-nya adalah `Percept`/`Action`, `ToolContext`, dan hint
   (`app`), bukan objek Python.
5. **Tanpa izin implisit.** Plugin yang memanggil aksi body melewati pemeriksaan `execute`/role
   yang sama; ia tidak memanggil `gateway.dispatch` langsung untuk melewati policy. Jika butuh aksi
   body, body mengeksposnya sebagai tool dan role memberikannya.
6. **Menurun, bukan crash.** Menonaktifkan atau menghapus plugin membuat capability-nya absen;
   loop gateway tetap berjalan ketika ia berada di pohonnya sendiri. Menghentikan gateway
   meninggalkan tool plugin tetap dapat dipanggil kecuali yang membutuhkan body. Kegagalan mana pun
   adalah `unavailable`, bukan traceback.
7. **Satu pemilik prompt per fakta.** Gateway memiliki bagian body; plugin hanya memiliki part
   statisnya. Keduanya tidak pernah menduplikasi instruksi yang sama; satu menyatakannya dan yang
   lain merujuk capability.

Contoh: gateway robot guard plus plugin `guard-playbooks`. Gateway memiliki transport, alarm, dan
latch serta berjalan tanpa plugin. Plugin menambah panduan statis dan pencarian pengunjung; tanpa
gateway ia tetap registrasi, tetapi tool yang membutuhkan body menolak. Gateway menetapkan
`hints["app"]`; plugin membacanya sebagai `ctx.app` dan tidak pernah menyimpulkannya dari nama body.

## 6. Izin dan kepercayaan

- **Role adalah satu-satunya sumber izin.** Handler gateway atau plugin tidak punya grant default;
  entri `tools`/`commands` yang hilang menolak semuanya. Satu-satunya pemeriksaan adalah
  `eidesoul/engine/tools/__init__.py:execute`.
- **Kelas approval dideklarasikan handler**, bukan namanya. Plugin yang ingin aman mendeklarasikan
  `approval_class="none"` hanya ketika aksinya fail-safe; setiap handler tak dikenal default ke
  `authorization`.
- **Guard melihat panggilan yang dimulai model.** Ia bukan sandbox untuk kode plugin yang dipasang
  operator. Memasang plugin adalah keputusan kepercayaan operator, dibatasi digest, rentang engine,
  dan catatan kredit/lisensi.
- **Secret adalah nama env.** Tidak ada manifest, prompt part, hasil tool, atau baris log yang
  membawa nilai rahasia.
- **Body menormalisasi identitas; core tidak pernah menebaknya.** Gateway yang tidak dapat
  membuktikan suatu fakta menghilangkan hint; engine memperlakukan ketiadaan sebagai cabang aman.

---

## 7. Semantik kegagalan

| Kegagalan | Hasil |
| --- | --- |
| Gateway tidak dapat terhubung | log, tetap unavailable, engine dan body lain lanjut |
| `dispatch` gateway menerima capability tak dikenal | `GatewayError`; tidak ada verb cadangan |
| Gateway mengembalikan receipt cacat | aksi menjadi `unknown`; pemanggil tidak mencoba ulang buta |
| Manifest plugin tidak valid | `unavailable`, tanpa import, tanpa perubahan config |
| Digest plugin tidak cocok | `unavailable`, tanpa import, tanpa unpack parsial |
| `register()` plugin melempar | `unavailable`, modul dibersihkan, engine lanjut |
| Plugin mendeklarasikan capability yang tidak dikembalikan | `PluginError`; plugin tidak dimuat |
| Plugin mengembalikan capability yang tidak dideklarasikan | `PluginError`; plugin tidak dimuat |
| Server MCP gagal start atau list tool | satu peringatan; tool-nya absen; engine lanjut |
| Panggilan tool MCP timeout atau mengembalikan `is_error` | teks `error:`; turn lanjut |
| Tool MCP tidak punya grant role | ditolak di `execute`; tanpa bypass khusus MCP |
| Role tidak punya grant | ditolak di `execute`; tidak ada bypass khusus plugin |
| Worker bukan primary | standby menolak intake dan tidak mengirim apa pun (lease) |

---

## 8. Pemeriksaan konformansi

Gateway konforman ketika:

1. ia mengimplementasikan `GatewayCore` dan hanya mengimpor `eidesoul.engine.inbound`, `registry`,
   serta tipe turn yang dibutuhkannya;
2. `run()` menghasilkan `Percept` dan tidak pernah menyentuh store atau model secara langsung;
3. `dispatch()` menolak capability tak dikenal dengan `GatewayError`;
4. `prompt_section()` statis, English, dan dalam 1200 karakter;
5. tool/command-nya tinggal di pohonnya sendiri dan tidak pernah di
   `eidesoul/engine/tools|commands`;
6. ia menormalisasi identitasnya sendiri dan tidak pernah mem-parsing id transport di core;
7. dengan body dihentikan, tidak ada loop, task, atau koneksi yang bertahan.

Plugin konforman ketika:

1. `plugin.yaml` valid, digest cocok dengan pohon dan pin, serta rentang engine terpenuhi;
2. `register()` mengembalikan persis tool/command/prompt yang dideklarasikan dan tidak ada yang lain;
3. semua secret adalah nama env dan semuanya ada sebelum enable;
4. dependensinya tinggal di `deps/` dan lock image tidak berubah;
5. menonaktifkan/menghapusnya tidak meninggalkan handler, prompt part, atau modul terimpor;
6. tool-nya lolos validasi `Handler` dan pemeriksaan izin yang sama dengan tool core;
7. jika ia mengirim gateway, gateway itu memenuhi pemeriksaan gateway di atas.

Server MCP konforman ketika:

1. ia dikonfigurasi dengan tepat satu dari `command` atau `url`, dan namanya unik;
2. ia menjawab `list_tools()` dalam 20 detik dan tool-nya mengekspos input schema yang dapat dipakai;
3. setiap tool me-resolve ke `mcp__<server>__<tool>` dan mencapai model hanya di bawah grant role;
4. argumen dan hasilnya diperlakukan sebagai data tidak tepercaya, tidak pernah instruksi;
5. ia tidak memakai `ToolContext` dan tidak membutuhkan prompt part, command, atau gateway;
6. secret asli tidak ditulis ke `config.yaml` (sampai celah nama-env di §10 ditutup).

Bentuk eksekusi pemeriksaan ini tinggal di `tests/test_gateway.py`, `tests/test_plugins.py`,
`tests/test_mcp.py`, dan suite kontrak prompt. Body, plugin, atau server baru menambahkan kasusnya;
ia tidak menambah mekanisme baru.

---

## 9. Contoh kerja: robot guard

- **Gateway `robot`.** Memiliki loop serial/sensor, deteksi kehadiran, input identitas (RFID,
  keypad, suara), TTS, dan relay alarm. Ia memancarkan `Percept(hints={"app": "robot", "presence":
  True, ...})` dan men-dispatch `send_text` (ucapan) serta `guard_alarm`/`guard_control` (verb
  miliknya).
- **Tool body.** `guard_alarm` (fail-safe, `approval_class="none"`), `guard_control`
  (`authorization`, reset/unlock). Timeout dan latch tinggal di body; model tidak berada di jalur
  keselamatan.
- **Prompt.** Satu `prompt_section()` statis: "kamu guard; jangan buka tanpa konfirmasi; jika
  konfirmasi tidak ada, body membunyikan alarm." Invarian itu diulang di `soul.rules`, bukan disimpan
  sebagai skill yang dapat diambil.
- **Plugin (opsional).** Plugin `guard-playbooks` dapat menambah panduan prompt statis atau tool
  `guard_lookup` untuk tipe pengunjung. Ia tidak memiliki alarm, transport, atau latch.
- **App.** `app="robot"` mengisolasi playbook/skill robot dari korpus body lain. Ia bukan izin: role
  tetap memutuskan tool mana yang boleh dipanggil robot.

---

## 10. Celah yang diketahui (per 2026-11-04)

Ini adalah cacat terhadap kontrak ini, bukan opsi:

- `plugins.py:_register` hanya mengimpor dan mendaftarkan `tools` dan `commands`; manifest dengan
  `provides.gateways` diterima tetapi gateway-nya tidak didaftarkan. Sampai jalur gateway itu ada,
  gateway yang dideklarasikan harus gagal tertutup sebagai `unavailable`, bukan menghilang diam-diam.
- `HINT_KEYS` belum memuat `app`; ADR-0035 masih draft.
- Prompt part plugin belum ada sampai ADR-0034 mendarat; hari ini `register()` hanya mengembalikan
  tools/commands.
- `Inbound` muncul di prosa lama (termasuk `AGENTS.md`); seam yang hidup adalah `Percept`/`Action` di
  `eidesoul/engine/inbound.py`. Kontrak ini memakai nama yang hidup.
- Nilai `env` MCP dibaca dari `config.yaml` sebagai string literal; berbeda dari `secrets` plugin,
  tidak ada indireksi atau verifikasi nama env. Jauhkan secret dari mapping itu sampai celahnya
  ditutup.
- Handler MCP mempertahankan kelas default `authorization` dan tidak membawa metadata `target`,
  `path_arg`, `scan`, atau `destructive`, sehingga guard tidak dapat memperlakukan satu tool MCP
  berbeda dari yang lain lewat deklarasi.
- Tool MCP mengabaikan `ToolContext` yang diterimanya, jadi tidak dapat membaca `ctx.app` setelah
  0035 atau menulis skill lewat store engine.

---

## 11. Referensi

- `eidesoul/engine/inbound.py` — `Percept`, `Action`, `Address`, `Receipt`, `GatewayCore`, `Rich`,
  `Directory`, `PrivateThread`, `HINT_KEYS`, `PROMPT_SECTION_MAX`.
- `eidesoul/engine/plugins.py` — skema manifest, `tree_digest`, `_verify`, `_register`, status siklus
  hidup, `PluginError`.
- `eidesoul/engine/registry.py` dan `eidesoul/engine/tools/__init__.py` — `Handler`, `ToolContext`,
  `execute`, satu-satunya pemeriksaan izin.
- `eidesoul/engine/agent.py` — intake `handle(percept, gateway)`, pintu keluar `dispatch(...)`,
  penggabungan tool/command gateway.
- ADR-0027 §4.1 — gateway, tool, dan plugin adalah tiga hal berbeda.
- ADR-0028 §4.1–4.4 — unit plugin, manifest, siklus hidup, pemuatan.
- ADR-0032 — alamat dan command message; routing body, tanpa quoting di engine.
- ADR-0033 §4.5 — satu `prompt_section` body.
- ADR-0034 — prompt parts: part plugin statis, satu kontribusi body.
- ADR-0035 — namespace app: gateway memasok `hints["app"]`, plugin membaca `ctx.app`, core memfilter.
- `eidesoul/engine/jobs/mcp.py` — `Hub`, penemuan, penamaan `mcp__<server>__<tool>`, timeout,
  rendering hasil.
- `eidesoul/engine/config.py:649-656,870-895` — `MCPServerConfig` dan validasi `mcp.servers`.
- `eidesoul/__main__.py:112-113,476` — start `Hub` saat boot proses dan stop di jalur selftest;
  `eidesoul/engine/agent.py:1796-1798` — `hub.stop()` saat agent shutdown.
