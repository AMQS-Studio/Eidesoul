> Terjemahan turunan — jangan sunting; sumber: engine/0023-relay-consent.md (sha256: 29f9cf5a468c)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0023: Relay Consent — Meneruskan Jawaban Pribadi ke Pihak Lain

- **Status:** accepted
- **Implementasi:** implemented — bukti: `relay_grants` (revisi 6), `RelayStore.ask`/`decide`/`revoke`/`check` dengan expiry + penolakan `none` + audit tanpa konten, `/approve` mencatat mode, `send_to` memeriksa grant, langkah `relay` ADR-0014 meminta dengan deadline run, `tests/test_relay.py`, `tests/test_automation.py`
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** manajer (A) meminta soul mengingatkan B, C, D lalu menanyakan kendala/ETA dan menawarkan bantuan; jawaban B di DM-nya adalah ruang pribadi B, sehingga meneruskannya ke A bukan urusan `cross_read` — itu **relay** yang butuh izin pemilik jawaban
- **Supersedes:** —
- **Terkait:** ADR-0006 (privasi grup & DM), ADR-0007 (`cross_send`, kelas approval `consent`), ADR-0008 (informasi tidak boleh bertambah), ADR-0014 (task & otomasi), ADR-0016 (DDL), ADR-0019 (metrik)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0023-relay-consent.md`.

---

## 1. Konteks

Skenario nyata: A (manajer) berkata *"ingatkan B, C, D soal job-nya, tanya kendala dan kira-kira kapan selesai, tawarkan bantuan."* Soul menghubungi ketiganya (`cross_send`, atribusi wajib), lalu ingin mengembalikan jawabannya ke A.

Tiga ruang berbeda terlibat:

| Ruang | Isi | Aturan lama |
|---|---|---|
| Room bersama (grup A+B+C) | jawaban B sudah terlihat A | tidak butuh izin |
| DM B dengan soul | ruang pribadi B | **tidak boleh** dibaca A; `cross_read` tidak ada |
| Task | manifest + riwayat run + sumber yang dideklarasikan | boleh dibaca task itu sendiri |

Yang belum punya rumah: **meneruskan isi jawaban B ke A** dengan izin B. Itu bukan `cross_read` (tidak ada pembacaan room), bukan pula `cross_send` biasa (isinya bukan pesan A). Itu **relay**.

## 2. Decision drivers

- Izin melekat pada **pemilik isi**, bukan pada yang meminta laporan.
- Izin harus **spesifik** (tugas, penerima, mode, masa berlaku), bukan "boleh teruskan semuanya".
- Bisa **dicabut** kapan pun, dan pencabutan segera menghentikan laporan berikutnya.
- Tidak ada jalur paksa: A, manajer, maupun administrator tidak bisa memberi izin atas nama B.
- Laporan tidak boleh menambah fakta (ADR-0008) dan tidak boleh membuka topik lain.
- Semua tindakan dapat diaudit tanpa menyimpan isi pribadi di log.

## 3. Opsi yang dipertimbangkan

| Opsi | Isi | Biaya | Risiko | Verdict |
|---|---|---|---|---|
| **A. Relay bebas** | soul meneruskan jawaban DM ke A | nol | pelanggaran privasi; B tidak tahu siapa membaca jawabannya | Ditolak |
| **B. Status saja** | A hanya tahu "sudah dihubungi / belum balas" | nol | aman, tetapi tugas manajer tidak selesai | Sebagai fallback |
| **C. Consent per tugas + mode** | B memilih: lengkap / ringkasan / anonim / jangan | kecil (satu pertanyaan) | kelelahan bertanya bila sering | **Dipilih** |
| **D. Hanya grup** | minta semua jawaban di grup | nol | memaksa B bicara di depan orang lain | Ditolak sebagai kewajiban |

## 4. Keputusan

### 4.1 Relay grant

- Satu baris per `(task_id, from_person, to_person)`:
  `{ id, task_id, from_person, to_person, mode, scope_ref, created_at, expires_at, revoked_at, decided_via }`.
- `mode`: `full` (isi apa adanya), `summary` (ringkas, **tanpa menambah fakta**), `anonymous` (isi boleh, nama B tidak disebut ke A), `none` (tidak diteruskan).
- `scope_ref` menunjuk thread/tugas; grant tidak berlaku untuk topik lain.
- `expires_at` default = tenggat tugas atau 30 hari, mana yang lebih dulu; tidak diperpanjang tanpa bertanya lagi.

### 4.2 Cara bertanya

- Soul menanyakan relay **pada saat yang sama** dengan reminder, atau satu pesan lanjutan; pilihan ditampilkan eksplisit: `lengkap / ringkasan / tanpa nama / jangan`.
- Pertanyaan menyebut **siapa penerima** dan **apa yang akan diteruskan** ("jawabanmu soal kendala & perkiraan selesai akan kukirim ke A").
- Tanpa jawaban → default **`none`**: A hanya menerima status aksi, bukan isi.
- Satu grant per tugas; tidak ada pertanyaan ulang tiap pesan.

### 4.3 Saat meneruskan

- Hanya isi yang tercakup grant yang dikirim; `summary` tidak boleh menambah fakta, nama, atau kesimpulan baru.
- Atribusi ke penerima tetap ada kecuali `anonymous`; mode anonim menyembunyikan **nama** dari A, bukan dari ledger.
- Relay masuk ledger: `relay_id`, `grant_id`, `from`, `to`, `mode`, waktu — **tanpa isi** pesan.
- Soul tidak meneruskan isi dari room lain yang tidak dicakup grant; relai tidak menjadi jalan pintas `cross_read`.

### 4.4 Pencabutan dan kedaluwarsa

- B berkata "jangan teruskan lagi" → `revoked_at` diisi, laporan berikutnya berhenti seketika.
- Laporan yang sudah terkirim **tidak bisa ditarik**; soul mengatakannya apa adanya saat mencabut.
- Lewat `expires_at` atau tugas selesai → grant mati; laporan lanjutan butuh grant baru.

### 4.5 Wewenang

- Hanya `from_person` yang boleh memberi/mencabut grant.
- A tidak bisa memberi grant atas nama B; administrator pun tidak.
- Jawaban di grup tempat A menjadi anggota tidak butuh grant (A sudah bisa membaca), tetapi soul tetap **tidak boleh** mengutipnya ke room lain.

### 4.6 Kelas approval

- Pertanyaan relay adalah **`consent`** (ADR-0007 §4.7): penyelesai adalah `from_person`; `scope_proof` = `{task_id, from, to, mode}`.
- Menolak tidak menurunkan hak apa pun; menolak tidak dikabarkan ke A sebagai "B bermasalah", hanya status.

## 5. Konfigurasi

```yaml
relay:
  enabled: true
  require_explicit: true        # tanpa jawaban -> none (status saja)
  default_expires_days: 30
  anonymous_min_recipients: 2   # anonim tetap bisa ditebak di lingkaran kecil; disebutkan saat memilih
  ask_cooldown_hours: 24        # hindari bertanya relay terus-menerus pada orang yang sama
