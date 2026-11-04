> Terjemahan turunan — jangan sunting; sumber: engine/0035-app-scoped-skills.md (sha256: cacfd1143d39)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0035: Skill Ber-scope App — Namespace di Atas `scope`, Bukan Audiens Keempat

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** perluasan ADR-0020 (memori prosedural), ADR-0018 (fakta body), ADR-0006 (plafon audiens); lanjutan ADR-0033 (core netral) dan ADR-0034 (prompt parts, draft)
- **Supersedes:** —
- **Terkait:** ADR-0031 (skema dan anggaran modul), ADR-0016 (migrasi), ADR-0027/0028 (unit plugin), ADR-0032 (alamat dan body)
- **Implementasi:** implemented — `turns.app`, `episodes.app`, `skills.app`, dan `ix_skills_app_scope` ada di migrasi 0017; hint body, retrieval, write, dan signature episode sudah diteruskan; matriks visibilitas dan jalur hint turn diuji.
- **Catatan lokasi:** keputusan engine (memperluas memori prosedural dan kontrak inbound), jadi ia duduk di bawah `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0035-app-scoped-skills.md`.

---

## 1. Konteks

Skill adalah memori prosedural: diturunkan dari episode atau diimpor operator, terlihat di bawah salah satu dari tiga audiens, dan diinjeksi ke prompt berdasarkan relevansi di dalam anggaran (ADR-0020). Audiensnya eksplisit:

- `SkillStore._visible` mengembalikan global untuk semuanya, thread ketika `stated_by` adalah thread saat ini, dan person ketika `stated_by` adalah orang saat ini (`stores/skills.py:164-170`).
- `skills.scope` dibatasi ke `('thread','person','global')` (`schema.py:485`).
- `retrieve()` menerima query, thread, person, dan katalog tool; ia tidak punya gagasan tentang aplikasi (`stores/skills.py:205-240`), dan agent tidak mengirim nilai semacam itu (`agent.py:1309-1316`).
- Jalur pembelajaran menulis scope dari episode: episode thread menghasilkan `scope=thread`, episode global menghasilkan `scope=global` (`memory/episodes.py:176`, `jobs/cognition.py:948-980`, `:1069-1078`). Tidak ada provenance aplikasi pada `turns` atau `episodes` (`schema.py:827-845`, `:854-878`).
- Body sudah bisa memberi fakta ke engine lewat `Inbound.hints` (`dm`, `mention`, `reply`, `group`, `presence`, `self`, `roles`; `inbound.py:13-20`). Tidak ada key `app`.
- Workaround ada dan nyata: plugin bisa menyimpan pack sendiri dan mengekspos tool yang memfilter `ctx.gateway.name`; impor thread-scoped auto-inject di satu thread tetap; skill yang body-nya menyebut tool yang tidak dipegang body saat ini dilewati (`stores/skills.py:224-229`). Tidak satu pun menyatakan "korpus ini milik app A" sambil tetap auto-inject di semua thread app A.
- Satu body bisa melayani beberapa aplikasi (gateway HTTP dengan tenant) dan satu aplikasi bisa lintas body (WhatsApp plus web). Jadi "body" adalah sumbu yang salah untuk korpus.

## 2. Decision drivers

1. **Korpus dan audiens adalah pertanyaan berbeda.** "Aplikasi mana yang memiliki prosedur ini" ortogonal terhadap "siapa yang boleh melihatnya".
2. **Body adalah transport.** Satu body bisa melayani banyak app dan satu app bisa lintas body; identitas app harus datang dari body sebagai data, bukan dari core yang mengenal nama gateway.
3. **Gagal tertutup.** Turn tanpa app hanya melihat skill bersama; skill ber-scope app tidak pernah bocor ke app lain karena hint-nya hilang.
4. **Pembelajaran menyimpan provenance.** Prosedur yang dipelajari di app A tetap di app A, dan dua run dengan tool yang sama di app berbeda tidak boleh melebur jadi satu skill.
5. **Satu jalur memori.** Skill tetap memori prosedural ADR-0020; namespace adalah kolom, bukan store kedua.
6. **Tidak ada literal app di core.** Nama app adalah data yang ditulis operator dan body; engine memvalidasi bentuknya dan tidak pernah memberi default.

