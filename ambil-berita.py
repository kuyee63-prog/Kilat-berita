#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot Pengumpul Berita KabarKilat
----------------------------------
Mengambil umpan RSS resmi media-media Indonesia, menggabungkan per kategori,
lalu menulis hasilnya ke berita.json. Dijalankan otomatis oleh GitHub Actions
tiap 30 menit di repo Kilat-berita. Hanya memakai library bawaan Python.

Aturan aman: hanya judul + cuplikan + foto + tautan ke sumber asli.
Satu umpan gagal tidak boleh menggagalkan umpan lain; kategori yang gagal
total memakai isi berita.json sebelumnya (kalau ada).
"""

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

GN = "https://news.google.com/rss/search?q="
GNP = "&hl=id&gl=ID&ceid=ID:id"

# (kategori, nama sumber, alamat umpan)
UMPAN = [
    ("nasional", "Antara", "https://www.antaranews.com/rss/terkini"),
    ("nasional", "Detik", "https://rss.detik.com/index.php/detikcom"),
    ("nasional", "Google News", GN + "berita+nasional+indonesia" + GNP),
    ("ekonomi", "Antara", "https://www.antaranews.com/rss/ekonomi"),
    ("ekonomi", "CNBC Indonesia", "https://www.cnbcindonesia.com/rss"),
    ("ekonomi", "Detik Finance", "https://finance.detik.com/rss"),
    ("bola", "Antara", "https://www.antaranews.com/rss/sepakbola"),
    ("bola", "Google News", GN + "sepak+bola+timnas+indonesia" + GNP),
    ("bola", "Google News", GN + "liga+inggris+OR+liga+champions" + GNP),
    ("teknologi", "Antara", "https://www.antaranews.com/rss/tekno"),
    ("teknologi", "Detik Inet", "https://inet.detik.com/rss"),
    ("teknologi", "Google News", GN + "teknologi+gadget+AI" + GNP),
    ("hiburan", "Antara", "https://www.antaranews.com/rss/hiburan"),
    ("hiburan", "Google News", GN + "selebriti+film+musik+indonesia" + GNP),
    ("internasional", "Antara", "https://www.antaranews.com/rss/dunia"),
    ("internasional", "Google News", GN + "berita+dunia+internasional" + GNP),
    ("daerah", "Antara Banten", "https://banten.antaranews.com/rss/terkini"),
    ("daerah", "Google News", GN + "Banten+Serang" + GNP),
    ("daerah", "Google News", GN + "Lampung" + GNP),
]

NS = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}

MAKS_PER_UMPAN = 12
MAKS_PER_KATEGORI = 40
BERKAS_KELUARAN = "berita.json"


def bersih_teks(teks):
    """Buang tag HTML dari cuplikan, rapikan spasi."""
    if not teks:
        return ""
    teks = re.sub(r"<[^>]+>", " ", teks)
    teks = unescape(teks)
    return re.sub(r"\s+", " ", teks).strip()


def gambar_dari_html(teks):
    if not teks:
        return ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', teks, re.I)
    return m.group(1) if m else ""


def parse_tanggal(nilai):
    """Ubah tanggal RSS (RFC822) / Atom (ISO) menjadi epoch milidetik."""
    if not nilai:
        return 0
    nilai = nilai.strip()
    try:
        dt = parsedate_to_datetime(nilai)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(nilai.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def teks_elemen(induk, nama, ns=None):
    e = induk.find(nama, ns) if ns else induk.find(nama)
    if e is not None and e.text:
        return e.text.strip()
    return ""


def gambar_dari_item(item):
    for jalur in ("media:content", "media:thumbnail"):
        e = item.find(jalur, NS)
        if e is not None and e.get("url"):
            return e.get("url")
    e = item.find("enclosure")
    if e is not None and e.get("url") and "image" in (e.get("type") or "image"):
        return e.get("url")
    return ""


def bentuk_item(judul_mentah, deskripsi, foto, tautan, tanggal, sumber_asal, kategori):
    judul = bersih_teks(judul_mentah)
    sumber = sumber_asal
    # Google News menempel nama media di akhir judul: "Judul - Nama Media"
    if sumber_asal == "Google News":
        potong = judul.rfind(" - ")
        if potong >= 10:
            sumber = judul[potong + 3:]
            judul = judul[:potong]
    if not judul or not tautan:
        return None
    return {
        "k": kategori,
        "s": sumber,
        "j": judul,
        "c": bersih_teks(deskripsi)[:190],
        "f": foto or gambar_dari_html(deskripsi) or "",
        "u": tautan.strip(),
        "t": tanggal or 0,
    }


def parse_umpan(xml_teks, sumber, kategori):
    """Ubah teks XML RSS/Atom menjadi daftar berita. Murni — bisa diuji offline."""
    hasil = []
    akar = ET.fromstring(xml_teks)
    items = akar.findall(".//item")
    if not items:  # Atom
        items = akar.findall(".//atom:entry", NS)
    for item in items[:MAKS_PER_UMPAN]:
        judul = teks_elemen(item, "title") or teks_elemen(item, "atom:title", NS)
        tautan = teks_elemen(item, "link") or teks_elemen(item, "atom:id", NS)
        if not tautan:
            e = item.find("atom:link", NS)
            if e is not None:
                tautan = e.get("href") or ""
        deskripsi = (
            teks_elemen(item, "description")
            or teks_elemen(item, "content:encoded", NS)
            or teks_elemen(item, "atom:summary", NS)
            or teks_elemen(item, "atom:content", NS)
        )
        tanggal = parse_tanggal(
            teks_elemen(item, "pubDate")
            or teks_elemen(item, "atom:published", NS)
            or teks_elemen(item, "atom:updated", NS)
        )
        foto = gambar_dari_item(item)
        item_jadi = bentuk_item(judul, deskripsi, foto, tautan, tanggal, sumber, kategori)
        if item_jadi:
            hasil.append(item_jadi)
    return hasil


def ambil_dari_internet(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; KabarKilatRobot/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def gabung_unik(daftar):
    lihat = set()
    hasil = []
    for b in daftar:
        kunci = b["u"].split("?")[0]
        if kunci in lihat:
            continue
        lihat.add(kunci)
        hasil.append(b)
    hasil.sort(key=lambda b: b["t"], reverse=True)
    return hasil[:MAKS_PER_KATEGORI]


def main():
    per_kategori = {}
    gagal = []
    for kategori, sumber, url in UMPAN:
        try:
            xml_teks = ambil_dari_internet(url)
            berita = parse_umpan(xml_teks, sumber, kategori)
            per_kategori.setdefault(kategori, []).extend(berita)
            print(f"OK   {kategori:14s} {sumber:16s} {len(berita)} berita")
        except Exception as e:
            gagal.append(f"{sumber} ({kategori}): {e}")
            print(f"GAGAL {kategori:14s} {sumber:16s} {e}", file=sys.stderr)

    # Kategori yang kosong total: pakai isi lama bila ada
    lama = {}
    if os.path.exists(BERKAS_KELUARAN):
        try:
            with open(BERKAS_KELUARAN, encoding="utf-8") as f:
                lama = json.load(f).get("kategori", {})
        except Exception:
            lama = {}

    keluaran_kategori = {}
    for kategori in {k for k, _, _ in UMPAN}:
        gabungan = gabung_unik(per_kategori.get(kategori, []))
        if not gabungan and lama.get(kategori):
            gabungan = lama[kategori]
            print(f"PAKAI LAMA {kategori}: {len(gabungan)} berita")
        keluaran_kategori[kategori] = gabungan

    total = sum(len(v) for v in keluaran_kategori.values())
    data = {
        "diperbarui": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "kategori": keluaran_kategori,
    }
    with open(BERKAS_KELUARAN, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"Selesai: {total} berita dari {len(UMPAN) - len(gagal)}/{len(UMPAN)} umpan.")
    if total == 0:
        print("PERINGATAN: tidak ada berita sama sekali.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
