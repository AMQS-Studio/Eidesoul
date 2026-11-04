> Terjemahan turunan — jangan sunting; sumber: engine/0036-role-model.md (sha256: 413275b7650a)

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** memperluas ADR-0022 (role `administrator`), ADR-0007 (otorisasi capability), ADR-0018 (fakta gateway), ADR-0033 (core netral)
- **Supersedes:** —
- **Terkait:** ADR-0016 (migrasi dan dokumen), ADR-0035 (namespace app, tidak terkait grant)
- **Implementasi:** draft — dokumen role, pemilihan berbasis identitas, dan command bootstrap diputuskan di sini; pohon kode masih menerima hint role dari body mana pun, membiarkan `administrator.users` kosong saat init, dan membiarkan role mana pun mengklaim `protected`.
- **Catatan lokasi:** keputusan engine (ia memiliki identitas, role, dan gate boot), jadi duduk di bawah `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0036-role-model.md`.

---

## 1. Konteks

Role adalah bundel grant bernama. Dokumen role membawa `users` (id person), `tools`, `commands`,
`context`, `priority`, `memory_admin`, `label`, dan `protected` (`access/roles.py:27-38`). Nama
`administrator` dicadangkan dan dilindungi (`roles.py:19`); gate boot mewajibkannya ada, memiliki
setidaknya satu user, membawa `tools: ["*"]`, dan mengizinkan `approve` (`roles.py:84-99`). Nama
lain adalah data: role baru tidak membutuhkan kode.

Identitas sudah terpisah dari transport. Body menyerahkan alamat, core memberi id opaque `per_...`
lewat `IdentityStore.resolve` (`agent.py:851-862`, `identity.py:107-145`), dan pencarian role hanya
memakai `inbound.actor` (`roles.py:60-72`). Seed yang diperiksa berisi tiga role dan tanpa user.
Diukur pada 2026-11-04:

| Celah | Bukti |
| --- | --- |
| hint role dipercaya dari setiap body | `agent.py:654-662` membaca `hints["roles"]`; `inbound.py:13-20` mendokumentasikannya hanya untuk konsol lokal |
| administrator seed tidak punya user | `roles.yaml:5`, `admin/templates.py:306-320`; `require_administrator()` menolaknya |
| init tidak dapat mengisi user administrator | `eidesoul init --label` mengisi `label`, bukan `users` (`admin/init.py:227-230`) |
| seed terikat budaya dan usang | `description: Committee member`; command `cari`, `memori`, `kirim`; tool `google` (`roles.yaml:31-46`) |
| `protected` tidak dibatasi ke administrator | `parse_role` menerima `protected: true` pada role apa pun (`roles.py:165-174`), sementara `prompts.py:42` dan `status.py:61` memperlakukan role protected mana pun sebagai privileged |
| `users` menerima string apa pun | `parse_role` memanggil `_str_tuple` lalu berhenti (`roles.py:151-183`); alamat mentah diam-diam tidak cocok dengan `per_...` mana pun dan jatuh ke `default_role` |

Modelnya sudah benar arahnya: izin datang dari daftar eksplisit, identitas opaque, dan invarian
administrator diperiksa saat boot. Seed, hint role, dan flag `protected` adalah tempat model itu
belum solid.

## 2. Decision drivers

1. **Satu sumber izin.** Hanya `tools` dan `commands` yang memberi. *Tipe* role yang memberi izin
   akan menciptakan sumber kedua dan membuat daftar tidak lagi otoritatif.
2. **Satu invarian keselamatan, bukan kategori.** `administrator` ada karena role terlindungi harus
   selalu dapat menyetujui dan tidak boleh hilang; ia bukan cetakan untuk kelas role.
3. **Identitas, bukan transport.** Grant melekat pada person kanonik, tidak pernah pada gateway,
   thread, atau URL. Body boleh menyatakan fakta; ia tidak boleh menyatakan otoritas.
4. **Gagal tertutup.** Role tak dikenal, grant hilang, user administrator hilang, dan hint role dari
   body tak tepercaya semuanya menolak; tidak ada yang diam-diam memperluas.
5. **Membosankan dan eksplisit.** Bundel bernama dengan daftar datar itu prediktabel; hierarki tipe,
   inheritance, dan scope per-room menambah aturan merge yang harus diuji sebelum dipercaya.
6. **Bootstrap harus mungkin.** Instalasi baru harus dapat membuat administrator pertamanya tanpa
   menyunting YAML manual atau melemahkan gate boot.

## 3. Opsi yang dipertimbangkan

