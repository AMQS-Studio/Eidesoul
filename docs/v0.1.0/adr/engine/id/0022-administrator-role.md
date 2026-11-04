> Terjemahan turunan — jangan sunting; sumber: engine/0022-administrator-role.md (sha256: 509fd2b65346)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0022: Peran `administrator` — Nama, Proteksi, dan Sebutan

- **Status:** accepted
- **Implementasi:** implemented — bukti: `engine/roles.py` (`label`, `protected`, `require_administrator`, `assert_protected_roles`), migrasi `admin/roles.py` dengan backup + audit, `roles.yaml`/template/default diganti nama, `/whoami`/`/help`/guard sepakat, `tests/test_roles.py`; hasil gate: `pytest -q` 680 lulus
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** nama role `administrator` bertabrakan dengan tiga konsep lain yang sudah memakai kata yang sama (`owner_id` provenance, `administrator` pada manifest otomasi, `lease_owner`); pemilik memutuskan role diganti nama menjadi `administrator`, role itu **diproteksi** (tidak boleh dihapus), dan setiap role boleh punya **sebutan** (`label`) opsional yang memberi tahu soul bagaimana memanggil pemegangnya ("Tuhan", "Majikan", "Dev", ...)
- **Supersedes:** —
- **Terkait:** ADR-0001 (roles seed/mirror), ADR-0007 (kelas approval & matriks tool), ADR-0008 (`owner_id` provenance), ADR-0012 (`lease_owner`), ADR-0014 (`administrator` otomasi), ADR-0016 (`config_changes`), ADR-0019 (log/role), ADR-0021 (scope administrator)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0022-administrator-role.md`.

---

## 1. Konteks

Kata `administrator` dipakai untuk empat hal berbeda:

| Arti | Bukti | Ikut berubah? |
|---|---|---|
| **Role** dengan izin tertinggi | `roles.yaml:31` `owner:`; `stores/recipes.py:29` `DEFAULT_ROLES = ("owner", "member")`; `admin/templates.py:143,158-159`; dokumen `role:administrator` | **ya** → `administrator` |
| Pemilik fakta di memori | `owner_id` (ADR-0008), `owner/subject` (ADR-0006) | **tidak** |
| Pemilik otomasi | manifest `owner: person:…`, `owner=system`, `owner_roles_at_creation` (ADR-0014) | **tidak** |
| Pemegang lease | `lease_owner` (ADR-0013), "owner generation" (ADR-0012) | **tidak** |

Selain itu, soul hari ini hanya melihat nama role + deskripsi (`prompts.py:141-147`), sehingga ia tidak punya cara menyebut pemegang role selain nama orangnya. Sebutan yang diinginkan operator ("panggil dia Majikan") tidak punya tempat.

## 2. Decision drivers

- Identifier tidak ambigu: `administrator` hanya berarti role.
- Role tertinggi tidak boleh hilang karena salah edit: proteksi di loader, bukan disiplin operator.
- Sebutan adalah bagian persona/alamat untuk **role mana pun** dan **opsional**, bukan izin dan bukan identifier.
- Tanpa alias/shim: satu nama, satu arti, migrasi sekali jalan.
- Migrasi tidak boleh menurunkan hak diam-diam.

## 3. Opsi yang dipertimbangkan

| Opsi | Isi | Biaya | Risiko | Verdict |
|---|---|---|---|---|
| **A. Rename keras + migrasi sekali** | `administrator` → `administrator`, migrasi dokumen saat `config import`, tanpa alias | sedang (satu migrasi) | salah migrasi = kehilangan pemegang izin | **Dipilih** |
| **B. Alias (`administrator` tetap valid)** | dua nama untuk satu role | kecil | dua nama selamanya; melanggar aturan no-shim; membingungkan register istilah | Ditolak |
| **C. Biarkan** | tidak berubah | nol | ambiguitas empat-arti tetap | Ditolak |

## 4. Keputusan

### 4.1 Identifier role

- Nama role adalah **`administrator`**. Semua tempat yang menamai role—`roles.yaml`, dokumen `role:<name>`, `DEFAULT_ROLES`, template admin, daftar `approval.roles`, dan sebutan di prompt—memakai nama itu.
- Kata `administrator` **berhenti** menjadi nama role. Ia tetap sah untuk tiga arti lain di §1 dan tidak boleh dipakai sebagai pengganti `administrator` di kode maupun dokumen (register ADR-0009 menolaknya). Prosa lama di ADR lain yang masih menulis `administrator` untuk role dibaca sebagai `administrator`; penyisiran mekanis dilakukan saat implementasi (test grep literal role), bukan dengan mengubah arti data.

### 4.2 Role terproteksi

- `administrator` membawa `protected: true` (dipaksa loader; nilai `false` ditolak saat load).
- Loader **menolak boot** bila: role `administrator` tidak ada; `users`-nya kosong; `tools` tidak mencakup `"*"`; atau `commands` tidak memuat `approve`.
- Role ini **tidak bisa dihapus** dan identifier-nya **tidak bisa di-rename** lewat editor role/manajer admin; perubahan hanya lewat `roles.yaml` + `monoagens config import` (dengan backup) atau tindakan owner di host.
- Tidak ada jalur pembuatan role `administrator` dari percakapan; tool/command apa pun yang menulis role menolak nama ini kecuali lewat import.

### 4.3 Sebutan (`label`) — opsional untuk setiap role

- Setiap role **boleh** punya `label` (opsional, default kosong), bukan hanya `administrator`. Fungsinya tunggal: memberi tahu soul **siapa pemegang role ini dan hendak dipanggil apa**.
- `label` masuk ke blok `## Roles` di prompt (`roles_block`, `prompts.py:141-147`) sebagai keterangan alamat, mis.:
  `- administrator: role ...; panggil dia "Majikan"`.
