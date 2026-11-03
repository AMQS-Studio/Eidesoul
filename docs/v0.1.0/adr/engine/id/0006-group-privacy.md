> Terjemahan turunan — jangan sunting; sumber: engine/0006-group-privacy.md (sha256: 919662affe75)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0006: Privasi Grup & Pre-Filter — Ceiling Sebelum Prompt, Capture Diri-Sendiri

- **Status:** accepted
- **Implementasi:** sebagian — ada: guard cross-chat (`privacy.py`, buffer `quoted_refs`); sisa: level + veto subjek + `rooms_of`
- **Tanggal:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0001 (canonical + Mem0 adapter), ADR-0002 (gate capture), ADR-0004 (labeling fallback), ADR-0005 (retention)
- **Supersedes:** —
- **Terkait:** ADR-0003 (worker), ADR-0007 (capability vs authorization), ADR-0008 (integritas), ADR-0011 (crash safety)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0006-group-privacy.md`.
- **Amandemen:** 2026-11-02 — normalisasi kerangka (ADR-0009 §4.4): Konfigurasi dipromosikan dari subbagian 4.5 ke bagian 5; bagian sesudahnya bergeser. 2026-11-03 — audit: daftar pemanggil `PrivacyPolicy` dilengkapi (jalur review auditor, `engine/privacy.py:160,192`), bukan hanya tool dan command. 2026-11-03 — audit: rentang `capture_turn`/`_assess`/`render` diselaraskan dengan `def` aktual di repo. 2026-11-03 — rename ADR-0000 §4.4: chat_id → thread_id, chats/chat_eras/chat_people → threads/thread_eras/thread_people, scope 'chat' → 'thread'.

---

## 1. Konteks

**Ceiling privasi sudah ada, tetapi tidak dijalankan di jalur prompt.** `PrivacyPolicy.show()` dan
`.disclose()` (`eidesoul/engine/memory/privacy.py:52-71`) sudah benar, tetapi hanya dipanggil di jalur
tool, command, dan review auditor:

- `eidesoul/engine/tools/memory.py:118-119,145-146,175-177` — `policy.show(row, is_dm=ctx.is_dm)`.
- `eidesoul/engine/commands/memori.py:58,76,104` — idem.
- `eidesoul/__main__.py:372-381` — selftest policy.

Jalur yang membangun **blok memori di prompt** tidak memakai policy sama sekali:

- `eidesoul/engine/memory/context.py:118-133` — `build()` hanya menyaring `vetoed`; tidak menyaring
  `level` dan tidak menerima `is_dm`.
- `eidesoul/engine/memory/mem0.py:536-557` — `context()` meneruskan ke `build()` tanpa ceiling.
- `eidesoul/engine/agent.py:619-628` — pemanggil tidak mengirim `is_dm`.

Akibatnya L3/L4 bisa masuk prompt **grup**, dan `MemoryContext.hits` (yang dipakai auditor dan jalur
disclosure) memuat baris yang seharusnya tidak pernah terlihat di grup.

**Capture grup tidak dibatasi “tentang diri sendiri”.** `capture_turn`
(`eidesoul/engine/privacy.py:225-277`) menyimpan `[teks pengguna, balasan soul]` dengan
`owner = person_id`, `subject = assess.subject atau owner`, `audience = "group"` — tanpa aturan bahwa
di grup hanya fakta yang **pembicara nyatakan tentang dirinya** yang boleh disimpan.

**Pertanyaan konfirmasi bocor di grup.** Jalur `verdict.ask` mengirim pertanyaan ke
`inbound.thread` (`eidesoul/engine/privacy.py:267-277`), dan jalur disclosure melakukan hal yang
sama (`:150-172`). Di grup, pertanyaan itu mengungkap adanya fakta yang sedang dilindungi.

**Yang sudah baik dan dipertahankan:**

- Hard exclusion kredensial sebelum disimpan (`eidesoul/engine/privacy.py:225`,
  `eidesoul/engine/memory/mem0.py:354-356`).
- Person memory ber-`audience="group"` tidak pernah dibawa ke chat lain
  (`eidesoul/engine/memory/mem0.py:425-433`).
- `vetoed` selalu menang di seluruh verdict (`memory/privacy.py:53-71`).
- Default aman: `group_max_level: 2`, `dm_max_level: 4`, `ask_level: 4`
  (`eidesoul/engine/config.py:118-127`).

## 2. Decision drivers

1. **L3/L4 tidak boleh pernah masuk prompt non-DM** — ditegakkan di satu funnel, bukan disiplin pemanggil.
2. **Capture grup hanya fakta diri pembicara** — fakta pihak ketiga butuh consent subjek (ADR-0007).
3. **Tidak ada pertanyaan bocor** — kanal grup tidak pernah dipakai untuk konfirmasi level/konten.
4. **Deterministik dan bisa diuji** — tanpa LLM pada jalur penegakan.
5. **DM tidak dikorbankan** — perilaku DM (ceiling 4) tetap.
6. **Satu sumber kebijakan** — `PrivacyPolicy` adalah satu-satunya predikat allow/ask/deny.
7. **Data minimization** — lebih baik tidak menyimpan daripada menyimpan lalu menyembunyikan.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Filter di tiap call-site** | Setiap pemanggil prompt/tool memfilter sendiri | Tidak mengubah API | Bocor begitu ada pemanggil baru; mudah lupa | Ditolak |
| **B. Filter hanya di `render()`** | Teks prompt bersih | `MemoryContext.hits` tetap membawa L4 → auditor/disclosure melihat yang terlarang | Tidak aman | Ditolak |
| **C. Ceiling di funnel retrieval** | `gather`/`build` menerima `is_dm` dan memakai `PrivacyPolicy.show`; capture diri-sendiri; larangan tanya di grup | Satu titik penegakan; invariant teruji; hits sudah bersih | Perlu plumb `is_dm` melewati `MemoryBackend.context` | **Dipilih** |
| **D. Pisah tabel/index per audience** | Tabel `memories_group` terpisah | Isolasi fisik | Kompleks, butuh migrasi, dua lifecycle; belum perlu | Ditolak |

## 4. Keputusan

> **Ceiling privasi ditegakkan saat retrieval, sebelum apa pun dirender atau diteruskan; di grup hanya fakta diri pembicara yang di-capture; dan tidak ada pertanyaan privasi di grup.**

### 4.1 Satu predikat, satu funnel

- `PrivacyPolicy.show(hit, is_dm=...)` menjadi **satu-satunya** predikat “boleh dilihat di chat ini”
  (`vetoed=false` dan `level <= ceiling`).
- `MemoryBackend.context()` **wajib** menerima `is_dm`; `mem0.context()` meneruskannya ke
  `memory_context.build(..., is_dm=...)`.
- `build()` membuang hit yang gagal `show()` **sebelum** `render()` dan **sebelum**
  `MemoryContext.hits` dibentuk. Auditor dan jalur disclosure karena itu tidak pernah melihat baris
  di atas ceiling.
- Berlaku untuk ketiga scope: `global`, `person`, `chat`.
- `ceiling = dm_max_level` bila DM, `group_max_level` bila grup.

### 4.2 Capture grup = pernyataan diri

Di non-DM:

| Konten | Perlakuan |
|---|---|
| Fakta yang pembicara nyatakan tentang dirinya (`subject` kosong atau == owner) | Disimpan: `scope=person(owner)`, `audience="group"`, `level <= group_max_level` |
| Fakta pihak ketiga (pembicara menyatakan tentang orang lain) | **Drop total** — tidak ada baris canonical; butuh consent subjek (ADR-0007) |
| Balasan soul sendiri | **Tidak disimpan** sebagai person memory (bisa memuat konteks dari DM) |
| Fakta bersama grup (nama grup, rencana, agenda) | Boleh `scope=thread(thread_id)`, `level <= group_max_level`, `source='soul'` |
| Kredensial/krisis/instruksi berbahaya | Hard exclusion, sebelum `memory_events` (ADR-0002/0004) |

`audience="group"` dan pengecualian lintas-chat di `_search_scope` dipertahankan. Drop dicatat
sebagai metrik, bukan dibisukan.

### 4.3 Tidak ada pertanyaan privasi di grup

- Di DM: perilaku sekarang dipertahankan — `verdict.ask` menyimpan `confirmed=false` lalu bertanya
  di DM.
- Di grup: `verdict.ask` **tidak** menyimpan baris `ok`. Baris dibuat `state='pending'` dengan
  `level NULL` (sentinel ADR-0004), sehingga tidak bisa dibaca dan tidak bisa diungkap. Tidak ada
  pertanyaan yang dikirim ke grup.
- Konfirmasi ditawarkan lewat DM pemilik bila kanal DM tersedia (`dm_offer: true`). Bila pemilik
  mengonfirmasi, baris bertransisi `pending → ok` dengan level hasil konfirmasi (tetap dibatasi
  ceiling grup saat dibaca di grup).
- Bila tidak pernah dikonfirmasi: `pending` melewati TTL 7 hari → purge + tombstone audit tanpa isi.
- Approval `privacy`/`disclosure` hanya boleh dikirim ke chat DM. Di grup, verdict `ask`
  diperlakukan seperti `deny` secara senyap: draft tetap dikirim tanpa bagian berisiko.

### 4.4 Scope retrieval di grup

- Grup: `global` + `person(speaker)` + `chat(this chat)`, semuanya lewat ceiling grup.
- Person memory dari chat lain dengan `audience="group"` tidak dipakai (sudah berjalan).
- Memori yang lahir dari DM tidak pernah menjadi bagian konteks grup.

## 5. Konfigurasi

```yaml
memory:
  privacy:
    default_level: 2
    ask_level: 4
    group_max_level: 2
    dm_max_level: 4
    auditor: true
    group_capture:
      self_only: true
      third_party: drop        # drop | deny
      capture_reply: false
      ask_in_group: false      # verdict.ask di grup -> pending level NULL
      dm_offer: true
    retrieval:
      ceiling: true
