> Terjemahan turunan — jangan sunting; sumber: 0040-monoagens-naming.md (sha256: 7dd4fcdb1002)

# ADR-0040: Penamaan — MonoAgens dan Persistent Agent Harness

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** mengikuti ADR-0009 (ADR home), ADR-0027 (distribusi), ADR-0031 (struktur engine), ADR-0033 (core netral), dan ADR-0039 (setup terminal); menggantikan nama produk Eidesoul.
- **Supersedes:** —
- **Terkait:** ADR-0000 (invariant awal), ADR-0027 (distribusi), ADR-0031 (struktur modul), ADR-0033 (core netral), ADR-0039 (setup terminal)
- **Implementasi:** implemented — package, CLI, environment, direktori state, docs, dan URL repository memakai MonoAgens; tidak ada alias.
- **Catatan lokasi:** penamaan bersifat cross-cutting, jadi ADR ini duduk di root ADR home. Salinan Indonesia di `docs/v0.1.0/adr/id/0040-monoagens-naming.md`.

---

## 1. Konteks

Produk dikirim sebagai **Eidesoul**. Nama itu membawa *soul*, istilah metafisik: ia menyiratkan kesadaran dan mengundang debat ensoulment. Konsep internal `soul.yaml` dan dokumen `soul` tidak terpengaruh, tetapi nama produk membuat klaim yang tidak didukung engine.

Pada saat yang sama, **harness** adalah kategori teknik yang benar — runtime yang membungkus model dan mengubahnya menjadi agent (arXiv:2606.10106; Microsoft Learn, *Agent Harness*) — tetapi ia jargon bagi pengguna dan tidak boleh membawa nama produk. Namespace `<prefix>Agent` sudah ramai: HermesAgent ada, dan `mono-agent` adalah produk yang sudah ada (PyPI `mono-agent`, `docs.mono-agent.dev`). Nama dengan bentuk itu akan terasa turunan dan bertabrakan.

Literatur sudah menamai properti dan kategorinya: *persistent identity* (Menon, arXiv:2604.09588; Natangelo, arXiv:2510.24831), *identity continuity* (Ru, arXiv:2609.23356), dan *agent harness* (arXiv:2606.10106). Keputusan ini memakai istilah-istilah itu alih-alih menciptakan sinonim.

Owner meminta nama yang legibel (sebuah agent), orisinal (bukan `<prefix>Agent`), bebas klaim metafisik, dan didukung nama tipe yang bisa dipakai di docs. Repository harus di-rename dalam satu cutover, bukan lewat alias kompatibilitas.

## 2. Decision drivers

| Driver | Konsekuensi |
| --- | --- |
| Tanpa klaim metafisik | Tidak ada *soul* dan tidak ada *self* di nama produk; *person* tetap copy produk, bukan kategori |
| Kategori harus legibel | *agent* adalah kategorinya; *harness* tetap di nama tipe runtime, bukan di nama produk |
| Nama harus orisinal | Tidak ada `<prefix>Agent`; tidak bertabrakan dengan HermesAgent atau `mono-agent` |
| Satu invariant harus terlihat | *mono* menamai satu identitas, satu writer, satu store — bukan satu tubuh, bukan satu proses |
| Persistensi harus terlihat | *persistent* menamai klaimnya: state bertahan lintas sesi, pergantian model, dan tubuh |
| Cutover bersih | Satu commit; tanpa paket alias, tanpa shim, tanpa CLI ganda |
| Versioning jujur | Pra-rilis; rename adalah breaking change dan boleh menjadi breaking |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkasan | Nilai |
| --- | --- | --- |
| **A. Pertahankan Eidesoul** | kirim nama yang ada | Ditolak: *soul* menyiratkan kesadaran; README akan terus menjelaskan apa yang tidak diklaim |
| **B. MonoHarness** | bawa kategori ke nama | Ditolak: *harness* jargon bagi pengguna dan mengulang tipe runtime di nama produk |
| **C. MonoAgent** | satu agent | Ditolak: bertabrakan dengan produk `mono-agent` yang ada dan masuk bentuk turunan `<prefix>Agent` |
| **D. MonoAssistant** | satu assistant | Ditolak: mempersempit produk ke satu peran; assistant adalah satu dari assistant, companion, advisor, dan robot |
| **E. Agens** | Latin untuk *agent* | Dipertimbangkan: benar di level kategori, tetapi tidak membawa invariant "satu" |
| **F. Perstare** | Latin *to persist* | Ditolak: tepat secara makna tetapi tidak legibel bagi pengguna; kategorinya hilang dari nama |
| **G. Synecheia** | Yunani *continuity* | Ditolak: sulit dieja dan diucapkan |
| **H. Noctum** | malam | Ditolak: melenceng dari konsep; menamai malam, bukan persistensi |
| **I. self-continuity** | konstruk psikologi | Ditolak: menyiratkan self dan kesadaran; itu properti, bukan tipe tool |
| **J. persistent continuity** | persistensi plus kontinuitas | Ditolak: redundan; kontinuitas sudah berarti bertahan lintas waktu |
| **K. identity-continuity** | compound bertanda hubung | Ditolak: modifier bertanda hubung ditolak demi keterbacaan |
| **L. MonoAgens** | satu agent | **Dipilih** |