- Bila `label` kosong: soul memakai nama orang (`people.name`) bila ada; bila tidak, memakai nama role. Label tidak pernah dipakai untuk mengisi nama pada log, audit, atau id.
- `label` **tidak pernah** menjadi izin, tidak pernah dibandingkan di kode, dan tidak menggantikan identifier; kode selalu membandingkan `role.name`.
- `label` adalah teks dari operator (T0), bukan dari percakapan: hanya bisa diubah lewat `roles.yaml` + `config import`, tidak lewat tool/command. Panjang dibatasi (mis. 40 karakter) dan tidak boleh kosong-spasi.
- Mengubah `label` tidak memerlukan migrasi, tidak mengubah audit trail selain `config_changes`, dan tidak mengubah siapa yang boleh approve.

### 4.4 Migrasi sekali jalan

- `monoagens config import` menjalankan migrasi: dokumen `role:administrator` → `role:administrator` (nama dokumen dan isi `name`), key `owner:` di `roles.yaml` → `administrator:`, daftar role di `approvals.roles` yang masih menyebut `administrator` → `administrator`, dan field `administrator` pada baris otomasi yang menunjuk role (bila ada) dibiarkan (itu pemilik, bukan role).
- Backup ditulis sebelum perubahan; migrasi idempoten dan tercatat di `config_changes` sebagai `role_rename`.
- Boot **gagal dengan pesan yang menyebut perintah migrasi** bila masih ada `role:administrator` dan tidak ada `role:administrator`.
- Bila **keduanya ada**, boot gagal (tidak ada penggabungan otomatis): operator memilih mana yang benar, karena menggabungkan bisa memberi izin ke pemegang yang salah.

### 4.5 Titik kode yang berubah

| Berkas | Perubahan |
|---|---|
| `roles.yaml` | key `owner:` → `administrator:` + `label` (opsional, semua role), `protected: true` |
| `monoagens/engine/stores/recipes.py:29` | `DEFAULT_ROLES = ("administrator", "member")` |
| `monoagens/admin/templates.py:143,158-159` | template role default |
| Pemeriksaan nama role di engine | semua literal `"owner"` yang berarti role → `"administrator"` |
| `monoagens/engine/turn/prompts.py:141-147` | `roles_block` menyertakan `label` bila ada (`; panggil dia "…"`) |
| `ADR-0015` `redact_infra: non_owner` | nilai → `non_administrator` |
| `ADR-0019` `status_owner_only` | → `status_administrator_only` |
| `ADR-0007` role approver | → `(administrator)` |

Data yang **tidak** berubah: kolom `owner_id`, `thread_people`, `lease_owner`, dan `administrator` pada manifest otomasi.

## 5. Konfigurasi

```yaml
roles:
  administrator:
    name: administrator
    label: administrator      # opsional; bebas: "Tuhan", "Majikan", "Dev"
    protected: true           # dipaksa loader; false ditolak
    tools: ["*"]
    memory_admin: true
    commands: ["*"]           # wajib memuat approve
  member:
    name: member
    label: ""                 # opsional; kosong -> pakai nama orang / nama role
    tools: [read, grep, fetch, web_search, sandbox, chat_history, memory, google, send]
    memory_admin: false
```

## 6. Metrik dan gate

| Metrik | Definisi | Gate |
|---|---|---|
| `role_load_error_total` | boot ditolak karena role administrator tidak sah | 0 pada instalasi sehat |
| `role_rename_migrations_total` | migrasi `administrator` → `administrator` | 0 atau 1 per instalasi; > 1 = migrasi tidak idempoten |
| `label_changes_total` | perubahan sebutan role mana pun | tercatat di `config_changes` |
| `role_write_rejected_total` | percobaan hapus/rename administrator | > 0 pada test, 0 pada operasi normal |