**A. Field `type: admin|user|guest` dengan grant implisit.** Ditolak: ia menjadi sumber izin kedua,
memunculkan "berapa banyak admin", dan tetap membutuhkan nama terlindungi untuk gate boot. Dua cara
untuk mengatakan hal yang sama lebih buruk daripada satu.

**B. Field `kind` hanya tampilan.** Tidak ditolak selamanya, tetapi ditunda: belum ada konsumen
sampai panel butuh pengelompokan, dan menambah metadata mati sekarang mengundang seseorang
memperlakukannya sebagai otoritas. Jika ditambahkan nanti, ia harus divalidasi sebagai enum dan tidak
memberi grant apa pun.

**C. Konfigurasi role per gateway (`gateway: whatsapp: users:`).** Ditolak: orang yang sama bisa
menjadi administrator di satu transport dan guest di transport lain, core akan mengenal nama
transport, dan pemetaan alamat gateway sudah tinggal di `person_links`. Grant per body juga tidak
diperlukan: `ToolIndex` sudah hanya mengekspos tool body yang aktif.

**D. Bundel bernama, satu nama terlindungi hardcoded, grant berbasis identitas (dipilih).**

**E. Pertahankan hint role dengan allowlist sumber.** Ditolak untuk fase ini: allowlist tetap
menjadikan kabel sebagai tempat otoritas diklaim. Konsol lokal dapat memakai identitas tertaut
normal, yang menghapus hint dan key config tambahan.

## 4. Keputusan

### 4.1 Role tetap bundel izin bernama

Role adalah entri di `roles.yaml` / dokumen `role:<name>` dengan daftar `tools` dan `commands`
datar. Tidak ada field `type`, `kind`, atau `level` yang memiliki otoritas. Role bernama `user` atau
`admin` hanyalah nama; ia memberi hanya apa yang tertulis di daftarnya.

`kind` hanya-tampilan BOLEH ditambahkan nanti untuk panel, dengan enum, tanpa grant default, dan
tanpa penggunaan di policy. Ia tidak ada sekarang.

### 4.2 `administrator` tetap satu-satunya nama dicadangkan

- Ia harus ada, membawa `tools: ["*"]`, mengizinkan `approve`, dan memiliki setidaknya satu user
  `per_...`.
- Ia tidak dapat dihapus atau dinamai ulang; hanya `label` dan daftar user-nya yang boleh berubah,
  lewat `roles.yaml` plus `eidesoul config import` (ADR-0022).
- Tidak ada alias `admin`. Role kedua dengan `"*"` diperbolehkan dan eksplisit, tetapi ia tidak
  terlindungi dan tidak memenuhi gate boot.
- `priority` mengurutkan role yang cocok; ia tidak membuat satu role menimpa yang lain. Grant
  bersifat aditif.

### 4.3 `protected` hanya milik administrator

`parse_role` WAJIB menolak `protected: true` pada role mana pun yang namanya bukan `administrator`.
Flag itu mempertahankan kedua makna yang sudah dimilikinya: role tidak dapat dihapus atau dinamai
ulang, dan ia diperlakukan sebagai privileged untuk diagnostik dan redaksi. Makna-makna itu tidak
dipecah menjadi dua field dalam ADR ini; satu flag, satu pemilik, adalah kontrak yang lebih kecil.

Privilege adalah satu predikat, dipakai di mana-mana:

```python
def privileged(role) -> bool:
    return role.protected or "*" in role.tools
```

`status` dan jalur redaksi prompt WAJIB memakai predikat ini, bukan aturan kedua. Role kustom dengan
`"*"` secara eksplisit privileged; role dengan `protected` adalah administrator.

### 4.4 Pemilihan role hanya berbasis identitas

- `Inbound.hints["roles"]` dihapus dari `HINT_KEYS` dan dari setiap produsen. Body tidak dapat
  menamai role.
- `Agent._role_set` menghapus cabang override dan selalu memanggil `roles.for_person(inbound.actor)`.
- `gateways.cli.role` dihapus. User konsol adalah alamat normal (`user: local`), di-resolve oleh
  `IdentityStore` seperti body lain; operator menautkan alamat itu ke person administrator sekali,
  saat bootstrap.
- Hint role yang tetap datang diabaikan; ia tidak pernah memperluas grant.

### 4.5 `users` berisi id person, bukan alamat

`parse_role` memvalidasi setiap entri dengan `is_person_id` (`per_` plus sepuluh karakter base32,
`identity.py:30-48`). Alamat transport, nomor telepon, JID, atau label arbitrer adalah `RoleError`
saat load. `per_...` yang valid tetapi belum punya baris diperbolehkan (person mungkin dibuat
kemudian); pencocokan tidak melakukan apa pun sampai baris dan link ada.