```

## 6. Metrik dan gate

| Metrik | Definisi | Gate |
|---|---|---|
| `relay_grants_asked_total` | pertanyaan relay yang dikirim | dibatasi `ask_cooldown_hours` |
| `relay_grants_granted_total` / `_declined_total` | jawaban B per mode | terlihat |
| `relay_grants_revoked_total` | pencabutan | laporan setelahnya = 0 |
| `relay_forwarded_total` | relay yang benar-benar dikirim | hanya dengan grant aktif |
| `relay_blocked_total` | percobaan relay tanpa grant | > 0 pada test, 0 pada operasi normal |
| `relay_expired_total` | grant mati karena waktu/tugas | terlihat |

## 7. Acceptance criteria

- [ ] B menjawab "jangan" → A hanya menerima status aksi ("sudah dihubungi / sudah membalas"), tanpa isi (test).
- [ ] Tanpa jawaban relay → default `none`, bukan `full` (test).
- [ ] `summary` tidak menambah fakta yang tidak ada di jawaban B (test dengan fakta pembanding).
- [ ] `anonymous` menyembunyikan nama dari A tetapi `relay_grant` tetap mencatat `from_person` (test).
- [ ] A maupun administrator tidak bisa membuat grant atas nama B (test `/approve` dan tool).
- [ ] Pencabutan menghentikan laporan berikutnya; laporan lama tidak ditarik dan tidak dipalsukan sebagai "belum pernah dikirim".
- [ ] Grant kedaluwarsa (waktu/tugas selesai) → relay berikutnya ditolak, bukan diteruskan diam-diam.
- [ ] Relay tidak pernah membaca room B: tidak ada `cross_read` di jalur ini (test akses).
- [ ] Ledger relay berisi id + mode, tanpa isi pesan (test dump baris).
- [ ] Jawaban di grup yang A anggotai tidak butuh grant, tetapi tidak boleh dikutip ke room lain.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| B merasa ditekan memberi izin | pertanyaan netral, opsi `jangan` setara, tanpa konsekuensi; `ask_cooldown_hours` |
| Anonim bocor di lingkaran kecil | `anonymous_min_recipients`; peringatan saat memilih |
| Isi sudah terkirim tidak bisa ditarik | dinyatakan jujur saat mencabut; laporan berikutnya berhenti |
| Grant menjadi izin umum | scope per tugas + kedaluwarsa; tidak ada grant "semua topik" |
| A menyamar meminta atas nama B | grant hanya dari `from_person`; admin tidak bisa override |
| Ringkasan menambah fakta | aturan "informasi tidak boleh bertambah" + test pembanding |
| Kelelahan bertanya | satu grant per tugas + cooldown |

## 9. Interaksi dengan ADR lain

- **ADR-0006** — relay hanya untuk isi yang memang milik B; pertanyaan privasi grup tetap di DM subjek.
- **ADR-0007** — relay adalah kelas `consent`; `cross_send` (reminder) tetap butuh atribusi `author`+`reason`.
- **ADR-0008** — `summary` tunduk pada larangan menambah fakta; relay tidak menciptakan trust baru.
- **ADR-0014** — relay berjalan sebagai langkah `send` di dalam task; status task tetap satu sumber kebenaran.
- **ADR-0016** — DDL `relay_grants` (§4.9).
- **ADR-0019** — metrik §6 masuk counter dan `/status`.

## 10. Referensi kode

- `eidesoul/engine/approvals.py` — jalur `consent` yang dipakai pertanyaan relay (kelas dari ADR-0007 §4.7).
- `eidesoul/engine/agent.py:311` — `send_text` (jalur kirim yang mencatat ke `outbound_messages`).
- `eidesoul/gateways/whatsapp/tools/send.py` — tool `send` (reminder dan laporan relay memakai jalur yang sama).
- `eidesoul/engine/schema.py` — tabel `approvals` (pola TTL/binding) sebagai acuan bentuk `relay_grants`.
- `eidesoul/engine/turn/prompts.py:141-147` — blok role/label (relay menyebut `label` administrator/manajer bila ada).

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0023-relay-consent.md`.*