## 7. Acceptance criteria

- [ ] `roles.yaml` dengan `owner:` dan dokumen `role:administrator` memicu boot gagal + pesan berisi perintah migrasi.
- [ ] `config import` memigrasikan keduanya, menulis backup, idempoten, dan tercatat sebagai `role_rename`.
- [ ] Bila `role:administrator` dan `role:administrator` sama-sama ada, boot gagal tanpa menggabungkan.
- [ ] Menghapus role `administrator` lewat editor/command ditolak; mengubah identifier-nya ditolak; `protected: false` ditolak saat load.
- [ ] `users` kosong, `tools` tanpa `"*"`, atau `commands` tanpa `approve` → boot gagal.
- [ ] `label` dapat dipasang pada **role mana pun** (test: `member` + `administrator`), dan `roles_block` memuatnya (`; panggil dia "…"`).
- [ ] `label` kosong pada role mana pun → prompt memakai nama orang atau nama role; tidak ada label kosong yang tercetak.
- [ ] Mengubah `label` tidak mengubah hasil pemeriksaan izin apa pun (test: label "Tuhan" vs kosong, matriks izin identik).
- [ ] `label` tidak bisa diubah dari percakapan (tool/command tulis role menolak), dan tidak pernah muncul di log/audit sebagai identifier.
- [ ] Grep test: tidak ada literal `"owner"` yang berarti role di `monoagens/` dan `roles.yaml`.
- [ ] `owner_id`, `lease_owner`, dan `administrator` pada otomasi tetap berfungsi setelah migrasi (test round-trip).
- [ ] `approvals.roles` yang memuat `administrator` ikut dimigrasikan; approval lama yang tidak bisa dipetakan ditandai dan ditolak saat `/approve`, bukan dieksekusi.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Migrasi salah → tidak ada pemegang izin | backup sebelum tulis, fail-fast, larangan boot tanpa administrator |
| Dua dokumen role (owner + administrator) | boot gagal; operator memilih satu, tidak ada merge otomatis |
| Label dipakai sebagai identifier atau izin | kode hanya membandingkan `role.name`; test label vs izin |
| Label dipakai menyelipkan instruksi lewat percakapan | hanya bisa diubah lewat `roles.yaml` + import (T0), bukan tool/command |
| Role terproteksi terkunci total saat salah konfigurasi | pesan boot menyebut field yang salah; perbaikan lewat `roles.yaml` + import |
| Approval lama menunjuk role yang sudah tidak ada | ditandai saat migrasi; `/approve` menolak dengan alasan jelas |
| Editor admin menulis ulang role | endpoint role menolak `administrator` untuk hapus/rename, hanya `label` |

## 9. Interaksi dengan ADR lain

- **ADR-0007** — kelas approval menyebut penyelesai `authorization` sebagai `administrator`, bukan `administrator`.
- **ADR-0001** — `roles.yaml` tetap seed + mirror; `config import` adalah satu-satunya jalur tulis yang sah.
- **ADR-0008** — `owner_id` tidak berubah arti maupun namanya; ia tetap provenance, bukan role.
- **ADR-0014** — `administrator` pada manifest otomasi tetap berarti pemilik pekerjaan; tidak ikut di-rename.
- **ADR-0016** — `config_changes` mencatat `role_rename`; tidak ada perubahan DDL.
- **ADR-0019** — log/`/status` memakai `role.name`; label tidak pernah masuk sebagai identifier.
- **ADR-0021** — scope `administrator` (seluruh workspace, SSRF tetap) memakai nama baru.
- **ADR-0009** — register menambahkan `administrator` dan `label`, serta menolak `administrator`/`admin`/`root` sebagai pengganti nama role.

## 10. Referensi kode

- `roles.yaml:31` — key role `administrator` (target rename).
- `monoagens/engine/stores/recipes.py:29` — `DEFAULT_ROLES`.
- `monoagens/admin/templates.py:143,158-159` — template role default.
- `monoagens/engine/turn/prompts.py:141-147` — `roles_block` (tempat `label` dipakai).
- `monoagens/engine/access/permissions.py` — `normalize`/`allows` (nama role dinormalisasi di sini).
- `monoagens/engine/config.py` — bagian `roles` (seed/import).
- `monoagens/engine/schema.py` — `people.name` (fallback alamat bila label kosong); tidak ada perubahan tabel role.
- `AGENTS.md` — aturan role di bagian Safety (sebutan `administrator`).

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0022-administrator-role.md`.*