`people adopt` tetap menjadi jalur migrasi untuk alamat mentah lama; dokumen role ditulis ulang ke id
`per_...` hasil adopsi, bukan disimpan sebagai bentuk kedua.

### 4.6 Bootstrap adalah command eksplisit dan teraudit

Subcommand baru membuat administrator pertama tanpa menyunting YAML manual:

```text
eidesoul people add <gateway> <address> --role administrator [--name <display name>]
```

Ia melakukan satu transaksi: membuat baris `people` dengan `per_...` baru, menyisipkan baris
`person_links` untuk `gateway`/`address`, menambahkan id ke `users` dokumen role, menulis mirror
`roles.yaml`, dan mencatat perubahan di `config_changes` serta action ledger. Ia menolak gateway tak
dikenal, role tak dikenal, role yang bukan `administrator` untuk user pertama (kecuali role sudah
punya user), dan alamat yang sudah tertaut ke person lain.

`eidesoul init` mencetak command ini sebagai langkah berikutnya yang pertama. `eidesoul run` tetap
memakai gate boot yang ada: tanpa user administrator ia menolak dan menyebut command-nya. Ia tidak
pernah memberi grant otomatis ke pengirim pertama.

### 4.7 Seed netral dan minimal

`roles.yaml` dan `admin/templates.ROLES` diperbarui bersamaan:

- Deskripsi English; tanpa "Committee member".
- Tanpa alias command (`cari`, `memori`, `kirim`) dan tanpa grant verb Indonesia.
- Tanpa `google` (atau tool plugin apa pun) di role member default; operator menambahkannya saat
  plugin diaktifkan.
- Member default hanya menyimpan capability bawaan yang selalu tersedia: `read`, `grep`, `fetch`,
  `web_search`, `chat_history`, `memory`, `info`; command `help`, `ping`, `link`, `unlink`,
  `whoami`, `status`, `tools`. Role guest mempertahankan daftar yang lebih sempit.
- `administrator.users` tetap kosong di berkas; ia diisi oleh `people add`, bukan placeholder.

### 4.8 Tanpa deny, inheritance, atau grant per-room di fase ini

Grant bersifat aditif. Sebuah role tidak dapat mengurangi tool yang diberikan role lain. Tidak ada
`extends`, tidak ada daftar deny, dan tidak ada role per-thread. Jika operator membutuhkan "member di
DM, guest di grup", itu keputusan policy baru dengan ADR sendiri; ia bukan trik nesting YAML.
Keterbatasan ini didokumentasikan di kontrak role.

## 5. Konfigurasi

- `gateways.cli.role` dihapus; tidak ada key pengganti.
- `engine.default_role` tetap (fallback untuk person yang belum tertaut), dan harus menamai role
  yang ada.
- `engine` tidak memperoleh key tipe role. Satu-satunya permukaan baru adalah subcommand `people
  add`.
- `roles.yaml` tetap seed plus mirror; dokumen `role:<name>` tetap otoritatif.

## 6. Metrik dan gate

Hitungan yang harus didorong ke nol, diukur 2026-11-04:

| Celah | Jumlah | Di mana |
| --- | --- | --- |
| tipe role dengan otoritas | 0 (jaga tetap nol) | — |
| hint role yang dipercaya dari body | 1 | `agent.py:654-662`, `inbound.py:13-20` |
| key role CLI | 1 | `gateways.cli.role` |
| user non-`per_` di seed | 0 | `roles.yaml`, `admin/templates.py` |
| alias usang di seed | 3 | `cari`, `memori`, `kirim` |
| role protected selain administrator | 1 mungkin | `roles.py:165-174` |

Gate-nya:

1. `protected: true` pada `member` adalah `RoleError`; pada `administrator` diterima dan ditegakkan.
2. `Percept` yang membawa `hints["roles"]` tidak dapat mengubah set role; `HINT_KEYS` tidak lagi
   memuat `roles`; tes CLI tidak lagi mengharapkannya.
3. Seed baru gagal `require_administrator`; `people add ... --role administrator` membuatnya lolos;
   id muncul di dokumen DB dan mirror `roles.yaml`.
4. Entri `users` yang bukan `per_...` ditolak saat load; id valid tetapi tak dikenal diperbolehkan.
5. Seed tidak memuat `Committee member`, `cari`/`memori`/`kirim`, dan `google`.
6. Dua role yang mencantumkan satu orang menghasilkan gabungan grant; `priority` hanya mengubah
   urutan.
