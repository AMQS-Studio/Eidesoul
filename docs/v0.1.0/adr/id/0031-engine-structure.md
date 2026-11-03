> Terjemahan turunan — jangan sunting; sumber: 0031-engine-structure.md (sha256: c4656f41e920)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0031: Struktur Engine — Tata Letak, Anggaran Pertumbuhan, dan Baseline Toolchain

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0009 (rumah ADR dan gate-nya), ADR-0016 (bentuk skema dan migrasi), ADR-0027 (distribusi dan kapabilitas clean by default), ADR-0028 (unit plugin)
- **Supersedes:** —
- **Terkait:** ADR-0000 (bentuk permukaan), ADR-0001 (satu port memori), ADR-0032 (pintu pengiriman mendarat setelah pemecahan ini)
- **Implementasi:** draft — belum ada berkas engine yang pindah dan belum ada kunci toolchain yang berubah. Inventaris terukur dan rencana bertahap di §4 adalah pekerjaannya; baris bukti diganti saat folder pertama mendarat.
- **Catatan lokasi:** lintas-potong (mengatur setiap permukaan engine), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0031-engine-structure.md`.

---

## 1. Konteks

Engine memuat 100 modul (tanpa `__init__.py`) dan 32.835 baris. Diukur pada 2026-11-03:

- 41 berkas duduk datar di atas `eidesoul/engine/`, terurut alfabet, sehingga domain yang tak berhubungan bercampur: `ablate.py` di atas `agent.py`, `approvals.py` di antara `ambient.py` dan `assembly.py`.
- Tiga modul menanggung 22% pohon: `memory/store.py` (2.785), `agent.py` (2.215), `automation.py` (2.106). Empat modul lain di atas 800 baris (`config.py` 1.776, `stores/affect.py` 1.285, `cognition.py` 1.164, `schema.py` 954).
- Kodenya tidak gemuk: 84% baris adalah kode, 11% kosong, 5% docstring atau komentar; pemindaian klon 8-baris menemukan 21 jendela berulang (sekitar 170 baris); satu-satunya modul yang tidak berada di jalur runtime adalah `ablate.py`, alat riset yang digerakkan `scripts/ablate.py`.
- 60 dari 100 modul hidup di lima folder dengan kontrak tetap: 12 tool dan 22 command adalah satu berkas per unit (discovery folder, bukan edit registry), 9 store menyimpan persistensi, 3 modul guard dan 14 modul memori masing-masing punya satu alasan untuk berubah.
- Toolchain tertinggal satu langkah dari bahasanya: `requires-python` menyebut 3.11 padahal mesin menjalankan 3.15, 138 dari 148 modul membawa `from __future__ import annotations`, tidak ada type checker yang dikonfigurasi, dan perkakas pengembangan duduk di extra opsional alih-alih dependency group.

Jadi masalahnya bukan jumlah berkas. Masalahnya navigasi mencampur domain, dan tiga berkas memusatkan setiap perubahan. Tekanan kedua masih baru: alat coding agentic membaca pohon repo sebagai konteks, dan repo ini sudah membawa `AGENTS.md`; pohon alfabet datar membuat konteks itu lebih buruk, bukan lebih baik.

## 2. Decision drivers

1. **Satu alasan untuk berubah per modul** (information hiding, Parnas 1972). Modul berhak punya berkas sendiri hanya bila gaya yang berbeda bisa mengubahnya.
2. **Bukti ukuran modul bersisi dua.** Kepadatan cacat punya minimum pada ukuran menengah; modul sangat kecil membayar biaya antarmuka, modul sangat besar membayar biaya pemahaman (Malaiya dan Denton 2000; Koru dkk. 2009). Jawabannya membatasi puncak, bukan memecah dasar.
3. **Friksi pengiriman mengikuti kopling, bukan ukuran berkas.** Dua modul yang selalu berubah bersama adalah satu modul yang menyamar (change coupling CodeScene).
4. **Tanpa lapisan abstraksi baru.** Engine bukan server dan bukan framework; lapisan yang tak diminta adalah liabilitas.
5. **Setiap aturan harus bisa diperiksa tanpa model.** Gate-nya `pytest -q`, `scripts/adr.py --check`, `scripts/adr.py --translate-check`, dan `scripts/selfcheck.py --check`.
6. **Pohon adalah konteks agen.** `AGENTS.md` adalah berkas konteks yang interoperabel antar alat (Galster dkk. 2026); tata letak yang dijelaskannya harus tetap terbaca dan stabil.

## 3. Opsi yang dipertimbangkan

**A. Status quo: tidak memperbaiki apa pun, memecah hanya saat berkas menyakitkan.** Nol churn hari ini, tapi daftar datar 41 baris tetap, dan setiap fitur baru menambah satu berkas top-level.

**B. Menggabung agresif ke berkas yang lebih sedikit dan lebih besar.** Menekan jumlah berkas, tapi menaikkan setiap modul stateful melewati ukuran tempat biaya pemahaman mendominasi, dan bertentangan dengan driver 2.

**C. Memecah tiga modul terpusat, mengelompokkan sisanya ke folder domain, dan membatasi pertumbuhan (dipilih).** Pemindahan mekanis, tanpa perubahan perilaku, setiap langkah hijau secara mandiri.

**D. Menulis ulang menjadi lapisan (service, repository, use case).** Menambah lapisan yang tidak dibutuhkan engine; seam yang penting sudah ada (`Handler`, `Percept`/`Action`, protokol gateway).

**E. Mengganti nama dengan prefiks domain (`turn_agent.py`).** Mengubah setiap nama dan tetap mempertahankan daftar datar; churn tanpa struktur.

## 4. Keputusan

### 4.1 Aturan modul

Satu modul punya satu alasan untuk berubah. Modul stateful tinggal pada atau di bawah **800 baris**; modul di atas itu butuh satu baris di ADR yang memperkenalkannya. Folder discovery mempertahankan kontraknya: satu berkas per tool di `tools/`, satu berkas per command di `commands/`, persistensi di `stores/`, guard di `guard/`, memori di `memory/`. Aturan itu tidak dilonggarkan ADR ini; justru itulah sebab core tetap kecil.

### 4.2 Memecah di tempat lebih dulu (bucket 1)

Belum ada folder baru, karena pemecahan dan pemindahan dalam satu commit menyembunyikan kedua diff.

- Pengiriman (send, notice, typing, media) pindah dari `agent.py` ke `reply.py`, yang sudah memiliki pembentukan teks. Target: `agent.py` sekitar 1.750 baris.
- Counter kapabilitas pindah dari `agent.py` ke `runtime.py`, yang sudah memegang fakta runtime. Target: `runtime.py` sekitar 180 baris.
- SQL murni untuk memori kanonik pindah dari `memory/store.py` ke `memory/queries.py` baru (fungsi yang menerima koneksi). `MemoryStore` tetap satu-satunya port (ADR-0001); hanya statement yang pindah. Target: `store.py` di bawah 2.400 baris.
- `agent.py` dan `automation.py` mempertahankan satu jalur pengiriman, bukan dua (salinan kedua adalah logika chunk, outbox, dan atribusi yang sama). Lihat ADR-0032 untuk pintunya.
- Tiga salinan routing menjadi satu resolver (juga ADR-0032).

`memory/queries.py` adalah satu-satunya modul engine baru di bucket ini. Ia diizinkan karena memindahkan statement dari port tanpa memindahkan port.

### 4.3 Lalu mengelompokkan per domain (bucket 2)

25 modul pindah; `ablate.py` keluar dari engine ke `scripts/`. Top level turun dari 41 berkas menjadi 15.

```text
eidesoul/engine/
  __init__.py
  agent.py                      # the handle: one turn, one orchestration
  inbound.py  plugins.py  registry.py  llm.py
  config.py  db.py  schema.py
  embed.py  i18n.py  io.py  runtime.py  soul.py  self_knowledge.py

  turn/                         # one message becomes one reply
    context.py  decision.py  assembly.py  salience.py
    prompts.py  reply.py  observability.py
  character/                    # persona data resolution
    character.py  knowledge.py  lexicon.py  patterns.py
  access/                       # who may do what
    roles.py  permissions.py  identity.py
    privacy.py  approvals.py  relay.py  target.py
  jobs/                         # background work and lifecycle
    cognition.py  automation.py  supervisor.py  lease.py
    ambient.py  currents.py  mcp.py

  memory/  stores/  tools/  commands/  guard/     # unchanged layout