## 3. Opsi yang dipertimbangkan

**A. Tambah `scope='app'`.** Ditolak: satu enum tidak bisa menyatakan "app A, dan hanya orang ini" atau "app A, dan hanya thread ini"; `stated_by` akan berarti thread, orang, atau app tergantung nilainya. Murah, tapi membatasi model pada visibilitas seluruh app.

**B. Jadikan `body` sebagai namespace.** Ditolak: body adalah transport dan tempat routing tenant. Satu app lintas WhatsApp dan web akan terpecah korpusnya; satu body HTTP yang melayani dua tenant akan menggabungkannya.

**C. Pack milik plugin saja.** Diterima sebagai jalur pelengkap untuk korpus besar atau terindeks eksternal, bukan sebagai model: ia tidak bisa auto-inject, dan memaksa setiap app mengimplementasikan ulang retrieval, prioritas, dan decay.

**D. Namespace `app` nullable di atas `scope`, diisi hint `app` (dipilih).** Satu kolom per record, dua filter saat baca, dan logika scope yang ada tidak berubah.

**E. Konvensi thread-per-app.** Diterima hanya sebagai jembatan migrasi: ia bekerja hari ini tanpa perubahan skema, tetapi memaksa satu thread per app dan tidak mencakup app dengan banyak percakapan.

## 4. Keputusan

### 4.1 Datanya

Tiga kolom nullable, satu index, satu migrasi (`0017`):

```text
turns.app     String(40) NOT NULL DEFAULT ''   -- provenance satu turn (hint body)
episodes.app  String(40) NOT NULL DEFAULT ''   -- disalin dari turn pertama episode
skills.app    String(40) NOT NULL DEFAULT ''   -- korpus tempat prosedur itu berada
Index("ix_skills_app_scope", skills.c.app, skills.c.scope)
```

`''` berarti bersama (tanpa app). Nama app adalah data: `^[a-z0-9][a-z0-9-]{0,63}$`, divalidasi di tempat skill ditulis. Tidak ada default engine dan tidak ada nama app di kode.

`turns.app` ditulis sekali saat intake dari `Inbound.hints["app"]` (`stores/history.py:273-283` memperoleh `app=""`; `agent.py` mengirim hint). Body yang menghilangkan hint menulis `''`; baris lama di-backfill `''` di migrasi.

`episodes.app` disalin dari turn pertama episode (`memory/episodes.py:90-122`). `_signature()` memuat `app=<nilai>` (`:158-177`), sehingga dua run dengan tool dan target yang sama di app berbeda tidak berbagi prosedur. Run bersama dan run app juga berbeda.

`skills.app` ditulis oleh `save`, `revise`, dan command import. Jalur refleksi menyalin `episode.app`; promote dan merge mempertahankan nilai yang ada.

### 4.2 Visibilitas

Urutan baca: app dulu, audiens kedua:

```python
if skill.app and skill.app != current_app:
    return False
```

Aturan lama (`global`, `thread` lewat `stated_by`, `person` lewat `stated_by`) lalu berlaku tanpa perubahan.

`current_app` adalah hint turn, bukan nama body. App yang hilang atau tidak dikenal menyembunyikan setiap skill ber-scope app dan mempertahankan yang bersama; itulah arah gagal tertutup.

`retrieve()` memperoleh `app: str = ""` dan `_visible()` memperoleh argumen yang sama; agent mengirim `inbound.hints.get("app", "")`. Anggaran, prioritas, decay penggunaan, dan format injeksi tidak berubah.

### 4.3 Jalur tulis