7. `status` dan redaksi prompt sepakat pada `privileged(role)` untuk role dengan `"*"` dan untuk
   administrator.
8. `pytest -q` dan tes gateway CLI diperbarui; tidak ada tes yang mengunci hint yang dihapus.

## 7. Kriteria penerimaan

- Dokumen role memvalidasi bentuk dan `users` saat load, dengan error yang menyebut nilai
  bermasalah.
- Tidak ada jalur kode yang membaca role dari `Percept.hints`; resolusi identitas adalah satu-satunya
  pemilih.
- Konsol mencapai administrator lewat person tertaut, bukan lewat hint role.
- Instalasi baru dapat mencapai administrator yang berfungsi dengan satu command terdokumentasi,
  tanpa menyunting YAML dan tanpa melemahkan gate boot.
- Seed berbahasa English, netral, minimal, dan bebas alias serta grant plugin opsional.
- Keterbatasan gabungan aditif didokumentasikan; tidak ada perilaku deny/inheritance/per-room yang
  tersirat.
- Tidak ada `type` role; menambahkannya nanti membutuhkan ADR baru.

## 8. Risiko dan mitigasi

- **Operator kehilangan pintasan role konsol.** Mitigasi: `people add cli local --role
  administrator` dicetak oleh init; user konsol selebihnya adalah identitas normal.
- **`per_...` valid ditulis sebelum person ada.** Mitigasi: id hanyalah string; `people add` membuat
  barisnya dalam satu transaksi, dan `people adopt` memperbarui alamat lama.
- **Role kustom dengan `"*"` privileged tetapi tidak protected.** Mitigasi: itu eksplisit dan
  disengaja; hanya gate boot yang bergantung pada nama terlindungi, bukan pada setiap role kuat.
- **Pembersihan seed menghapus grant yang diandalkan operator.** Mitigasi: pra-rilis, tanpa
  kompatibilitas; operator menambahkan grant kembali ke `roles.yaml` lalu import.
- **Command `people add` menjadi jalur tulis kedua.** Mitigasi: ia memakai ulang jalur import role
  dengan backup, audit, dan document store yang sama; satu command, bukan store baru.
- **Menghapus hint role merusak tes dan skrip lokal.** Mitigasi: satu commit migrasi memperbarui
  config CLI, fixture, dan tes arsitektur dalam perubahan yang sama.

## 9. Interaksi dengan ADR lain

- ADR-0022: nama, proteksi, dan gate boot tetap; ADR ini menghapus pintasan yang membiarkan body
  mengklaim role dan mengisi daftar user secara eksplisit.
- ADR-0007: kelas approval dan matriks tool tidak berubah; identitas hanya memilih set role.
- ADR-0018: gateway menyerahkan alamat, bukan role; body tetap tidak tepercaya untuk otoritas.
- ADR-0033: tidak ada nama gateway atau transport yang masuk ke logika role; CLI ditautkan seperti
  body lain.
- ADR-0035: scope app merutekan korpus skill; ia tidak mengubah role atau grant.
- ADR-0016: tidak ada perubahan skema; `people`, `person_links`, dan dokumen role sudah ada.

## 10. Referensi kode

- `eidesoul/engine/access/roles.py:19,27-38,60-72,84-108,151-183` — nama dicadangkan, field Role,
  pencarian identitas, gate boot, dan parsing.
- `eidesoul/engine/access/identity.py:30-48,107-145` — bentuk `per_...` dan resolusi.
- `eidesoul/engine/agent.py:654-662,851-862` — cabang hint role dan penyerahan identitas.
- `eidesoul/engine/inbound.py:13-20,47-53` — daftar hint dan `HINT_KEYS`.
- `eidesoul/gateways/cli/config.py:8-21` dan `gateways/cli/gateway.py:66-67` — opsi role CLI dan
  hint yang ia tulis.
- `eidesoul/engine/turn/prompts.py:42`, `eidesoul/engine/commands/status.py:61` — situs predikat
  privilege.
- Jalur import `eidesoul/engine/access/roles.py` dan `eidesoul/admin/roles.py` — jalur tulis
  teraudit yang dipakai ulang `people add`.
- `roles.yaml`, `eidesoul/admin/templates.py:306-320`, `eidesoul/admin/init.py:227-230` — seed dan
  perilaku `--label`.
- `tests/test_roles.py`, `tests/test_cli_gateway.py`, `tests/test_architecture.py` — suite yang
  diubah keputusan ini.
