> Terjemahan turunan — jangan sunting; sumber: engine/0016-schema-migration.md (sha256: bc1f03ce9ea6)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0016: Skema Kanonik & Migrasi — Satu DDL, Satu Baseline Alembic

- **Status:** accepted
- **Implementasi:** implemented — bukti: satu rantai Alembic (revisi 1-12: baseline, fencing lease, siklus turn, spine memori, cognition/episodes/skills, relay, approval class, `llm_calls`, search, automation, set FK/CHECK/index P0-3, `message_media`), `upgrade` primary-only di bawah advisory lock, upsert parent `threads` saat ingest, clamp `visibility` di jalur tulis, byte media hanya lewat `message_media`; gerbang: `pytest -q` 792 passed, `--check` 31/31, `--translate-check` 0 masalah
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** audit 2026-11-03 menemukan DDL `memories` di ADR-0001 basi (tanpa kolom `state`, `level not null default 2`, `confirmed default true`), constraint ADR-0004 menabrak `state='superseded'` dari ADR-0008, dan tidak ada pemilik migrasi untuk kolom yang lahir di ADR-0005/0008/0010/0012/0013/0014.
- **Supersedes:** —
- **Terkait:** ADR-0001 (canonical), ADR-0004 (state/level), ADR-0005 (retensi), ADR-0008 (provenance/trust), ADR-0010 (dispute), ADR-0011 (reaper/ledger), ADR-0012 (`generation`), ADR-0013 (`turns`/outbound), ADR-0014 (automation), ADR-0009 (rumah ADR)
- **Amandemen:** 2026-11-03 — keputusan pemilik: ADR-0019 (observability) masuk set; bentuk kolom `llm_calls` ditambahkan di §4.6 sebagai milik tunggal DDL, dengan retensi `engine.metrics.retention_days` (ADR-0005).
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0016-schema-migration.md`.

---

## 1. Konteks

- Repo punya **23 tabel** di satu `MetaData` (`eidesoul/engine/schema.py`), `SCHEMA_VERSION = 1`
  (`db.py:20`), dan `create_all` (`db.py:57-58`) yang menulis versi ke `schema_meta` (`db.py:63-65`).
  Tidak ada Alembic dan tidak ada jalur perubahan skema selain membuat tabel baru dari nol.
- **DDL `memories` di ADR-0001 §4 tidak lagi cocok dengan keputusan turunannya.** Ia tidak memuat
  `state` sama sekali, memaksa `level smallint not null default 2` dan `confirmed boolean not null
  default true` — padahal ADR-0004 menjadikan `level NULL` sebagai sentinel "tidak boleh dipakai" dan
  `confirmed` hanya bermakna saat `state='ok'`.
- **Constraint ADR-0004 menabrak keputusan ADR-0008.** `state='superseded'` lahir dari baris `ok`
  yang berlevel; CHECK `(state IN ('ok','staged')) = (level IS NOT NULL)` memaksanya ber-`level NULL`,
  sehingga level dan provenance baris yang digantikan hilang.
- **Kolom baru tersebar tanpa rumah migrasi:** retensi (ADR-0005), provenance/trust/kuota
  (ADR-0008), metadata dispute (ADR-0010), `generation` (ADR-0012), `turns`/`outbound_messages`
  (ADR-0013), `automations`/`automation_runs` (ADR-0014). ADR-0012 bahkan menunjuk "ADR-0005
  (migrasi skema)" — ADR-0005 adalah retensi, bukan migrasi.
- **P0-3 review 2026-11-01 belum punya pemilik:** FK untuk `thread_people`/`person_links`/`skill_files`/
  `google_identity`/`thread_eras`, CHECK `engine_lease.id=1` dan `visibility`/`privacy` 0-4, index
  `messages(ts)`, `approvals(thread_id,sender,status,expires_at)`, `approvals(status,expires_at)`,
  `thread_people(person_id)`, serta pemisahan blob media ke `message_media`.

## 2. Decision drivers

1. **Satu definisi per konsep** (aturan repo): satu DDL kanonik per tabel, bukan empat versi di empat ADR.
2. **Urutan yang bisa dijalankan mesin**, bukan diingat manusia saat implementasi.
3. **ADR pemilik tetap berdaulat atas semantik kolomnya**; ADR ini memegang bentuk, constraint, dan urutan.
4. **Aman untuk data yang sudah ada**: tambah kolom nullable → backfill → constraint, tidak sebaliknya.
5. **Terlihat**: versi skema dan migrasi tertunda harus bisa dibaca (`db check`), bukan tersembunyi.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Satu ADR pemilik DDL + baseline Alembic** | DDL kanonik `memories` di sini; tabel lain tetap di ADR pemiliknya; migrasi di sini | Satu tempat untuk bentuk, semantik tetap di pemiliknya | Satu dokumen tambahan | **dipilih** |
| B. Amandemen besar ADR-0001 | Semua DDL pindah ke 0001 | Tanpa dokumen baru | ADR-0001 jadi campuran keputusan, skema, dan riwayat; tiap ADR turunan menyunting berkas yang sama | ditolak |
| C. ALTER tersebar per ADR | Tiap ADR menulis `ALTER TABLE`-nya sendiri | Tidak perlu koordinasi | Urutan penerapan tak dijamin; inilah keadaan yang memunculkan masalah ini | ditolak |

| Alat migrasi | Keputusan |
|---|---|
| **Alembic** (baseline + revisi append-only) | **dipilih** — satu-satunya jalur yang menyimpan urutan dan bisa di-`check` |
| SQL manual berurutan | ditolak — urutan hidup di kepala orang |
| `create_all` + `ALTER` ad hoc | ditolak — hanya benar untuk DB kosong |

## 4. Keputusan

### 4.1 Aturan kepemilikan

| Wilayah | Pemilik DDL | Catatan |
|---|---|---|
| `memories`, `memory_events`, `memory_uses`, `memory_tombstones`, `memory_index` | **ADR ini §4.2** | kolom lahir di ADR-0001/0004/0005/0008/0010; bentuk & constraint di sini |
| `memories_readable` (view) | ADR-0004 §5.3 | bentuk view tidak diduplikasi di sini |
| `cognition_jobs` | ADR-0003 §5.4 | **nama kanonik**; `memory_outbox` di ADR-0001/0002 adalah nama lama, bukan tabel kedua |
| `turns`, `turn_steps`, `outbound_messages` | ADR-0013 §6 | `outbound_messages` ber-`scope_key` (§4.3 di ADR-0013) |
| `automations`, `automation_runs` | ADR-0014 §4.1/§4.10 | — |
| `engine_lease`, `engine_control` | ADR-0012 | CHECK/FK lintas tabel ditetapkan di sini |
| 17 tabel lama lain | `schema.py` (baseline) | disentuh hanya lewat §4.5 |

Aturan: **ADR pemilik mendefinisikan arti kolomnya; ADR ini menetapkan bentuk final, constraint,
index, dan urutan migrasinya.** Kolom baru = amandemen ADR pemilik **plus** satu revisi di §4.4.
Tabel atau kolom yang tidak tercantum di salah satu pemilik = cacat, bukan kebebasan.

### 4.2 DDL kanonik `memories`

```sql
memories(
  id               uuid primary key,
  scope            text not null,               -- global | person | chat            (ADR-0001)
  person_id        text not null default '',
  thread_id          text not null default '',
  kind             text not null default 'fact',-- fact|preference|event|note|commitment|rule (ADR-0008)
  text             text not null,
  state            text not null default 'pending',  -- lihat 4.3                      (ADR-0004)
  level            smallint null,               -- NULL = tidak boleh dipakai       (ADR-0004)
  confirmed        boolean null,                -- bermakna hanya saat state='ok'   (ADR-0004)
  owner_id         text not null default '',    -- stated_by                        (ADR-0008)
  subject_id       text not null default '',    -- about                            (ADR-0008)
  via              text not null default 'turn',-- enum 4.3                         (ADR-0008)
  refs             jsonb not null default '[]', -- rujukan pesan/event             (ADR-0008)
  trust            smallint not null default 1, -- T0..T3                          (ADR-0008)
  audience         text not null default 'private',
  source           text not null default 'turn',-- kolom lama, dipetakan ke via    (ADR-0008)
  vetoed           boolean not null default false,                                   -- (ADR-0004)
  vetoed_by        text not null default '',
  vetoed_at        double precision not null default 0,
  pinned           boolean not null default false,                                   -- (ADR-0005)
  durability       text not null default 'normal',                                   -- (ADR-0005)
  category         text not null default 'general',                                  -- (ADR-0005)
  use_count        int not null default 0,                                           -- (ADR-0005)
  distinct_use_days int not null default 0,                                          -- (ADR-0005)
  last_used_at     double precision not null default 0,                              -- (ADR-0005)
  max_lifetime_at  double precision null,                                            -- (ADR-0005)
  expires_at       double precision null,       -- NULL = tanpa batas (pin/Tier 1)   (ADR-0005)
  supersedes       uuid null,                   -- baris yang digantikan             (ADR-0008)
  superseded_by    uuid null,                   -- pengganti (timbal balik)          (ADR-0008)
  due_at           double precision null,       -- commitment: tenggat               (ADR-0008 B3)
  task_status      text null,                   -- open|done|cancelled|postponed|overdue (ADR-0008 B2)
  dispute_state    text not null default 'none',-- none|open|resolved|stale          (ADR-0010)
  disputed_with    uuid null,                                                        -- (ADR-0010)
  dispute_reason   text not null default '',                                         -- (ADR-0010)
  disputed_at      double precision not null default 0,                              -- (ADR-0010)
  asserted_at      double precision not null default 0,                              -- (ADR-0008)
  created_at       double precision not null,
  updated_at       double precision not null
)
```

Setiap kolom menyebut ADR pemilik semantiknya; ADR-0001 §4 berhenti memuat DDL dan hanya memuat daftar
tabel + perannya.

### 4.3 Constraint & index

```sql
-- state: enam nilai, satu sumber
CHECK ( state IN ('pending','ok','staged','failed','not_worth','superseded') )
-- level ada tepat pada baris berlabel; superseded lahir dari baris berlabel (ADR-0008 B)
CHECK ( (state IN ('ok','staged','superseded')) = (level IS NOT NULL) )
CHECK ( level IS NULL OR level BETWEEN 0 AND 4 )
CHECK ( state <> 'ok' OR confirmed IS NOT NULL )
CHECK ( scope IN ('global','person','thread') )
CHECK ( via IN ('turn','tool','command','reflection','ambient','automation','soul_doc') )
CHECK ( dispute_state IN ('none','open','resolved','stale') )