## 4. Keputusan

Produk dan tipenya dinamai sebagai berikut:

- **Nama produk:** MonoAgens.
- **Kategori:** agent.
- **Tipe runtime:** persistent mono agent harness.
- **Tagline:** *persistent mono agent, always remembering, always learning, the same person wherever it lives.*
- **Lapisan:** MonoAgens adalah agent; persistent mono agent harness adalah runtime-nya. MonoAgens bukan harness.

Nama ini satu kata di copy produk dan dua morfem di glosarium:

| Morfem | Arti | Yang dikesampingkan |
| --- | --- | --- |
| *mono* | satu identitas, satu writer, satu store | bukan satu tubuh, bukan satu proses, bukan satu replika |
| *agens* | Latin *one who acts*; akar kata *agent* | bukan peran assistant saja; bukan harness |

*Persistent* menamai klaim di tipe, bukan di nama produk: state bertahan lintas sesi, pergantian model, dan embodiment. *Continuity* tetap menjadi bentuk pendek tipe runtime dan properti yang dijaga harness.

Tagline adalah copy produk. *Person* di sana menamai identitas yang sama, bukan personhood, dan bukan kesadaran; README menyatakan ini di naming note.

## 5. Cakupan rename

Rename adalah cutover bersih. ADR ini mencatat cakupannya; ia tidak mengeksekusinya.

| Permukaan | Dari | Ke |
| --- | --- | --- |
| Paket Python | `monoagens/` | `monoagens/` |
| Root import | `monoagens.*` | `monoagens.*` |
| Nama distribusi | `monoagens` | `monoagens` |
| CLI | `monoagens` | `monoagens` |
| Repository | `github.com/inimuqsith/Eidesoul` | `github.com/AMQStudio/MonoAgens` |
| Direktori state | `.monoagens/` | `.monoagens/` |
| Prefix environment | `MONOAGENS_*` | `MONOAGENS_*` |
| Wordmark | `MONOAGENS` | `MONOAGENS` |
| README dan README.id | Eidesoul | MonoAgens |
| AGENTS, TASK, LOG | Eidesoul | MonoAgens |
| Referensi kode ADR | `monoagens/...` | `monoagens/...` |
| Image dan file Docker | `monoagens` | `monoagens` |
| Lockfile | saat ini | dibuat ulang setelah `pyproject.toml` |

Yang **tidak** berubah:

- `soul.yaml` dan dokumen `soul`: keduanya menamai konsep internal, bukan produk.
- Section konfigurasi `engine`, `gateways`, dan `roles`.
- Nama gateway, skema database, nama tabel, dan identifier migrasi.
- Keputusan ADR historis: prosanya mempertahankan nama produk lama; hanya referensi kode yang diperbarui agar tetap resolve setelah pemindahan.

Urutan cutover:

1. `pyproject.toml`: `name`, `[project.scripts]`, `[tool.setuptools.packages.find]`, URL proyek.
2. `git mv monoagens monoagens`; tulis ulang import di engine, gateways, admin, providers, tests, scripts, dan `migrations/env.py`.
3. Path runtime: `.monoagens/` ke `.monoagens/`, prefix environment, default gateway.
4. Branding: `admin/brand.py`, `admin/logo_art.py`, alt text `assets/logo.png`, dan assertion lockup di `tests/test_init.py`.
5. Docs: `README.md`, `README.id.md`, `AGENTS.md`, `TASK.md`, `LOG.md`, dan referensi kode ADR.
6. Rename repository, badge, dan tautan.
7. `uv lock`; lalu gate di bagian 6.

Tanpa alias, tanpa paket shim, tanpa entry point kedua, dan tanpa fallback environment variable. Status pra-rilis membuat breaking change ini dapat diterima.

## 6. Konfigurasi

Tidak ada config key yang ditambahkan atau di-rename oleh keputusan ini. Kontrak penamaannya:

| Istilah | Nilai |
| --- | --- |
| Produk | MonoAgens |
| Kategori | agent |
| Tipe runtime | persistent mono agent harness |
| Tipe runtime pendek | continuity harness |
| Tagline | *persistent mono agent, always remembering, always learning, the same person wherever it lives.* |
| Paket dan CLI | `monoagens` |
| Direktori state | `.monoagens/` |
| Prefix environment | `MONOAGENS_` |

Key `soul.yaml`, dokumen `soul`, dan section `engine` mempertahankan namanya.

## 7. Metrik dan gate

| Metrik | Target |
| --- | ---: |
| Kemunculan `monoagens` setelah cutover | 0 di luar prosa ADR historis |
| Import rusak di engine, gateways, admin, providers, tests, scripts, migrations | 0 |
| `pytest -q` | lolos |
| `ruff check` | lolos |
| `python -P scripts/types.py --check` | lolos |
| `python scripts/adr.py --check` | lolos |
| `python scripts/adr.py --translate-check` | lolos |
| `python scripts/selfcheck.py --check` | lolos |
| `monoagens selftest` | lolos |
| `uv sync --locked` | lolos |
| Nama terpakai (`monoagens` di PyPI, repository di GitHub) | diverifikasi sebelum cutover |

## 8. Kriteria penerimaan

- [x] `monoagens` ter-import dan `pytest -q` lolos.
- [x] `monoagens selftest` berjalan di store nyata.
- [x] `README.md` dan `README.id.md` memakai MonoAgens, tagline, dan naming note.
- [x] `AGENTS.md`, `TASK.md`, dan `LOG.md` memakai MonoAgens.
- [x] Referensi kode ADR resolve ke path `monoagens/...`.
- [x] Wordmark dan assertion `tests/test_init.py` memakai MONOAGENS.
- [x] `uv.lock` dibuat ulang dan `uv sync --locked` lolos.
- [x] `grep -R monoagens` hanya mengembalikan prosa ADR historis.
- [x] Repository, badge, dan URL proyek menunjuk MonoAgens.
- [x] Tidak ada paket alias, shim, atau CLI ganda.

## 9. Risiko dan mitigasi

| Risiko | Mitigasi |
| --- | --- |
| Rename parsial merusak import | Satu branch cutover; test dijalankan sebelum repository di-rename |
| Referensi basi bertahan di docs atau scripts | Gate `grep -R monoagens` dan check ADR |
| Nama PyPI atau GitHub sudah terpakai | Verifikasi keduanya sebelum cutover; fallback ke repository ber-scope owner bila perlu |
| Wordmark ANSI dihasilkan salah | Perbarui `admin/logo_art.py` dengan baris baru dan assert di `tests/test_init.py` |
| ADR historis menunjuk path lama | Perbarui referensi kode saja; prosa keputusan tidak diubah |
| State `.monoagens/` terputus | Dokumentasikan `mv .monoagens .monoagens` sekali di catatan cutover; tanpa fallback runtime |
| Pengguna CLI terpasang rusak | Pra-rilis; breaking change diterima dan dicatat di `LOG.md` |

## 10. Interaksi dengan ADR lain

- **ADR-0009**: ADR ini tinggal di root ADR home; salinan Indonesianya di `adr/id/`.
- **ADR-0027**: image distribusi dan entry point-nya memakai nama baru; extra dan aturan kapabilitas default-off tidak berubah.
- **ADR-0031**: pohon paket di-rename; batas modul dan cap 800 baris tidak berpindah.
- **ADR-0033**: rename tidak memasukkan kosakata transport, vendor, atau budaya ke engine.
- **ADR-0039**: wordmark setup dan assertion lockup berubah; wizard, langkah location, dan penanganan secret tidak.

## 11. Referensi kode

- `pyproject.toml` — nama distribusi, scripts, package discovery, dan URL proyek.
- `monoagens/__main__.py` — dispatch CLI dan entry point yang di-rename.
- `monoagens/config.py` — path host dan direktori state.
- `monoagens/admin/brand.py` — wordmark dan tagline.
- `monoagens/admin/logo_art.py` — baris half-block yang dihasilkan.
- `monoagens/admin/init.py` — default yang menyebut direktori state.
- `migrations/env.py` — import paket di environment Alembic.
- `tests/` — import dan assertion wordmark.
- `scripts/` — import dan konstanta path.
- `README.md`, `README.id.md`, `AGENTS.md`, `TASK.md`, `LOG.md` — nama produk dan tautan.
- `Dockerfile`, `uv.lock` — entry point image dan lockfile yang dibuat ulang.

---

*ADR ini tinggal di `docs/v0.1.0/adr/0040-monoagens-naming.md`.*