- `SkillStore.save(..., app="")` dan `revise(..., app=None)`; `revise` mempertahankan nilai saat ini ketika argumennya `None`.
- `/skill import <path>` menerima `app=<name>`; tanpa itu skill bersifat bersama. `/skill save` menerima token yang sama.
- Plugin mengimpor lewat `ctx.skills` dan mengirim `ctx.app`.
- `ToolContext` memperoleh `app` (dari hint yang sama), sehingga tool plugin memfilter dengan fakta eksplisit alih-alih membaca `ctx.gateway.name`.

### 4.4 Kontrak body

`Inbound.hints["app"]` adalah jawaban body atas "aplikasi mana yang dituju pesan ini". Key itu bergabung dengan daftar hint terdokumentasi di `inbound.py`; sifatnya opsional. Body boleh menurunkannya dari konfigurasinya sendiri (route, mapping grup, tenant id) dan tidak pernah dari tabel core.

Gateway atau plugin boleh menyimpan pack dan index sendiri. Ketika ia ingin injeksi otomatis engine, ia menulis ke tabel skill core dengan `app` yang sama seperti yang ia laporkan di hint.

### 4.5 Yang tidak berubah

- `scope` tetap tiga nilai; tidak ada audiens keempat.
- Skill tetap memori prosedural dengan revisi, trust, prioritas, penggunaan, dan decay.
- Prompt part plugin tetap statis (ADR-0034); ADR ini tidak menambah permukaan prompt dinamis.
- Jalur plugin untuk pack besar tetap valid; ia pelengkap, bukan pengganti.

## 5. Konfigurasi

Tidak ada key engine. Satu-satunya permukaan data baru adalah hint body `app` dan token import/save `app=<name>`. Gateway boleh mengekspos setting-nya sendiri (misalnya `gateways.<name>.app`) dan menerjemahkannya menjadi hint; engine tidak pernah membaca konfigurasi gateway untuk ini.

## 6. Metrik dan gate

Hitungan yang harus didorong ke nol, diukur 2026-11-04:

| Celah | Jumlah | Di mana |
| --- | --- | --- |
| nilai audiens yang tidak bisa membawa app | 1 enum, 3 nilai | `schema.py:485` |
| record tanpa provenance app | 3 tabel | `turns`, `episodes`, `skills` |
| call site retrieval tanpa app | 1 | `agent.py:1309` |
| key hint terdokumentasi tanpa app | 1 daftar | `inbound.py:13-20` |

Gate-nya:

1. Skill yang ditulis dengan `app='a'` tidak ada di prompt turn yang hint-nya `b` atau hilang, dan ada untuk setiap thread `a` ketika scope-nya mengizinkan.
2. Skill bersama (`app=''`) ada di setiap app.
3. `app='a'` digabung `scope='person'` hanya terlihat oleh orang itu di dalam `a`; digabung `scope='thread'` hanya di thread itu di dalam `a`.
4. Dua episode dengan tool dan target yang sama tetapi `turns.app` berbeda menghasilkan signature berbeda dan tidak melebur.
5. Kandidat refleksi dari episode `a` disimpan dengan `app='a'`; episode bersama menyimpan `''`.
6. Turn tanpa hint tidak pernah gagal dan tidak pernah melihat skill ber-scope app.
7. Gate netral tidak menemukan literal nama app di `monoagens/engine/**`; hanya konstanta pola yang ada.
8. `tests/test_migrations.py` mencakup `0017`; `pytest -q` dan suite kontrak prompt tetap hijau.

## 7. Kriteria penerimaan

- `skills.app`, `episodes.app`, dan `turns.app` ada dengan default dan index yang dinyatakan; migrasi mem-backfill `''`.
- Matriks visibilitas di §6 lolos sebagai tes table-driven di `tests/test_skills.py` dan `tests/test_prompt_contract.py` (satu kasus per app).
- `retrieve(..., app=...)` adalah satu-satunya jalur baca; tidak ada pemanggil di engine yang menghilangkannya.
- `/skill import app=a` dan `/skill save app=a` bekerja; tanpa token skill bersifat bersama.
- Tool plugin menerima `ctx.app` dan bisa memfilter pack-nya sendiri dengan itu.
- Menghapus nama app dari data tidak butuh perubahan kode; menambah satu tidak butuh migrasi.
- Anggaran prompt, prioritas, dan decay berperilaku sama seperti sebelumnya untuk skill bersama.