CREATE INDEX memories_live      ON memories (scope, person_id, state);
CREATE INDEX memories_thread    ON memories (thread_id, state);
CREATE INDEX memories_expiry    ON memories (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX memories_refs_gin  ON memories USING gin (refs);
CREATE UNIQUE INDEX memories_supersede_pair ON memories (id, supersedes) WHERE superseded_by IS NOT NULL;
```

- `state='superseded'` **mempertahankan** `level` dan `refs`: yang berubah hanya status dan
  `superseded_by`. Constraint lama yang memaksa `level NULL` dicabut di sini.
- `expires_at NULL` = tanpa batas (Tier 1/pin). ADR-0005 tetap pemilik aturan kapan `NULL` sah.
- Tabel yang belum lahir di skema lama (`memory_uses`, `memory_tombstones`, `memory_index`,
  `memory_events`) memakai bentuk yang sama seperti disebut di ADR-0001/0005, dengan FK ke `memories(id)`.

### 4.4 Baseline Alembic & urutan migrasi

`SCHEMA_VERSION` naik satu per revisi. Baseline = keadaan `schema.py` hari ini (23 tabel, versi 1).

| Versi | Isi | Pemilik keputusan |
|---|---|---|
| 1 | baseline: 23 tabel yang ada | `schema.py` |
| 2 | `memory_events`, `cognition_jobs` (+ `memories` dasar tanpa `state`) | ADR-0001, ADR-0003 |
| 3 | `memories.state/level/confirmed` + view `memories_readable` + CHECK 4.3 | ADR-0004 |
| 4 | kolom retensi: `pinned`, `durability`, `category`, `use_*`, `expires_at`, `max_lifetime_at` | ADR-0005 |
| 5 | provenance/trust: `via`, `refs`, `asserted_at`, `trust`, `owner/subject` | ADR-0008 |
| 6 | dispute: `dispute_state`, `disputed_with`, `dispute_reason`, `disputed_at` | ADR-0010 |
| 7 | fencing: `engine_lease.generation`, FK/CHECK/index P0-3, `message_media` | ADR-0012, ADR-0011 |
| 8 | `turns`, `turn_steps`, `outbound_messages(scope_key, chunk_index)` | ADR-0013 |
| 9 | `automations`, `automation_runs` | ADR-0014 |

Aturan tiap revisi:

1. **Tambah nullable dulu**, backfill berbatas (`backfill_batch`), baru constraint/`NOT NULL`.
2. Constraint pada tabel besar ditambah `NOT VALID`, lalu `VALIDATE CONSTRAINT` setelah backfill.
3. Tidak ada `DROP COLUMN`/`DROP TABLE` tanpa satu rilis jeda dan catatan di `LOG.md`.
4. `down` hanya untuk dev (`allow_down: false` default) — turun versi di produksi berarti kehilangan data.
5. Migrasi hanya dijalankan **primary** di bawah `pg_advisory_lock`; standby tidak pernah migrasi.
6. `create_all` hanya untuk DB kosong dan test; produksi memakai migrasi.

**Backfill awal (revisi 3):** baris lama tidak punya `state` dan semua baris yang tersimpan dianggap
terbaca, jadi `state='ok'` untuk baris dengan `level` 0-4 (default lama 2), `confirmed` diisi `true`
(perilaku lama), sisanya `state='pending'`. Satu kali, idempoten, dicatat di `schema_meta`.

### 4.5 FK, CHECK, dan index dari review P0-3

- FK: `thread_people.person_id`, `person_links.*`, `skill_files.skill_id`, `google_identity.*`,
  `thread_eras.thread_id` ke tabel induknya (`ON DELETE CASCADE` untuk anak milik, `SET NULL` untuk rujukan).
- CHECK: `engine_lease.id = 1`; `visibility BETWEEN 0 AND 4` (messages/memori terkait);
  `level BETWEEN 0 AND 4` (sudah di 4.3).
- Index: `messages(ts)`, `approvals(thread_id, sender, status, expires_at)`,
  `approvals(status, expires_at)`, `thread_people(person_id)`.
- Blob media dipisah ke `message_media(message_id, kind, mime, bytes_ref)`; `messages` menyimpan pointer.

### 4.6 Tabel `llm_calls` (metrik biaya, ADR-0019)

```sql
CREATE TABLE llm_calls (
  id          bigserial PRIMARY KEY,
  ts          timestamptz NOT NULL DEFAULT clock_timestamp(),
  turn_id     text,          -- null untuk pekerjaan latar (job_id/run_id terisi)
  job_id      bigint,
  run_id      text,
  purpose     text NOT NULL, -- decision | reply | polish | digest | extract | reflect | research | label
  model       text NOT NULL,
  tokens_in   integer,       -- null bila provider tidak mengembalikan usage
  tokens_out  integer,
  latency_ms  integer NOT NULL,
  ok          boolean NOT NULL
);
CREATE INDEX ix_llm_calls_ts ON llm_calls (ts);
CREATE INDEX ix_llm_calls_turn ON llm_calls (turn_id);
```

- Tanpa FK ke `turns`/`cognition_jobs`/`automation_runs`: tabel metrik tidak boleh menahan atau
  memblokir penghapusan entitas, dan satu baris tanpa id masih berguna (mis. kegagalan sebelum turn).
- Isinya id + angka saja; teks prompt/balasan tidak pernah masuk (ADR-0019 §4.1).
- Retensi: `engine.metrics.retention_days` (default 90), dihapus job purge ADR-0005.
- **Amandemen 2026-11-03:** keputusan pemilik — ADR-0020 (prosedur turunan) masuk set; `skills` diperluas dan `skill_revisions` ditambahkan di §4.7. 2026-11-03 — §4.8: kolom `approval_class` + `scope_proof` pada `approvals` (ADR-0007 §4.7). §4.9: `relay_grants` + `automation_state_log` (ADR-0023, ADR-0014 §4.13).

### 4.7 Tabel `skills` dan `skill_revisions` (prosedur turunan, ADR-0020)

```sql
ALTER TABLE skills
  ADD COLUMN scope          text NOT NULL DEFAULT 'thread',   -- thread | person | global
  ADD COLUMN via            text NOT NULL DEFAULT 'reflection',
  ADD COLUMN stated_by      text,
  ADD COLUMN source_episode text,
  ADD COLUMN uses           integer NOT NULL DEFAULT 0,
  ADD COLUMN last_used      timestamptz,
  ADD COLUMN revision       integer NOT NULL DEFAULT 1,
  ADD COLUMN trust          text NOT NULL DEFAULT 'T2';     -- T0-T3 (ADR-0008)

ALTER TABLE skills
  ADD CONSTRAINT ck_skills_scope CHECK (scope IN ('thread','person','global')),
  ADD CONSTRAINT ck_skills_trust CHECK (trust IN ('T0','T1','T2','T3'));

CREATE TABLE skill_revisions (
  skill           text NOT NULL,
  revision        integer NOT NULL,
  description     text NOT NULL,
  body            text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
  source_episode  text,
  PRIMARY KEY (skill, revision)
);

CREATE INDEX ix_skills_scope ON skills (scope);
CREATE INDEX ix_skill_revisions_skill ON skill_revisions (skill, revision DESC);
```

- `skill_files` tetap sebagai berkas pendukung; scope-nya mengikuti baris `skills` induk.
- Semantik kolom (scope default, `via=reflection`, `uses`/`last_used` untuk renewal, revisi
  append-only) milik ADR-0020 §4.5; ADR-0016 hanya memegang bentuknya.
- `global` hanya boleh lahir dari sumber T0 atau approval (ADR-0020 §4.4); CHECK tidak bisa
  menegakkan itu sendirian, jadi ia dijaga di jalur tulis + test.

### 4.8 Kelas approval pada `approvals` (ADR-0007 §4.7)

```sql
ALTER TABLE approvals
  ADD COLUMN approval_class text NOT NULL DEFAULT 'authorization',  -- consent | authorization
  ADD COLUMN scope_proof    json NOT NULL DEFAULT '{}';

ALTER TABLE approvals
  ADD CONSTRAINT ck_approvals_class CHECK (approval_class IN ('consent','authorization'));
```

- `scope_proof` adalah hasil hitung deterministik saat request: daftar **target** aksi (path, room,
  kolom) + snapshot roots role penanya + kelas yang diturunkan. Eksekusi membandingkannya dengan
  scope saat itu; perbedaan jangkauan membatalkan approval atau menaikkan kelasnya.
- Tidak ada index baru: query tetap `(thread_id, sender, status)` dan `(status, expires_at)`.

### 4.9 `relay_grants` dan `automation_state_log` (ADR-0023, ADR-0014 §4.13)

```sql
CREATE TABLE relay_grants (
  id             bigserial PRIMARY KEY,
  task_id        text NOT NULL,
  from_person    text NOT NULL,
  to_person      text NOT NULL,
  mode           text NOT NULL,              -- full | summary | anonymous | none
  scope_ref      text NOT NULL,              -- thread/tugas
  created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at     timestamptz,
  revoked_at     timestamptz,
  decided_via    text NOT NULL DEFAULT ''    -- consent | owner_note
);
CREATE UNIQUE INDEX ux_relay_grant ON relay_grants (task_id, from_person, to_person);
CREATE INDEX ix_relay_grants_expiry ON relay_grants (expires_at);

CREATE TABLE automation_state_log (
  id             bigserial PRIMARY KEY,
  automation_id  bigint NOT NULL,
  from_state     text NOT NULL,
  to_state       text NOT NULL,
  reason         text NOT NULL DEFAULT '',
  actor          text NOT NULL DEFAULT '',
  at             timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_automation_state_log ON automation_state_log (automation_id, at DESC);
```

- `mode='none'` tetap disimpan (bukan menghapus baris): A hanya menerima status aksi, dan keputusan B
  tetap bisa diaudit.
- `relay_grants` tidak menyimpan isi pesan; isi tetap di ruang masing-masing.
- `automation_state_log` hanya mencatat transisi state (ADR-0014 §4.13), bukan setiap run.

## 5. Konfigurasi

```yaml
database:
  migrations:
    mode: on_boot          # on_boot | manual
    allow_down: false      # `down` hanya di dev
    backfill_batch: 500
    advisory_lock_timeout_seconds: 30
```

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Selisih versi kode vs `schema_meta` | 0 (selain itu boot gagal) |
| Constraint `NOT VALID` yang menggantung | 0 setelah backfill |
| Baris `memories` dengan `state` NULL | 0 |
| Tabel dari dua nama (`memory_outbox` + `cognition_jobs`) | 0 |
| Index P0-3 yang hilang setelah migrasi | 0 |
| Migrasi yang berjalan di standby | 0 |

## 7. Acceptance criteria

- [ ] Hanya ada satu DDL `memories` di seluruh ADR, yaitu §4.2; ADR-0001 §4 memuat daftar tabel + peran saja.
- [ ] `state='superseded'` mempertahankan `level`; CHECK 4.3 menggantikan CHECK ADR-0004 §5.2.
- [ ] `level` dan `confirmed` nullable di skema; `level NULL` = tidak boleh dipakai.
- [ ] Alembic baseline (versi 1) + revisi 2-9 dapat dijalankan dari DB kosong maupun DB versi 1.
- [ ] Upgrade dari DB versi 1 berhasil; `db check` melaporkan versi dan migrasi tertunda.
- [ ] Migrasi hanya di primary, di bawah advisory lock; standby menolak.
- [ ] `memory_outbox` tidak pernah dibuat sebagai tabel; `cognition_jobs` satu-satunya nama.
- [ ] FK/CHECK/index P0-3 ada setelah revisi 7.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Migrasi memblokir boot | `mode: manual` untuk operator; revisi idempoten; `db check` lebih dulu |
| Backfill lama pada tabel besar | batch + index `memories(expires_at)`; jalankan saat sepi |
| Dua instance migrasi serentak | `pg_advisory_lock` + hanya primary |
| `down` merusak data | `allow_down: false` default |
| DDL tersebar kembali ke ADR lain | check migrasi: tabel/kolom tanpa pemilik di §4.1 = gagal |
| `create_all` dipakai di produksi | fail-fast bila `mode=on_boot` dan versi berbeda |
| Kolom baru ditambahkan tanpa ADR | check yang sama menolak kolom tanpa baris di tabel 4.1 |

## 9. Interaksi dengan ADR lain

- **ADR-0001** — canonical tetap di sana; DDL dipindah ke sini, daftar tabel + peran tetap di 0001 §4.
- **ADR-0003** — `cognition_jobs` nama kanonik; `memory_outbox` di 0001/0002 dibaca sebagai nama lama.
- **ADR-0004** — semantik `state`/`level`/`confirmed` dan view `memories_readable` tetap miliknya; CHECK diperbaiki di 4.3.
- **ADR-0005** — kolom retensi; aturan kapan `expires_at` NULL tetap miliknya.
- **ADR-0008** — provenance/trust dan `superseded`; constraint `level` diselaraskan di sini.
- **ADR-0010** — kolom dispute; tidak menambah nilai `state`.
- **ADR-0011** — reaper/ledger; urutan migrasi menutup `outbound_messages` di revisi 8.
- **ADR-0012** — `generation` + CHECK `engine_lease.id=1` di revisi 7.
- **ADR-0013** — `turns`/`turn_steps`/`outbound_messages`; nuansa kolom tetap di 0013 §6.
- **ADR-0014** — `automations`/`automation_runs` di revisi 9.
- **ADR-0009** — berkas ini ikut migrasi ke `docs/v0.1.0/adr/`; migrasi skema ≠ migrasi dokumen.
- **`AGENTS.md`** — diamandemen 2026-11-03: `MemoryStore` menjadi port tulis tunggal (ADR-0001), dan yang dilarang adalah jalur semantik kedua (`Never revive MemoryStore` diganti larangan tersebut).

## 10. Referensi kode

- `eidesoul/engine/db.py:20` — `SCHEMA_VERSION`.
- `eidesoul/engine/db.py:57-58` — `create_all` (hanya DB kosong/test).
- `eidesoul/engine/db.py:63-65` — penulisan versi ke `schema_meta`.
- `eidesoul/engine/schema.py` — 23 tabel; `:270-290` `engine_lease`, `engine_control`.
- `eidesoul/engine/memory/mem0.py:264-281` — metadata JSON yang hari ini menyimpan provenance di luar skema.
- `eidesoul/engine/tools/settings.py:85-98` — skema config (bukan skema DB), preseden "sumber tunggal skema".
- Tidak ada `alembic.ini`/`migrations/` di repo — ini prasyarat, bukan keadaan sekarang.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0016-schema-migration.md`.*