```

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Hit L3/L4 yang lolos ke prompt grup | 0 (diuji) |
| Capture grup yang di-drop karena `subject != owner` | Terlihat, bukan nol |
| Pertanyaan privasi/disclosure dikirim ke chat non-DM | 0 (diuji) |
| `pending` hasil `ask` di grup yang tak pernah dikonfirmasi | Terlihat + TTL bekerja |
| Memory ber-`audience=group` yang muncul di chat lain | 0 (diuji) |
| Regresi recall di DM | Tidak turun |

## 7. Acceptance criteria

- [ ] `MemoryBackend.context()` menolak dipanggil tanpa `is_dm`.
- [ ] `build()` membuang hit di atas ceiling sebelum `render()` dan sebelum `MemoryContext.hits` dibentuk.
- [ ] Test: `search` mengembalikan L4 di grup → blok prompt tidak memuatnya, dan `hits` juga tidak.
- [ ] Test: DM dengan baris yang sama tetap menampilkannya (ceiling DM = 4).
- [ ] Di grup, capture menolak fakta pihak ketiga dan balasan soul sendiri (tanpa baris canonical).
- [ ] Tidak ada approval `privacy`/`disclosure` yang dikirim ke chat non-DM.
- [ ] `verdict.ask` di grup menghasilkan baris `pending`/`level NULL`, bukan baris `ok`.
- [ ] Baris `pending` hasil `ask` grup tidak pernah muncul di retrieval/prompt/disclosure.
- [ ] Konfirmasi lewat DM mengubah `pending → ok` dengan level valid.
- [ ] Tool dan command tetap memakai `PrivacyPolicy.show` yang sama (tidak ada predikat kedua).
- [ ] Hard exclusion tetap terjadi sebelum `memory_events`.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Soul terasa “pelupa” di grup | Disengaja; fakta diri sendiri tetap masuk; fakta bersama grup masuk `scope=chat`; `dm_offer` untuk naik level |
| Ada jalur baca baru yang melewati funnel | `context()` tanpa `is_dm` = error; test gagal bila predikat kedua dibuat |
| `pending` menumpuk dari grup | TTL 7 hari + alert backlog (ADR-0004) + metrik |
| Subject resolution gagal (nama ambigu) | Default **drop**, bukan owner; log + metrik |
| Auditor LLM melihat konten di atas ceiling | `hits` sudah terfilter sebelum auditor; di grup maksimum L2 |
| Operator butuh capture pihak ketiga | Tuas `third_party: deny` tersedia sebagai keputusan sadar, terdokumentasi risikonya |

## 9. Interaksi dengan ADR lain

- **ADR-0001** — canonical tetap sumber kebenaran; `audience` dan `level` adalah kolom yang dibaca funnel.
- **ADR-0002** — diamandemen: di grup, gate capture berhenti di pernyataan diri; hard exclusion tetap sebelum `memory_events`.
- **ADR-0003** — worker tidak boleh menaikkan `level` di luar jalur `PrivacyPolicy`.
- **ADR-0004** — `verdict.ask` di grup memakai jalur `pending`/`level NULL`; hanya `state='ok'` yang bisa dibaca, dan TTL 7 hari berlaku.
- **ADR-0005** — retensi tidak berubah; ceiling adalah filter baca, bukan filter hidup.
- **ADR-0007** — fakta pihak ketiga butuh consent/veto subjek; ADR-0006 menutup jalur capture otomatisnya. Ceiling yang sama berlaku di jalur keluar: teks yang dikirim lewat `send` ke room lain tidak boleh memuat fakta pihak ketiga di atas ceiling room tujuan.
- **ADR-0008** — provenance/trust tidak menaikkan ceiling.
- **ADR-0011** — kegagalan retrieval tidak boleh jatuh ke “tanpa ceiling”; gagal = blok kosong.

## 10. Referensi kode

- `eidesoul/engine/memory/privacy.py:52-71` — `show`, `disclose`.
- `eidesoul/engine/memory/context.py:40-86,88-133` — `gather`, `render`, `build`.
- `eidesoul/engine/memory/mem0.py:340-380,425-433,536-557` — `capture`, pengecualian lintas-chat, `context()`.
- `eidesoul/engine/privacy.py:133-140,141-172,222-281,282-309` — `_risky`, `review`, `capture_turn`, `_assess`.
- `eidesoul/engine/agent.py:619-628,900-918` — pemanggil konteks dan spawn capture.
- `eidesoul/engine/tools/memory.py:104-119,145-146` — pemakaian policy di tool.
- `eidesoul/engine/commands/memori.py:58,76,104` — pemakaian policy di command.
- `eidesoul/engine/config.py:118-127` — `PrivacyConfig`.
- `eidesoul/__main__.py:372-381` — selftest privacy.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0006-group-privacy.md`.*