```

`__init__.py` menjadi peta tertulis pohon ini; ia berhenti mengimpor subpaket secara eager, karena pemanggil sudah mengimpor modul yang dibutuhkannya.

### 4.4 Mekanika perubahan

Setiap folder adalah satu commit, dan setiap commit adalah pemindahan murni: `git mv`, lalu penulisan ulang import, lalu rujukan path ADR, lalu gate. Tanpa reformat, tanpa perubahan perilaku, tanpa penggantian nama simbol. Suite 831 tes adalah jaring pengaman; gate merah mengembalikan commit, bukan tesnya.

Urutan: pecah (4.2), lalu folder (4.3), lalu pekerjaan core di ADR-0032. Pemecahan mengubah isi berkas; folder mengubah path. Dengan urutan ini rujukan path ADR ditulis ulang sekali, setelah modul baru ada.

### 4.5 Anggaran pertumbuhan

Jumlah modul engine adalah anggaran, bukan kebetulan. Modul engine baru diizinkan bila ia menggantikan satu modul, memecah modul yang di atas 800 baris, atau ADR yang memperkenalkannya menyebut alasannya. Fitur memperluas modul yang memiliki domainnya. Inilah aturan yang menjaga delapan ADR berikutnya dari menambah delapan berkas top-level lagi.

### 4.6 Mirror digenerate, bukan disalin

`admin/templates.py` menulis konfigurasi default; hari ini ia mengulang default dataclass dengan tangan. Ia menurunkannya dari pohon dataclass, pohon yang sama yang sudah dijelajahi `self_knowledge.py`. Mirror yang tidak bisa basi lebih baik daripada mirror yang diperiksa.

`scripts/adr.py` mendapat `--stamp`: setelah ADR Inggris diedit, alat itu menulis baris `sha256` salinan Indonesianya. Salinannya tetap ditulis tangan (ADR-0009); hanya aritmetikanya yang diotomatiskan.

### 4.7 Baseline toolchain

Floor bahasa dan checker adalah bagian dari struktur, karena keduanya menentukan apa yang boleh ditulis di mana.

- **Floor Python 3.14.** `requires-python = ">=3.14"`, ruff `target-version = "py314"`, dan image serta `install.sh` menyusul. Di 3.14 anotasi dievaluasi secara lazy (PEP 649/749), jadi `from __future__ import annotations` dihapus dari setiap modul dan sebuah tes struktur melarangnya kembali; keluarga `FA` ruff hanya *menambahkan* import itu, jadi ia tidak bisa menegakkan larangan ini sendiri.
- **Dependency group (PEP 735).** Perkakas pengembangan pindah ke `[dependency-groups] dev`; `[project.optional-dependencies]` hanya menyimpan opsi runtime (`whatsapp`). Lockfile diregenerasi di commit yang sama.
- **Type checker mulai sebagai ratchet, bukan tembok.** `pyright` berjalan dengan `basic`, dan baseline yang di-commit mencatat jumlah error per direktori; gate gagal bila sebuah direktori bertambah. `strict` tidak dinyalakan sekaligus, karena `agent.py` dan `automation.py` akan menjadi tembok pertama.
- **Ruff mendapat `RUF`, `ASYNC`, `PT`, dan `FA`.** `C90` dan `PLR0912` tetap: keduanya penjaga mekanis untuk aturan ukuran di §4.1, bukan preferensi gaya.
- **`TaskGroup` hanya di tempat pembatalan sibling memang diinginkan.** Situs `asyncio.gather(..., return_exceptions=True)` mempertahankan semantiknya; pembatalan terstruktur mengubah perilaku kegagalan dan bukan peningkatan gaya.
- **`slots=True` pada dataclass panas** (`Percept`, `Action`, `Handler`), dan tidak di tempat lain secara default.

Tidak ada yang mengubah perilaku; yang berubah adalah apa yang bisa dilihat gate.

## 5. Konfigurasi

Tanpa kunci konfigurasi runtime. Toolchain hanya hidup di `pyproject.toml`: `requires-python`, `[tool.ruff]` (`target-version`, pilihan aturan), `[dependency-groups] dev`, `[tool.pyright]`, dan berkas baseline-nya. Struktur adalah kode, bukan knob; nilai yang dilaporkan adalah pengukuran di §6, dicetak `scripts/selfcheck.py`.

## 6. Metrik dan gate

| Metrik | Hari ini | Target setelah ADR ini |
|---|---|---|
| Modul engine (tanpa `__init__`) | 100 | 101 (`memory/queries.py`) |
| Berkas top-level engine | 41 | 15 |
| Modul di atas 2.000 baris | 3 | 1 (`memory/store.py`, port) |
| Modul di atas 800 baris | 7 | 7: tiga yang di atas 2.000 mengecil dan tidak ada yang baru |
| Baris import ditulis ulang sekali | — | sekitar 226 (turn 85, character 34, access 69, jobs 37) |
| Rujukan path ADR diperbarui sekali | — | sekitar 126 baris |
| Jendela 8-baris berulang | 21 | bukan gate; hanya dilaporkan |
| Floor Python | `>=3.11`; image dan install script memaku 3.12 | `>=3.14` di semua tempat |
| `from __future__ import annotations` | 138 dari 148 modul | 0, ditegakkan tes struktur |
| Dependensi pengembangan | `dev` di `[project.optional-dependencies]` | `[dependency-groups] dev` (PEP 735) |
| Error type-check | tidak diukur | baseline per direktori yang di-commit dan tidak pernah naik |

Gate, setelah setiap commit dalam urutan: `pytest -q` pada store Postgres+pgvector sekali pakai, `ruff check`, `python scripts/types.py --check`, `python scripts/adr.py --check`, `python scripts/adr.py --translate-check`, `python scripts/selfcheck.py --check`.

## 7. Acceptance criteria

- `agent.py`, `automation.py`, dan `memory/store.py` di bawah targetnya, dan tidak ada modul melewati 800 baris kecuali yang tercantum di §6.
- Top level `eidesoul/engine/` hanya memuat modul yang ditunjukkan §4.3.
- `grep -rn "components\.\|ComponentSettings"` tetap kosong; tidak ada simbol yang diganti nama oleh pemindahan.
- Setiap dari lima commit hijau pada keempat gate, dan 831 tes lulus sebelum dan sesudah.
- `scripts/selfcheck.py --check` mencetak jumlah modul engine dan modul mana pun di atas 800 baris.
- `AGENTS.md` membawa pohon target supaya aturan dan kode sepakat.
- Suite berjalan di 3.14 atau lebih baru, tidak ada modul yang mengimpor `__future__.annotations`, dan tes struktur gagal bila ada yang menambahkannya.
- `uv sync --locked` memasang dev group dari `[dependency-groups]`, dan tidak ada extra `dev`.
- `scripts/types.py --check` gagal hanya bila sebuah direktori melewati baseline yang tercatat; menurunkan baseline adalah commit tersendiri.

## 8. Risiko dan mitigasi

- **Churn import memecahkan skrip dan tes.** Pemindahan murni, satu folder per commit, dan suite sebagai gate.
- **Path ADR menjadi basi.** Rujukan path ditulis ulang di commit yang sama, dan `--translate-check` gagal begitu salinan menyimpang.
- **`git blame` jadi kurang berguna.** `git mv` tanpa reformat menjaga deteksi rename.
- **Pemecahan berlebihan setelah cap.** Bukti memotong dua arah; cap 800 baris adalah plafon, bukan target, dan dasar dibiarkan.
- **Pohon yang bergerak membingungkan agen.** `AGENTS.md` dan `eidesoul/engine/__init__.py` membawa peta, dan folder stabil setelah satu kali mendarat.
- **Kenaikan floor meninggalkan image atau venv lama.** Dockerfile, `config.yaml`, `install.sh`, dan kedua README berubah di commit yang sama; interpreter 3.13 kini gagal cepat dengan satu pesan.
- **Baseline ratchet menyembunyikan utang baru di dalam hitungan lama.** Skrip mencatat per direktori, bukan satu angka global, dan penurunan adalah commit tersendiri.
- **Set aturan baru membanjiri gate.** Aturan mendarat dengan `--fix` lebih dulu, temuan sisanya ditriase sekali, dan aturan yang tidak bisa dipenuhi diabaikan secara eksplisit dengan alasan.

## 9. Interaksi dengan ADR lain

ADR-0009 mengatur di mana keputusan ini hidup dan menggerbangi salinannya. ADR-0016 tidak tersentuh: tanpa perubahan skema, tanpa migrasi. ADR-0027 dan ADR-0028 mempertahankan batasnya; folder plugin dan gateway tidak dipindah. ADR-0001 mempertahankan satu port tulis, itulah sebab `memory/store.py` tetap satu objek dan hanya statement-nya yang pindah ke `memory/queries.py`. ADR-0032 bergantung pada pemecahan ini: pintu pengiriman menggantikan kode yang lebih dulu harus hidup hanya di `reply.py`.

## 10. Referensi

- D. L. Parnas, *On the Criteria To Be Used in Decomposing Systems into Modules*, CACM 15(12), 1972. doi:10.1145/361598.361623 — satu alasan untuk berubah per modul.
- Y. K. Malaiya dan J. Denton, *Module Size Distribution and Defect Density*, ISSRE 2000. <https://www.cs.colostate.edu/~cs530dl/s25/8b3modsize.pdf> — kepadatan cacat punya minimum pada ukuran modul menengah.
- A. G. Koru dkk., *An Investigation into the Functional Form of the Size-Defect Relationship for Software Modules*, IEEE TSE 35(2), 2009 — modul kecil secara proporsional lebih rentan cacat; jangan dipecah kecil-kecil.
- CodeScene, *How to assess coupling health* (change coupling). <https://helpcenter.codescene.com/en/help/articles/1844075-how-to-assess-coupling-health-in-codescene> — friksi pengiriman datang dari bagaimana kode berubah bersama.
- B. Ahmeti, M. Linder, R. Groner, R. Wohlrab, *Architecture Decision Records in Practice: An Action Research Study*, ECSA 2024. <https://rebekkaa.github.io/files/2024_ECSA.pdf> — keputusan yang disimpan di repo bersama kodenya adalah yang tetap berguna.
- M. Galster dkk., *Harness Engineering for Agentic AI Coding Tools: An Exploratory Study*, arXiv:2602.14690, 2026. <https://arxiv.org/abs/2602.14690> — `AGENTS.md` adalah berkas konteks yang interoperabel; tata letak repo adalah bagian dari konteks itu.
- M. Borg dkk., *Echoes of AI: Investigating the downstream effects of AI assistants on software maintainability*, EMSE 31:161, 2026. doi:10.1007/s10664-026-10889-1 — tidak ada penalti maintainability sistematis yang terukur, sementara pembengkakan kode dari generasi tetap menjadi risiko yang disebut; anggaran di §4.5 adalah penyeimbangnya.
- Python 3.14, *What's New* (PEP 649/749, evaluasi anotasi secara lazy). <https://docs.python.org/3/whatsnew/3.14.html>
- PEP 735, *Dependency Groups in pyproject.toml*. <https://peps.python.org/pep-0735/>
- Ruff, *Rules* — keluarga `FA` menambahkan future import, bukan melarangnya. <https://docs.astral.sh/ruff/rules/>
- Pyright, *Configuration* (`basic` dan `strict`, `include` per direktori). <https://microsoft.github.io/pyright/#/configuration>

## 11. Referensi kode

- `eidesoul/engine/agent.py` — handle; pengiriman dan counter keluar darinya di §4.2.
- `eidesoul/engine/reply.py` — menerima helper pengiriman.
- `eidesoul/engine/runtime.py` — menerima counter kapabilitas.
- `eidesoul/engine/memory/store.py` — port tunggal; statement pindah ke `memory/queries.py`.
- `eidesoul/engine/automation.py` — salinan pengiriman kedua dihapus di §4.2.
- `eidesoul/admin/templates.py` — default diturunkan dari pohon dataclass di §4.6.
- `scripts/adr.py` — `--stamp` di §4.6.
- `scripts/selfcheck.py` — melaporkan jumlah modul dan modul yang kelebihan ukuran di §6.
- `pyproject.toml` — floor, pilihan ruff, dependency group, dan konfigurasi pyright di §4.7.
- `scripts/types.py` — berkas baru (proposal, belum dibuat): ratchet type-check per direktori di §4.7.
- `tests/test_architecture.py` — melarang `__future__.annotations` di §4.7.

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0031-engine-structure.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/0031-engine-structure.md`.*
