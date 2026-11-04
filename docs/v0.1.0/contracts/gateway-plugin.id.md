# Kontrak Gateway dan Plugin

**Cakupan:** permukaan ekstensi engine Eidesoul: bagaimana sebuah body (gateway) dan bundel
kemampuan yang dapat dipasang (plugin) terhubung, apa yang dimiliki masing-masing, dan apa yang
tidak boleh dilakukan keduanya.
**Konteks:** ditulis setelah ADR-0034 (prompt parts) dan ADR-0035 (skill ber-scope app). Klausa yang
bergantung pada keduanya ditandai **[setelah 0034]** dan **[setelah 0035]** dan mengikat ketika ADR
masing-masing keluar dari `draft`.
**Status:** kontrak normatif. Jarak antara berkas ini dan kode adalah cacat, bukan tafsir alternatif.
**Versi English:** `gateway-plugin.md`.

---

## 1. Tiga kata

Sebuah **gateway** adalah body. Ia memindahkan event antara dunia luar dan engine serta memiliki aksi
transport-specific dari body itu.

Sebuah **plugin** adalah unit kode kemampuan yang dapat dipasang. Ia diambil, diverifikasi,
diaktifkan, dinonaktifkan, dan dihapus sebagai satu kesatuan; ia menyumbang tool, command, dan
(setelah 0034) prompt part statis.

Sebuah **tool** adalah kemampuan yang dipanggil engine. Sebuah **command** adalah kemampuan
operasional yang didispatch engine. Tool dan command bukan unit ekstensi: keduanya adalah *isi*
yang dapat disediakan oleh modul core, gateway, atau plugin.

Ketiganya tidak dapat dipertukarkan:

| Pertanyaan | Jawaban |
| --- | --- |
| Apakah ia memiliki transport, run loop, dan aksi tubuhnya sendiri? | gateway |
| Apakah ia kode yang dapat dipasang/dihapus dengan manifest dan digest? | plugin |
| Apakah ia kemampuan yang dipanggil model atau operator? | tool atau command |
| Bolehkah gateway "sekadar plugin"? | Tidak. Ia boleh *dikirim* oleh plugin, tetapi didaftarkan dan diaktifkan sebagai gateway (ADR-0028 §4.1). |
| Bolehkah plugin membuka transportnya sendiri? | Hanya lewat gateway yang ia kirim dan deklarasikan; tidak pernah sebagai efek samping saat import. |
| Bolehkah core memuat aksi gateway? | Tidak. Aksi gateway tinggal di `eidesoul/gateways/<name>/tools|commands`; aksi plugin tinggal di pohon plugin. |

Uji satu baris: **gateway adalah dari mana byte datang; plugin adalah apa yang dapat dipasang; tool
adalah apa yang boleh dipanggil model.**

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

## 4. Aturan komposisi

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

### 4.1 Setelah ADR-0034: permukaan prompt

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

### 4.2 Setelah ADR-0035: hint app

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

## 5. Izin dan kepercayaan

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

## 6. Semantik kegagalan

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
| Role tidak punya grant | ditolak di `execute`; tidak ada bypass khusus plugin |
| Worker bukan primary | standby menolak intake dan tidak mengirim apa pun (lease) |

---

## 7. Pemeriksaan konformansi

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

Bentuk eksekusi pemeriksaan ini tinggal di `tests/test_gateway.py`, `tests/test_plugins.py`, dan
suite kontrak prompt. Body atau plugin baru menambahkan kasusnya; ia tidak menambah mekanisme baru.

---

## 8. Contoh kerja: robot guard

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

## 9. Celah yang diketahui (per 2026-11-04)

Ini adalah cacat terhadap kontrak ini, bukan opsi:

- `plugins.py:_register` hanya mengimpor dan mendaftarkan `tools` dan `commands`; manifest dengan
  `provides.gateways` diterima tetapi gateway-nya tidak didaftarkan. Sampai jalur gateway itu ada,
  gateway yang dideklarasikan harus gagal tertutup sebagai `unavailable`, bukan menghilang diam-diam.
- `HINT_KEYS` belum memuat `app`; ADR-0035 masih draft.
- Prompt part plugin belum ada sampai ADR-0034 mendarat; hari ini `register()` hanya mengembalikan
  tools/commands.
- `Inbound` muncul di prosa lama (termasuk `AGENTS.md`); seam yang hidup adalah `Percept`/`Action` di
  `eidesoul/engine/inbound.py`. Kontrak ini memakai nama yang hidup.

---

## 10. Referensi

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