## 8. Risiko dan mitigasi

- **Body lupa hint dan skill app menghilang.** Mitigasi: gagal tertutup itu disengaja; `/skill list` dan hasil import menyebut app-nya, dan hint yang hilang terlihat di baris turn (`turns.app=''`).
- **Nama app menyebar atau bertabrakan karena huruf besar.** Mitigasi: satu pola, di-lowercase sebelum tulis, tanpa alias; nama app bukan role dan tidak memberi apa pun.
- **Kebocoran lintas app lewat prosedur hasil belajar.** Mitigasi: app adalah bagian signature episode dan disalin pada setiap penulisan; promote dan merge mempertahankannya.
- **Migrasi tiga tabel saat beban berjalan.** Mitigasi: kolom nullable dengan server default, satu index dibangun di revisi yang sama, tanpa rewrite; baris lama membaca `''`.
- **Dua model korpus yang sama (skill core dan pack plugin) menyimpang.** Mitigasi: aturannya eksplisit — plugin menulis ke core dengan `app`, atau ia melayani pack sendiri dan tidak mengharapkan injeksi otomatis. Tidak pernah keduanya untuk satu korpus.
- **Hint menjadi privilege.** Mitigasi: bukan; role tetap menentukan tool dan izin. Hint app hanya memilih korpus.

## 9. Interaksi dengan ADR lain

- ADR-0020: skill mempertahankan maknanya; app adalah provenance, scope tetap audiens. Perubahan perilaku satu-satunya adalah signature.
- ADR-0018: `app` bergabung ke peta hint terbuka, dipasok body, tidak pernah ditebak engine.
- ADR-0006: app adalah routing, bukan level privasi; plafon audiens tetap menentukan entri mana yang boleh diungkap.
- ADR-0034: prompt part tetap statis; ADR ini tidak menambah permukaan prompt dinamis.
- ADR-0027/0028: plugin boleh menyimpan pack sendiri; ketika ingin injeksi engine ia menulis lewat `ctx.skills` dengan app yang ia laporkan.
- ADR-0016: satu migrasi, satu index, tanpa shim kompatibilitas.
- ADR-0031: tiga kolom nullable dan satu index; tidak ada modul baru.

## 10. Referensi kode

- `monoagens/engine/schema.py:467-489` — `skills`, CHECK scope, dan index scope; `:827-845` episodes; `:854-878` turns.
- `monoagens/engine/stores/skills.py:164-170,188-260` — `_visible`, `_mentions`, `_vector`, `retrieve`, `_inject`.
- `monoagens/engine/stores/skills.py:303-330` — `view`/`read`; jalur operator yang juga harus menyebut app.
- `monoagens/engine/agent.py:1306-1316` — satu-satunya call retrieval yang akan mengirim `hints["app"]`.
- `monoagens/engine/stores/history.py:273-283` — transaksi intake yang mencatat `turns.app`.
- `monoagens/engine/memory/episodes.py:90-122,158-177` — awal episode dan signature.
- `monoagens/engine/jobs/cognition.py:948-980,1069-1078` — promote/merge dan tulisan distill yang harus menyalin app.
- `monoagens/engine/inbound.py:13-20` — key hint yang terdokumentasi.
- `monoagens/engine/tools/__init__.py:28-100` — `ToolContext`, yang memperoleh `app`.
- `tests/test_skills.py`, `tests/test_prompt_contract.py`, `tests/test_migrations.py` — tiga suite yang diperluas keputusan ini.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/id/0035-app-scoped-skills.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/engine/0035-app-scoped-skills.md`.*
